"""
Lead finder — scour the web for small-business websites worth pitching to.

We use DuckDuckGo's HTML endpoint (https://duckduckgo.com/html/) because:
  - no API key
  - no JS rendering required
  - works behind plain `requests`
  - rate limits are forgiving for a few queries at a time

For India-focused work we:
  - bias queries toward `site:.in` and append the city/region
  - skip well-known aggregator/directory domains (JustDial, Sulekha,
    IndiaMART, Yelp, Google Maps, Facebook pages, etc.)
  - try to pick the homepage (strip /about, /contact, deep paths) for each hit

The output is a list of partial-lead dicts ready to be enriched by `scrape()`.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup


# domains we drop (aggregators, directories, social, marketplaces, big media)
SKIP_DOMAINS = {
    # directories / aggregators
    "justdial.com", "sulekha.com", "indiamart.com", "tradeindia.com",
    "yelp.com", "yellowpages.in", "askme.com", "olx.in",
    "urbancompany.com", "urbanclap.com", "bookmyshow.com",
    # search engines & maps
    "google.com", "google.co.in", "maps.google.com",
    "bing.com", "duckduckgo.com", "yahoo.com",
    # social
    "facebook.com", "m.facebook.com", "instagram.com", "twitter.com",
    "x.com", "linkedin.com", "youtube.com", "pinterest.com",
    "tiktok.com", "snapchat.com", "telegram.org",
    # travel & food aggregators
    "tripadvisor.com", "tripadvisor.in", "zomato.com", "swiggy.com",
    "makemytrip.com", "goibibo.com", "booking.com", "airbnb.com",
    "agoda.com", "expedia.com",
    # marketplaces
    "amazon.com", "amazon.in", "flipkart.com", "myntra.com", "ajio.com",
    "snapdeal.com", "shopify.com", "etsy.com",
    # platform / DIY hosts (these are CMS landing pages, not businesses)
    "wikipedia.org", "quora.com", "reddit.com", "medium.com",
    "blogspot.com", "wordpress.com", "wix.com", "weebly.com",
    "carrd.co", "linktr.ee", "github.com", "stackoverflow.com",
    "behance.net", "dribbble.com",
    # large news / media — never a pitch target
    "timesofindia.indiatimes.com", "indiatimes.com", "indianexpress.com",
    "thehindu.com", "ndtv.com", "news18.com", "hindustantimes.com",
    "livemint.com", "businesstoday.in", "economictimes.indiatimes.com",
    "moneycontrol.com", "scroll.in", "thequint.com", "firstpost.com",
    "healthline.com", "everydayhealth.com", "webmd.com", "mayoclinic.org",
    "fitness.com", "menshealth.com", "womenshealthmag.com",
    "harvard.edu", "stanford.edu", "mit.edu",
    "forbes.com", "nytimes.com", "bbc.com", "cnn.com", "wsj.com",
    "vogue.in", "vogue.com", "gq.com", "elle.com",
    "fitindia.gov.in",  # gov campaign, not a business
    # dictionaries / reference (false-positive magnets)
    "merriam-webster.com", "dictionary.cambridge.org", "collinsdictionary.com",
    "britannica.com", "wordreference.com", "thefreedictionary.com",
    "vocabulary.com", "dictionary.com", "thesaurus.com", "lexico.com",
    "yappe.in",  # itself an aggregator
    # big-box retail
    "bestbuy.com", "costco.com", "walmart.com", "target.com",
    "ikea.com", "homedepot.com", "lowes.com",
}

# entire TLD families to skip
SKIP_TLD_SUFFIXES = (
    ".gov", ".gov.in", ".gov.uk", ".mil",
    ".edu", ".edu.in", ".ac.in", ".ac.uk",
)

# city/region modifiers we'll allow as the trailing token
INDIA_TOKENS_DEFAULT = "india"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
    "Gecko/20100101 Firefox/127.0"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

@dataclass
class FoundLead:
    website: str
    business_name: str
    description: str
    source_query: str

    def to_dict(self) -> dict:
        return {
            "website":       self.website,
            "business_name": self.business_name,
            "description":   self.description,
            "source_query":  self.source_query,
            "status":        "new",
        }


@dataclass
class FindResult:
    leads: list[FoundLead] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    errors:     list[str]  = field(default_factory=list)


# ---------------------------------------------------------------------------
# query builders
# ---------------------------------------------------------------------------

def build_queries(category: str, city: str = "", country: str = "in") -> list[str]:
    """
    Build a handful of varied queries so we sweep more SMB sites than a
    single search would return.

    We *don't* use the `site:.in` search operator — Bing/DDG often return
    zero organic results for queries that include it. Instead we bias by
    geography in the query text and filter by TLD on the result side
    (see `_passes_country_filter`).
    """
    cat   = category.strip()
    city  = (city or "").strip()
    tld   = country.lower().strip()
    place = city if city else (INDIA_TOKENS_DEFAULT if tld == "in" else "")

    geo = f" {place}" if place else ""

    # We deliberately avoid leading the query with "best" — search engines
    # treat the word ambiguously and surface dictionary / electronics-store
    # pages. We also avoid bare 1-word queries; specificity = better SMB hits.
    # Three carefully-chosen query shapes give variety without triggering
    # search-engine rate limits. Order matters — we run them in this order
    # and stop early when max_results is reached.
    seeds = []
    if place:
        seeds += [
            f"{cat} in {place}",
            f"{cat} {place} contact",
            f"local {cat} {place}",
        ]
    else:
        seeds += [
            f"{cat} india",
            f"{cat} india contact",
            f"local {cat} india",
        ]
    # dedupe, preserve order
    seen, out = set(), []
    for s in seeds:
        s = s.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower()); out.append(s)
    return out


# ---------------------------------------------------------------------------
# DDG HTML scrape
# ---------------------------------------------------------------------------

DDG_HTML = "https://html.duckduckgo.com/html/"
DDG_LITE = "https://lite.duckduckgo.com/lite/"
BING     = "https://www.bing.com/search"


def _clean_ddg_redirect(href: str) -> str:
    """
    DDG wraps result links in something like:
        //duckduckgo.com/l/?uddg=<percent-encoded>&rut=...
    Unwrap to the real URL.
    """
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    p = urlparse(href)
    if "duckduckgo.com" in p.netloc and p.path.startswith("/l/"):
        q = parse_qs(p.query)
        real = q.get("uddg", [""])[0]
        if real:
            return unquote(real)
    return href


def _clean_bing_redirect(href: str) -> str:
    """Bing sometimes wraps with /click?u=... — unwrap when it does."""
    if not href:
        return ""
    p = urlparse(href)
    if "bing.com" in p.netloc and "click" in p.path:
        q = parse_qs(p.query)
        u = q.get("u", [""])[0]
        if u:
            return unquote(u)
    return href


def _homepage(url: str) -> str:
    """Strip path/query/fragment — we want the site root for scraping."""
    try:
        p = urlparse(url)
        if not p.scheme or not p.netloc:
            return ""
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _bare_domain(netloc: str) -> str:
    return netloc.lower().lstrip(".").removeprefix("www.")


def _is_skipped(netloc: str) -> bool:
    bare = _bare_domain(netloc)
    if not bare:
        return True
    for sd in SKIP_DOMAINS:
        if bare == sd or bare.endswith("." + sd):
            return True
    for suf in SKIP_TLD_SUFFIXES:
        if bare.endswith(suf):
            return True
    return False


def _passes_country_filter(netloc: str, country: str) -> bool:
    """
    If `country` is set (e.g. 'in'), keep only domains likely to be from
    that country. We accept the country ccTLD (.in, .co.in, .org.in, etc.)
    and also generic .com sites — most Indian SMBs use a .com domain, so
    a hard ccTLD-only filter would lose too many real prospects. Hard-rejects
    foreign ccTLDs we have no business pitching ("agency in India" pitch
    to a .de or .au site doesn't land).
    """
    if not country:
        return True
    bare = _bare_domain(netloc)
    if not bare:
        return False
    # foreign ccTLDs to reject (when country='in')
    foreign_ccs = {
        "us", "uk", "co.uk", "au", "com.au", "ca", "de", "fr", "it", "es",
        "nl", "ru", "cn", "jp", "kr", "br", "mx", "za", "ng", "ae", "sg",
        "my", "ph", "id", "tw", "hk", "pk", "bd", "lk", "np",
    }
    if country == "in":
        # accept obvious-Indian
        if bare.endswith(".in"):
            return True
        # reject obvious-foreign
        for fc in foreign_ccs:
            if bare.endswith("." + fc):
                return False
        # gTLDs (.com .org .net .biz .co .io) — keep, may still be Indian
        return True
    # default: only accept the country TLD
    return bare.endswith("." + country)


def _search_web(query: str, *, timeout: int = 20) -> list[dict]:
    """
    Returns list of {"url": homepage, "title": ..., "snippet": ...}.
    Tries DDG (HTML, then Lite) and falls back to Bing.
    Raises only if every engine fails.
    """
    errors: list[str] = []

    # 1. DDG HTML — try GET then POST (POST is what their form uses but
    # GET often slips past their soft anti-bot 202 gate).
    for endpoint in (DDG_HTML, DDG_LITE):
        for method in ("GET", "POST"):
            try:
                if method == "GET":
                    r = requests.get(
                        endpoint, params={"q": query, "kl": "in-en"},
                        headers=HEADERS, timeout=timeout, allow_redirects=True,
                    )
                else:
                    r = requests.post(
                        endpoint, data={"q": query, "kl": "in-en"},
                        headers=HEADERS, timeout=timeout, allow_redirects=True,
                    )
                if r.status_code != 200:
                    errors.append(f"{endpoint} {method}: HTTP {r.status_code}")
                    continue
                results = _parse_ddg_html(r.text)
                if results:
                    return results
                errors.append(f"{endpoint} {method}: 0 parsed results")
            except requests.RequestException as e:
                errors.append(f"{endpoint} {method}: {e}")

    # 2. Bing fallback
    try:
        r = requests.get(
            BING, params={"q": query, "cc": "IN", "setlang": "en-IN"},
            headers=HEADERS, timeout=timeout, allow_redirects=True,
        )
        if r.status_code == 200:
            results = _parse_bing_html(r.text)
            if results:
                return results
            errors.append("bing: 0 parsed results")
        else:
            errors.append(f"bing: HTTP {r.status_code}")
    except requests.RequestException as e:
        errors.append(f"bing: {e}")

    raise RuntimeError(" | ".join(errors) or "no results")


def _parse_bing_html(html: str) -> list[dict]:
    """
    Bing's modern markup keeps organic results in li.b_algo, but the visible
    link is often inside an h2 > a OR is reconstructable from the breadcrumb
    <cite> tag (which shows e.g. 'https://www.fitness.com' or
    'https://www.healthline.com › fitness').
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for li in soup.select("li.b_algo"):
        # title — try h2, fall back to first text-y anchor
        title = ""
        h2 = li.find("h2")
        if h2:
            title = h2.get_text(" ", strip=True)
        if not title:
            a = li.find("a")
            if a:
                title = a.get_text(" ", strip=True)

        # URL — prefer the cite tag's first chunk (Bing renders breadcrumbs
        # with the U+203A ›  separator). The cite text often contains
        # display whitespace ("https:// www.example.com") that needs squashing.
        href = ""
        cite = li.find("cite")
        if cite:
            cite_text = cite.get_text("", strip=True)  # no separator at all
            cite_text = re.sub(r"\s+", "", cite_text)
            first = re.split(r"[›>]", cite_text, maxsplit=1)[0]
            if first.startswith("http"):
                href = first
            elif first:
                href = "https://" + first.lstrip("/")
        # fall back: any non-bing anchor href
        if not href:
            for a in li.find_all("a", href=True):
                cleaned = _clean_bing_redirect(a["href"])
                if cleaned.startswith("http") and "bing.com" not in cleaned:
                    href = cleaned
                    break

        # snippet
        snippet = ""
        cap = li.select_one(".b_caption p, .b_snippet, .b_paractl, p")
        if cap:
            snippet = cap.get_text(" ", strip=True)

        if href:
            out.append({"url": href, "title": title, "snippet": snippet})
    return out


def _parse_ddg_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []

    # html endpoint:  div.result__body a.result__a + a.result__snippet
    for body in soup.select("div.result"):
        a = body.select_one("a.result__a")
        snip = body.select_one("a.result__snippet, .result__snippet")
        if not a:
            continue
        href = _clean_ddg_redirect(a.get("href", ""))
        title = a.get_text(" ", strip=True)
        snippet = snip.get_text(" ", strip=True) if snip else ""
        if href:
            out.append({"url": href, "title": title, "snippet": snippet})

    if out:
        return out

    # lite endpoint:  a.result-link + td.result-snippet
    for tr in soup.find_all("tr"):
        a = tr.find("a", class_="result-link")
        if not a:
            continue
        href = _clean_ddg_redirect(a.get("href", ""))
        title = a.get_text(" ", strip=True)
        snip_td = tr.find_next("td", class_="result-snippet")
        snippet = snip_td.get_text(" ", strip=True) if snip_td else ""
        if href:
            out.append({"url": href, "title": title, "snippet": snippet})

    return out


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def find_leads(
    category: str,
    city: str = "",
    *,
    country: str = "in",
    max_results: int = 15,
    existing_websites: Optional[set[str]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> FindResult:
    """
    Run a small bouquet of searches and return a deduped list of partial
    leads with the homepage URL of each candidate.

    Pass `existing_websites` (lowercased homepages) so we don't re-suggest
    leads you've already imported.
    """
    existing = {_homepage(u).lower().rstrip("/")
                for u in (existing_websites or []) if u}
    seen: set[str] = set()
    out: list[FoundLead] = []
    res = FindResult()

    queries = build_queries(category, city, country=country)

    def emit(msg: str):
        if progress:
            try: progress(msg)
            except Exception: pass

    for q in queries:
        if len(out) >= max_results:
            break
        res.queries_run.append(q)
        emit(f"Searching: {q}")
        try:
            hits = _search_web(q)
        except Exception as e:
            res.errors.append(f"{q!r}: {e}")
            continue

        for h in hits:
            home = _homepage(h["url"])
            if not home:
                continue
            netloc = urlparse(home).netloc
            if _is_skipped(netloc):
                continue
            if not _passes_country_filter(netloc, country):
                continue
            key = home.lower().rstrip("/")
            if key in seen or key in existing:
                continue
            seen.add(key)

            name = _name_from_title(h.get("title") or "", netloc)
            out.append(FoundLead(
                website=home,
                business_name=name,
                description=(h.get("snippet") or "")[:300],
                source_query=q,
            ))
            if len(out) >= max_results:
                break

        # be polite to the search engines — bigger jitter helps avoid
        # the soft-anti-bot gate that triggers after a burst of requests
        time.sleep(1.6)

    res.leads = out
    emit(f"Done. {len(out)} candidate site(s).")
    return res


# title → business-name guess
_TITLE_SEP_RE = re.compile(r"\s*[\|\-–—:·•]\s*")


def _name_from_title(title: str, netloc: str) -> str:
    if not title:
        return _bare_domain(netloc).split(".")[0].title()
    parts = _TITLE_SEP_RE.split(title)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return title.strip()
    # heuristic: the shortest "word-y" chunk is usually the business name
    candidate = min(parts, key=len)
    # but if the first chunk is short already, prefer it (more common pattern)
    if len(parts[0]) <= 40:
        candidate = parts[0]
    return candidate[:80]


# ---------------------------------------------------------------------------
# threaded wrapper (for the UI)
# ---------------------------------------------------------------------------

class Finder:
    """Run find_leads on a worker thread without freezing the UI."""

    def __init__(self, category: str, city: str = "", *,
                 country: str = "in", max_results: int = 15,
                 existing_websites: Optional[set[str]] = None,
                 progress: Optional[Callable[[str], None]] = None):
        self.category = category
        self.city = city
        self.country = country
        self.max_results = max_results
        self.existing_websites = existing_websites or set()
        self.progress = progress
        self._thread: Optional[threading.Thread] = None
        self.result: Optional[FindResult] = None

    def start(self, on_done: Callable[[FindResult], None]):
        def run():
            try:
                self.result = find_leads(
                    self.category, self.city,
                    country=self.country,
                    max_results=self.max_results,
                    existing_websites=self.existing_websites,
                    progress=self.progress,
                )
            except Exception as e:
                self.result = FindResult(errors=[str(e)])
            on_done(self.result)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self._thread = t
