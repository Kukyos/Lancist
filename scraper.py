"""
Website analysis for Oryn — raw signal detection.

Fetches the homepage (and a few secondary pages: contact, shop, /robots.txt,
/sitemap.xml when relevant) and extracts every signal we can think of that
would inform a cold pitch: tech stack, SEO basics, conversion tooling, trust
signals, engagement features, and content snippets used later for business
categorisation.

Detection is signature-based on the rendered HTML + response headers. Pure
client-side SPAs that inject everything via JS may underreport — the framework
signature usually still catches it, but a chat widget injected at runtime
might be missed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 15

# ---------- Signature tables ----------

WHATSAPP_PATTERNS = [
    r"wa\.me/", r"api\.whatsapp\.com/send", r"web\.whatsapp\.com/send",
    r"whatsapp://send", r"chat\.whatsapp\.com/",
]

LIVE_CHAT_SIGNATURES = {
    "Intercom":      ["intercom.io", "intercomcdn.com", "widget.intercom.io"],
    "Drift":         ["drift.com", "driftt.com", "js.driftt.com"],
    "Tawk.to":       ["tawk.to", "embed.tawk.to"],
    "Crisp":         ["crisp.chat", "client.crisp.chat"],
    "HubSpot Chat":  ["js.hs-scripts.com", "hubspot.com/conversations"],
    "Zendesk":       ["zdassets.com", "zendesk.com/embeddable"],
    "LiveChat":      ["livechatinc.com", "cdn.livechatinc.com"],
    "Freshchat":     ["freshchat.com", "wchat.freshchat.com"],
    "Olark":         ["olark.com"],
    "Smartsupp":     ["smartsupp.com"],
    "JivoChat":      ["jivochat.com", "jivosite.com", "code.jivosite.com"],
    "Chatra":        ["chatra.io"],
    "Userlike":      ["userlike.com"],
    "Tidio":         ["tidio.co", "tidiochat.com", "code.tidio.co"],
    "Purechat":      ["purechat.com"],
    "SnapEngage":    ["snapengage.com"],
    "LivePerson":    ["liveperson.net"],
    "HelpCrunch":    ["helpcrunch.com"],
    "FB Customer Chat": ["fb-customerchat"],
    "Zopim":         ["zopim.com"],
}

ECOMMERCE_SIGNATURES = {
    "Shopify":      ["cdn.shopify.com", "shopify.theme", "shopify.com/s/"],
    "WooCommerce":  ["woocommerce", "wc-block-", "wp-content/plugins/woocommerce"],
    "Magento":      ["mage.cookies", "magento", "x-magento"],
    "BigCommerce":  ["bigcommerce.com", "stencil-utils"],
    "Wix Stores":   ["wixstores", "stores.wix"],
    "Squarespace Commerce": ["squarespace-commerce"],
    "Ecwid":        ["app.ecwid.com", "ecwid.com"],
    "Snipcart":     ["snipcart.com"],
    "Stripe Checkout": ["checkout.stripe.com", "js.stripe.com"],
    "PayPal Buttons":  ["paypal.com/sdk/js", "paypalobjects.com"],
    "Razorpay":     ["checkout.razorpay.com"],
    "Square":       ["squareup.com", "web.squarecdn.com"],
}

MODERN_FRAMEWORK_SIGNATURES = {
    "Next.js":   ["__NEXT_DATA__", "/_next/static/"],
    "Nuxt":      ["__NUXT__", "/_nuxt/"],
    "Gatsby":    ["___gatsby", "/page-data/", "gatsby-"],
    "Astro":     ["astro-island", "astro:"],
    "Remix":     ["__remixContext"],
    "SvelteKit": ["/_app/immutable/"],
    "React":     ["react-dom", "data-reactroot", "data-react-"],
    "Vue":       ["__vue_app__", "data-v-app"],
    "Angular":   ["ng-version=", "ng-cloak"],
    "Webflow":   ["wf-page-id", "webflow.com", "data-wf-"],
    "Framer":    ["framerusercontent.com", "framer.com"],
    "Wix":       ["static.wixstatic.com", "wix.com"],
    "Squarespace": ["static1.squarespace.com", "squarespace.com"],
    "WordPress": ["/wp-content/", "/wp-includes/"],
}

ANIMATION_SIGNATURES = {
    "GSAP":          ["gsap.min.js", "gsap@", "/gsap/"],
    "Framer Motion": ["framer-motion"],
    "AOS":           ["aos.css", "aos.js", "data-aos"],
    "ScrollMagic":   ["scrollmagic"],
    "Lottie":        ["lottie-web", "lottiefiles", "lottieplayer"],
    "Three.js":      ["three.min.js", "three.module.js"],
    "Swiper":        ["swiper-bundle", "swiper.min.js", "swiper-container"],
    "Locomotive":    ["locomotive-scroll"],
    "Tailwind":      ["tailwindcss", "cdn.tailwindcss.com"],
}

FORM_EMBED_SIGNATURES = {
    "Typeform":  ["typeform.com"],
    "Calendly":  ["calendly.com"],
    "Jotform":   ["jotform.com"],
    "Google Forms": ["docs.google.com/forms"],
    "Tally":     ["tally.so"],
    "Airtable":  ["airtable.com/embed"],
    "HubSpot Forms": ["js.hsforms.net"],
    "Mailchimp Embedded": ["list-manage.com", "mailchimp.com"],
}

ANALYTICS_SIGNATURES = {
    "Google Analytics": ["google-analytics.com", "googletagmanager.com", "gtag(", "ga('create'"],
    "Meta Pixel":       ["connect.facebook.net", "fbq('init'"],
    "Hotjar":           ["static.hotjar.com", "hotjar.com"],
    "Mixpanel":         ["cdn.mxpnl.com"],
    "Plausible":        ["plausible.io/js"],
}

VIDEO_EMBED_SIGNATURES = ["youtube.com/embed", "youtu.be/", "player.vimeo.com", "wistia.com", "vidyard.com"]
MAPS_SIGNATURES = [
    "google.com/maps/embed", "maps.googleapis.com", "maps.google.com/embed",
    "maps.apple.com", "openstreetmap.org/export/embed", "mapbox.com/embed",
]
LOCATION_HEADING_RE = re.compile(
    r"\b(our location|find us|visit us|visit our|store location|our address|"
    r"our store|branches|locations|where to find us|come visit)\b",
    re.IGNORECASE,
)
STREET_PATTERNS = [
    re.compile(r"\b\d{1,5}\s+[A-Z][\w'\.\-]{2,}\s+(?:Street|St\.?|Road|Rd\.?|"
               r"Avenue|Ave\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|"
               r"Way|Court|Ct\.?|Highway|Hwy\.?)\b", re.I),
    re.compile(r"\b\d{1,4}[,\s]+[A-Z][\w\s\-\.]{2,40}\s+(?:Nagar|Layout|Colony|"
               r"Lane|Cross|Main\sRoad|Bazaar)\b", re.I),
]
POSTAL_PATTERNS = [
    re.compile(r"\b\d{6}\b"),                # India PIN
    re.compile(r"\b\d{5}(?:-\d{4})?\b"),     # US ZIP
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b"),  # UK
]
COOKIE_BANNER_SIGNATURES = ["cookiebot", "onetrust", "cookieconsent", "cookielaw"]
NEWSLETTER_KEYWORDS = ["subscribe", "newsletter", "stay updated", "join our mailing", "sign up for updates"]
TESTIMONIAL_SIGNATURES = [
    "testimonial", "what our clients say", "what our customers say",
    "client reviews", "google reviews", "trustpilot", "google-reviews",
]
SOCIAL_PATTERNS = {
    "Facebook":  re.compile(r"https?://(?:www\.|m\.)?facebook\.com/[^\s\"'<>]+", re.I),
    "Instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[^\s\"'<>]+", re.I),
    "Twitter/X": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[^\s\"'<>]+", re.I),
    "LinkedIn":  re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/[^\s\"'<>]+", re.I),
    "YouTube":   re.compile(r"https?://(?:www\.)?youtube\.com/(?:@|c/|channel/|user/)[^\s\"'<>]+", re.I),
    "TikTok":    re.compile(r"https?://(?:www\.)?tiktok\.com/@[^\s\"'<>]+", re.I),
    "Pinterest": re.compile(r"https?://(?:www\.)?pinterest\.[a-z.]+/[^\s\"'<>]+", re.I),
}

CONTACT_PATH_CANDIDATES = ["/contact", "/contact-us", "/contact.html", "/get-in-touch", "/reach-us"]
SHOP_PATH_CANDIDATES = ["/shop", "/store", "/products", "/collections", "/cart"]

CTA_VERBS = [
    "get started", "get a quote", "book now", "book a", "request a quote",
    "schedule", "sign up", "join now", "start free", "learn more",
    "contact us", "buy now", "shop now", "subscribe", "talk to us",
    "call now", "request demo", "try free", "claim your",
]


# ---------- Result type ----------

@dataclass
class ScrapeResult:
    url: str
    final_url: Optional[str] = None
    status: str = "ok"
    error: Optional[str] = None

    # metadata
    title: Optional[str] = None
    description: Optional[str] = None
    og_description: Optional[str] = None
    og_site_name: Optional[str] = None
    server: Optional[str] = None
    generator: Optional[str] = None
    text_sample: str = ""             # plain-text body excerpt, for categorisation

    # auto-extracted business info (for form autofill)
    extracted_business_name: Optional[str] = None
    extracted_emails: list = field(default_factory=list)
    extracted_phones: list = field(default_factory=list)
    extracted_addresses: list = field(default_factory=list)

    # tech / framework
    detected_frameworks: list = field(default_factory=list)
    detected_animations: list = field(default_factory=list)
    detected_chat_provider: Optional[str] = None
    detected_ecommerce_platform: Optional[str] = None
    detected_form_embeds: list = field(default_factory=list)
    detected_analytics: list = field(default_factory=list)

    # core Oryn-service capability flags
    has_whatsapp: bool = False
    has_live_chat: bool = False
    has_ecommerce: bool = False
    has_contact_form: bool = False
    has_mobile_responsive: bool = False
    has_modern_design: bool = False

    # SEO
    is_https: bool = False
    has_h1: bool = False
    has_meta_description: bool = False
    has_og_tags: bool = False
    has_twitter_card: bool = False
    has_structured_data: bool = False
    has_favicon: bool = False
    has_robots_txt: bool = False
    has_sitemap: bool = False

    # trust / contact
    has_click_to_call: bool = False
    has_email_link: bool = False
    has_address_or_map: bool = False
    has_testimonials: bool = False
    has_privacy_policy: bool = False
    has_about_page: bool = False
    social_links: list = field(default_factory=list)  # list of (platform, url)

    # engagement / content
    has_blog: bool = False
    has_faq: bool = False
    has_gallery: bool = False
    has_video_embed: bool = False
    has_newsletter_signup: bool = False
    has_cookie_banner: bool = False
    has_lazy_loading: bool = False
    cta_count: int = 0

    # quantitative
    images_total: int = 0
    images_with_alt: int = 0
    script_count: int = 0
    body_word_count: int = 0

    notes: dict = field(default_factory=dict)
    pages_checked: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# ---------- Helpers ----------

def _ensure_scheme(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def _fetch(url: str, timeout: int = TIMEOUT) -> Optional[requests.Response]:
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None


def _haystack(resp: requests.Response) -> str:
    parts = [resp.text or ""]
    for k, v in resp.headers.items():
        parts.append(f"{k}: {v}")
    return "\n".join(parts).lower()


def _find_first(haystack: str, mapping: dict[str, list[str]]) -> Optional[str]:
    for name, sigs in mapping.items():
        for s in sigs:
            if s.lower() in haystack:
                return name
    return None


def _find_all(haystack: str, mapping: dict[str, list[str]]) -> list[str]:
    found = []
    for name, sigs in mapping.items():
        for s in sigs:
            if s.lower() in haystack:
                found.append(name)
                break
    return found


def _any_in(haystack: str, needles: list[str]) -> bool:
    return any(n.lower() in haystack for n in needles)


# ---------- Detectors ----------

def _detect_whatsapp(haystack: str, soup: BeautifulSoup) -> bool:
    for pat in WHATSAPP_PATTERNS:
        if re.search(pat, haystack, re.IGNORECASE):
            return True
    for a in soup.find_all("a", href=True):
        if "whatsapp" in a["href"].lower():
            return True
    for el in soup.find_all(True, limit=2500):
        cls = " ".join(el.get("class") or []).lower()
        eid = (el.get("id") or "").lower()
        if "whatsapp" in cls or "whatsapp" in eid or "wa-button" in cls or "wa-chat" in cls:
            return True
    return False


def _detect_contact_form(soup: BeautifulSoup) -> tuple[bool, Optional[str]]:
    for form in soup.find_all("form"):
        inputs = form.find_all(["input", "textarea"])
        has_email = any(
            (i.get("type") == "email") or "email" in (i.get("name") or "").lower()
            for i in inputs
        )
        has_message = any(i.name == "textarea" for i in inputs)
        if (has_email and len(inputs) >= 2) or has_message:
            return True, "form"
    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("mailto:"):
            return True, "mailto"
    return False, None


def _detect_newsletter(soup: BeautifulSoup, haystack: str) -> bool:
    for form in soup.find_all("form"):
        inputs = form.find_all("input")
        has_email = any(
            (i.get("type") == "email") or "email" in (i.get("name") or "").lower()
            for i in inputs
        )
        text_nearby = (form.get_text() or "").lower() + " " + " ".join(
            (i.get("placeholder") or "") for i in inputs
        ).lower()
        if has_email and any(k in text_nearby for k in NEWSLETTER_KEYWORDS):
            return True
    return _any_in(haystack, NEWSLETTER_KEYWORDS) and ("type=\"email\"" in haystack or "type='email'" in haystack)


def _detect_social(html: str) -> list[tuple[str, str]]:
    found = []
    seen = set()
    for platform, pattern in SOCIAL_PATTERNS.items():
        for m in pattern.findall(html):
            base = m.split("?")[0].split("#")[0].rstrip("/")
            # filter share intents and bare domain links
            if "/sharer" in base or "/share" in base or base.lower().endswith(".com"):
                continue
            if (platform, base) in seen:
                continue
            seen.add((platform, base))
            found.append((platform, base))
            if len(found) >= 12:
                return found
    return found


def _detect_mobile_responsive(soup: BeautifulSoup, haystack: str) -> bool:
    vp = soup.find("meta", attrs={"name": re.compile("viewport", re.I)})
    if vp and "width=device-width" in (vp.get("content") or "").lower():
        return True
    if "@media" in haystack or "max-width:" in haystack:
        return True
    return False


def _detect_location(soup: BeautifulSoup, haystack: str,
                     extracted_addresses: list) -> bool:
    """
    True when the site clearly publishes a physical address — embedded map,
    JSON-LD address, <address> tag, microdata, a 'Locations' section, or a
    street-address text pattern next to a postal code.
    """
    if _any_in(haystack, MAPS_SIGNATURES):
        return True
    if extracted_addresses:                       # JSON-LD already gave us one
        return True
    addr_tag = soup.find("address")
    if addr_tag and addr_tag.get_text(strip=True):
        return True
    if soup.find(attrs={"itemprop": "address"}):
        return True
    if soup.find(attrs={"itemtype": re.compile("PostalAddress", re.I)}):
        return True
    # explicit "Locations" / "Find us" heading
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "section", "div"], limit=400):
        txt = (tag.get_text(" ", strip=True) or "")[:80]
        if txt and LOCATION_HEADING_RE.search(txt):
            return True
    # text + postal-code pattern co-occurring
    text = soup.get_text(" ", strip=True)
    if any(p.search(text) for p in STREET_PATTERNS):
        return True
    if any(p.search(text) for p in POSTAL_PATTERNS):
        # postal codes alone are weak — require a street/locality keyword nearby
        if re.search(r"\b(road|street|nagar|colony|avenue|lane|bazaar|cross|"
                     r"sector|block|near|opp\.?|opposite)\b", text, re.I):
            return True
    return False


def _detect_ecommerce_buttons(soup: BeautifulSoup) -> bool:
    for a in soup.find_all(["a", "button"]):
        txt = (a.get_text() or "").strip().lower()
        if txt in {"add to cart", "buy now", "shop now", "add to bag"} or "add to cart" in txt:
            return True
    return False


def _count_ctas(soup: BeautifulSoup) -> int:
    n = 0
    for el in soup.find_all(["a", "button"]):
        txt = (el.get_text() or "").strip().lower()
        if len(txt) > 80:
            continue
        if any(v in txt for v in CTA_VERBS):
            n += 1
    return n


def _text_sample(soup: BeautifulSoup, max_chars: int = 4000) -> tuple[str, int]:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ").strip())
    words = len(text.split())
    return text[:max_chars], words


def _link_targets_lower(soup: BeautifulSoup) -> list[str]:
    return [(a.get("href") or "").lower() for a in soup.find_all("a", href=True)]


# ---------- Main ----------

def scrape(url: str) -> ScrapeResult:
    url = _ensure_scheme(url)
    result = ScrapeResult(url=url)

    resp = _fetch(url)
    if resp is None or not resp.ok:
        result.status = "failed"
        result.error = f"could not fetch (status={getattr(resp, 'status_code', 'n/a')})"
        return result

    result.final_url = resp.url
    result.pages_checked.append(resp.url)
    result.is_https = (urlparse(resp.url).scheme.lower() == "https")

    html = resp.text or ""
    soup = BeautifulSoup(html, "html.parser")
    haystack = _haystack(resp)

    # ---- metadata ----
    if soup.title and soup.title.string:
        result.title = soup.title.string.strip()
    md = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    if md and md.get("content"):
        result.description = md["content"].strip()
        result.has_meta_description = True
    og_desc = soup.find("meta", attrs={"property": re.compile(r"^og:description$", re.I)})
    if og_desc and og_desc.get("content"):
        result.og_description = og_desc["content"].strip()
    og_site = soup.find("meta", attrs={"property": re.compile(r"^og:site_name$", re.I)})
    if og_site and og_site.get("content"):
        result.og_site_name = og_site["content"].strip()
    gen = soup.find("meta", attrs={"name": re.compile("generator", re.I)})
    if gen and gen.get("content"):
        result.generator = gen["content"].strip()
    result.server = resp.headers.get("Server") or resp.headers.get("X-Powered-By")

    # ---- auto-extract contact info (used by UI to autofill form) ----
    _extract_contacts(result, soup, html, resp.url)

    # ---- text sample + word count (used by categoriser) ----
    sample, words = _text_sample(soup)
    result.text_sample = sample
    result.body_word_count = words

    # ---- SEO ----
    result.has_h1 = bool(soup.find("h1"))
    result.has_og_tags = bool(soup.find("meta", attrs={"property": re.compile(r"^og:", re.I)}))
    result.has_twitter_card = bool(soup.find("meta", attrs={"name": re.compile(r"^twitter:", re.I)}))
    result.has_structured_data = bool(
        soup.find("script", attrs={"type": re.compile("ld\\+json", re.I)})
    )
    result.has_favicon = bool(soup.find("link", rel=re.compile("icon", re.I)))

    # ---- core capability flags ----
    result.has_whatsapp = _detect_whatsapp(haystack, soup)

    chat = _find_first(haystack, LIVE_CHAT_SIGNATURES)
    if chat:
        result.has_live_chat = True
        result.detected_chat_provider = chat
        result.notes["live_chat"] = chat

    ec = _find_first(haystack, ECOMMERCE_SIGNATURES)
    if ec:
        result.has_ecommerce = True
        result.detected_ecommerce_platform = ec
        result.notes["ecommerce"] = ec
    elif _detect_ecommerce_buttons(soup):
        result.has_ecommerce = True
        result.notes["ecommerce"] = "cart/buy buttons"

    cf, cf_note = _detect_contact_form(soup)
    if cf:
        result.has_contact_form = True
        result.notes["contact_form"] = cf_note

    # form embeds (Typeform, Calendly, etc.) also count as contact form
    embeds = _find_all(haystack, FORM_EMBED_SIGNATURES)
    if embeds:
        result.detected_form_embeds = embeds
        result.has_contact_form = True
        result.notes.setdefault("contact_form", "embed: " + ", ".join(embeds))

    result.has_mobile_responsive = _detect_mobile_responsive(soup, haystack)

    # modern design = modern builder/framework or animation libs
    frameworks = _find_all(haystack, MODERN_FRAMEWORK_SIGNATURES)
    animations = _find_all(haystack, ANIMATION_SIGNATURES)
    result.detected_frameworks = frameworks
    result.detected_animations = animations
    MODERN_BUILDERS = {"Next.js", "Nuxt", "Gatsby", "Astro", "Remix", "SvelteKit",
                       "React", "Vue", "Webflow", "Framer"}
    is_modern = bool(MODERN_BUILDERS.intersection(frameworks)) or bool(animations)
    result.has_modern_design = is_modern
    if is_modern:
        bits = []
        if frameworks: bits.append("frameworks: " + ", ".join(frameworks))
        if animations: bits.append("animations: " + ", ".join(animations))
        result.notes["modern_design"] = "; ".join(bits)

    # ---- trust / contact ----
    link_targets = _link_targets_lower(soup)
    result.has_click_to_call = any(h.startswith("tel:") for h in link_targets)
    result.has_email_link = any(h.startswith("mailto:") for h in link_targets)
    result.has_address_or_map = _detect_location(soup, haystack, result.extracted_addresses)
    result.has_testimonials = _any_in(haystack, TESTIMONIAL_SIGNATURES)
    result.has_privacy_policy = any(
        re.search(r"privacy(-|_| )?policy|/privacy", h) for h in link_targets
    )
    result.has_about_page = any(
        re.search(r"/about|/our[-_ ]?story|/who[-_ ]?we[-_ ]?are", h) for h in link_targets
    )
    result.social_links = _detect_social(html)

    # ---- engagement / content ----
    result.has_blog = any(
        re.search(r"/blog|/news|/articles|/posts", h) for h in link_targets
    ) or "wp-content/themes" in haystack and "/category/" in haystack
    result.has_faq = "faq" in haystack or "frequently asked" in haystack
    result.has_gallery = any(
        re.search(r"/gallery|/portfolio|/work|/projects", h) for h in link_targets
    ) or _any_in(haystack, ["gallery", "lightbox", "fancybox"])
    result.has_video_embed = _any_in(haystack, VIDEO_EMBED_SIGNATURES)
    result.has_newsletter_signup = _detect_newsletter(soup, haystack)
    result.has_cookie_banner = _any_in(haystack, COOKIE_BANNER_SIGNATURES)

    # analytics
    result.detected_analytics = _find_all(haystack, ANALYTICS_SIGNATURES)

    # quantitative
    images = soup.find_all("img")
    result.images_total = len(images)
    result.images_with_alt = sum(1 for img in images if (img.get("alt") or "").strip())
    result.has_lazy_loading = any((img.get("loading") or "").lower() == "lazy" for img in images)
    result.script_count = len(soup.find_all("script"))
    result.cta_count = _count_ctas(soup)

    # ---- secondary pages: contact / shop fallbacks ----
    if not result.has_contact_form:
        _probe_pages(result, soup, CONTACT_PATH_CANDIDATES, kind="contact_form")
    if not result.has_ecommerce:
        _probe_pages(result, soup, SHOP_PATH_CANDIDATES, kind="ecommerce")

    # ---- robots.txt + sitemap ----
    parsed = urlparse(result.final_url or url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rt = _fetch(origin + "/robots.txt", timeout=8)
    if rt is not None and rt.ok and "user-agent" in (rt.text or "").lower():
        result.has_robots_txt = True
        if "sitemap:" in (rt.text or "").lower():
            result.has_sitemap = True
    if not result.has_sitemap:
        sm = _fetch(origin + "/sitemap.xml", timeout=8)
        if sm is not None and sm.ok and "<urlset" in (sm.text or "").lower():
            result.has_sitemap = True

    return result


EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_REGEX = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s\-.]*)?\(?\d{2,4}\)?[\s\-.]*\d{3,4}[\s\-.]*\d{3,5}(?!\d)"
)

JUNK_EMAIL_DOMAINS = {"sentry.io", "sentry-cdn.com", "wixpress.com",
                      "example.com", "domain.com", "yourdomain.com"}
JUNK_EMAIL_PREFIXES = {"noreply", "no-reply", "donotreply"}


def _clean_business_name_from_title(title: str, host: str) -> str:
    # strip trailing taglines after | – — :
    for sep in [" | ", " – ", " — ", " - ", " : ", " :: "]:
        if sep in title:
            parts = title.split(sep)
            # heuristic: keep the shorter side (usually the brand name)
            parts.sort(key=len)
            return parts[0].strip()
    return title.strip()


def _looks_like_phone(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    return 7 <= len(digits) <= 15


def _extract_contacts(result: ScrapeResult, soup: BeautifulSoup, html: str, final_url: str):
    # business name: prefer og:site_name → cleaned <title> → first <h1> → domain
    candidates = []
    if result.og_site_name:
        candidates.append(result.og_site_name)
    if result.title:
        host = urlparse(final_url).netloc.lower()
        candidates.append(_clean_business_name_from_title(result.title, host))
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        candidates.append(h1.get_text(strip=True))
    host = urlparse(final_url).netloc.replace("www.", "").split(".")[0]
    if host:
        candidates.append(host.replace("-", " ").title())
    for c in candidates:
        if c and 2 <= len(c) <= 80:
            result.extracted_business_name = c
            break

    # emails: mailto: links + body regex
    emails: list[str] = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.lower().startswith("mailto:"):
            addr = h.split(":", 1)[1].split("?")[0].strip()
            if addr:
                emails.append(addr)
    emails.extend(EMAIL_REGEX.findall(html))

    seen = set()
    clean_emails = []
    for e in emails:
        e = e.strip().rstrip(".,;:")
        if not e or e.lower() in seen:
            continue
        domain = e.split("@", 1)[-1].lower()
        prefix = e.split("@", 1)[0].lower()
        if domain in JUNK_EMAIL_DOMAINS or prefix in JUNK_EMAIL_PREFIXES:
            continue
        # filter image-path false positives (foo@2x.png style)
        if domain.split(".")[-1] in {"png", "jpg", "jpeg", "gif", "svg", "webp"}:
            continue
        seen.add(e.lower())
        clean_emails.append(e)
        if len(clean_emails) >= 8:
            break
    result.extracted_emails = clean_emails

    # phones: prefer tel: links (high precision); supplement with regex if none found
    phones: list[str] = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.lower().startswith("tel:"):
            num = h.split(":", 1)[1].strip()
            if num and _looks_like_phone(num):
                phones.append(num)
    if not phones:
        text_only = soup.get_text(" ", strip=True)
        for m in PHONE_REGEX.findall(text_only)[:30]:
            if _looks_like_phone(m):
                phones.append(m.strip())

    seen_p, clean_phones = set(), []
    for p in phones:
        canon = re.sub(r"[\s\-.()]", "", p)
        if canon in seen_p:
            continue
        seen_p.add(canon)
        clean_phones.append(p)
        if len(clean_phones) >= 5:
            break
    result.extracted_phones = clean_phones

    # addresses from JSON-LD
    addrs: list[str] = []
    import json as _json
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            data = _json.loads(s.string or s.get_text() or "")
        except (ValueError, TypeError):
            continue
        for node in _json_walk(data):
            if isinstance(node, dict):
                addr = node.get("address")
                if isinstance(addr, dict):
                    parts = [addr.get(k) for k in
                             ("streetAddress", "addressLocality", "addressRegion",
                              "postalCode", "addressCountry")]
                    parts = [str(p) for p in parts if p]
                    if parts:
                        addrs.append(", ".join(parts))
                elif isinstance(addr, str) and addr.strip():
                    addrs.append(addr.strip())

    # addresses from <address> tags
    for tag in soup.find_all("address"):
        t = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if t and 8 <= len(t) <= 240:
            addrs.append(t)

    # addresses from microdata
    for tag in soup.find_all(attrs={"itemprop": "address"}):
        t = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if t and 8 <= len(t) <= 240:
            addrs.append(t)

    # text-pattern extraction as a last resort
    if not addrs:
        full_text = soup.get_text(" ", strip=True)
        for pattern in STREET_PATTERNS:
            for m in pattern.finditer(full_text):
                # capture ~120 chars around the street pattern as the address
                start = max(0, m.start() - 30)
                end   = min(len(full_text), m.end() + 80)
                snippet = re.sub(r"\s+", " ", full_text[start:end]).strip()
                if snippet:
                    addrs.append(snippet)
                if len(addrs) >= 5:
                    break
            if addrs:
                break

    # de-dupe
    result.extracted_addresses = list(dict.fromkeys(addrs))[:5]


def _json_walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _json_walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _json_walk(v)


def _probe_pages(result: ScrapeResult, home_soup: BeautifulSoup, candidates, kind: str):
    base = result.final_url or result.url
    parsed_base = urlparse(base)
    keywords = [c.strip("/").lower() for c in candidates]
    tried = set()

    for a in home_soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        if not any(k in href.lower() for k in keywords):
            continue
        full = urljoin(base, href)
        if urlparse(full).netloc != parsed_base.netloc:
            continue
        tried.add(full)
        if _probe_one(result, full, kind):
            return

    for path in candidates:
        full = urljoin(base, path)
        if full in tried:
            continue
        if _probe_one(result, full, kind):
            return


def _probe_one(result: ScrapeResult, url: str, kind: str) -> bool:
    resp = _fetch(url, timeout=10)
    if resp is None or not resp.ok:
        return False
    result.pages_checked.append(resp.url)
    soup = BeautifulSoup(resp.text or "", "html.parser")
    haystack = _haystack(resp)

    if kind == "contact_form":
        has, note = _detect_contact_form(soup)
        if has:
            result.has_contact_form = True
            result.notes["contact_form"] = f"{note} on {urlparse(url).path or '/'}"
            return True
    elif kind == "ecommerce":
        ec = _find_first(haystack, ECOMMERCE_SIGNATURES)
        if ec:
            result.has_ecommerce = True
            result.detected_ecommerce_platform = ec
            result.notes["ecommerce"] = f"{ec} on {urlparse(url).path or '/'}"
            return True
        if _detect_ecommerce_buttons(soup):
            result.has_ecommerce = True
            result.notes["ecommerce"] = f"cart/buy buttons on {urlparse(url).path or '/'}"
            return True
    return False
