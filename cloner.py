"""
Local mirror of a target website for the 'Make demo' workflow.

For each lead we clone:
  - the homepage
  - every internal page linked from the homepage (capped, configurable)
  - every CSS, JS, image, font, video referenced by those pages
  - every url(...) inside the CSS files

Then we rewrite every <link>, <script>, <img>, <source>, <a href> to point at
the local copy so opening index.html offline renders the site faithfully.

The output folder is then handed to the user (and to ORYN.md) so they can edit
HTML/CSS directly and ship the "improved" version back to the prospect.
"""
from __future__ import annotations

import hashlib
import mimetypes
import re
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse, urlunparse, unquote

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

DEFAULT_MAX_PAGES  = 12
DEFAULT_MAX_ASSETS = 400
DEFAULT_TIMEOUT    = 20


def slugify(s: str, max_len: int = 60) -> str:
    s = re.sub(r"https?://", "", s or "")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s or "lead")[:max_len]


class CloneResult:
    def __init__(self):
        self.folder: Optional[Path] = None
        self.index_path: Optional[Path] = None
        self.pages: list[Path] = []
        self.assets: list[Path] = []
        self.errors: list[str] = []
        self.cancelled = False

    def to_dict(self):
        return {
            "folder": str(self.folder) if self.folder else None,
            "index_path": str(self.index_path) if self.index_path else None,
            "pages": [str(p) for p in self.pages],
            "asset_count": len(self.assets),
            "errors": self.errors[:20],
            "cancelled": self.cancelled,
        }


class Cloner:
    """
    Single-shot site mirror. Construct, call .clone(), or .start() for a
    background thread; result accumulates in self.result.
    """

    def __init__(
        self,
        url: str,
        out_dir: Path,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_assets: int = DEFAULT_MAX_ASSETS,
        timeout: int = DEFAULT_TIMEOUT,
        progress: Optional[Callable[[str], None]] = None,
    ):
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        self.url = url
        self.origin_host = urlparse(url).netloc.lower()
        self.out_dir = Path(out_dir)
        self.assets_dir = self.out_dir / "assets"
        self.max_pages = max_pages
        self.max_assets = max_assets
        self.timeout = timeout
        self.progress = progress or (lambda _m: None)

        self.session = requests.Session()
        self.session.headers.update(HEADERS)

        # url → local relative filename (within assets_dir)
        self.assets_by_url: dict[str, str] = {}
        # canonical url → local Path of the saved page (in out_dir root)
        self.pages_by_url: dict[str, Path] = {}
        self.queue: list[tuple[str, bool]] = []  # (url, is_home)
        self.result = CloneResult()
        self._cancel = threading.Event()

    # ---------- public API ----------

    def cancel(self):
        self._cancel.set()

    def clone(self) -> CloneResult:
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self.assets_dir.mkdir(parents=True, exist_ok=True)
            self.result.folder = self.out_dir
            self._fetch_page(self.url, is_home=True)
            # process queue (BFS, 1 level deep from home)
            while self.queue and len(self.pages_by_url) < self.max_pages:
                if self._cancel.is_set():
                    self.result.cancelled = True
                    break
                next_url, _ = self.queue.pop(0)
                self._fetch_page(next_url, is_home=False)
        except Exception as e:
            self.result.errors.append(f"fatal: {e}")
        return self.result

    def start(self, on_done: Optional[Callable[[CloneResult], None]] = None):
        def _run():
            r = self.clone()
            if on_done:
                on_done(r)
        threading.Thread(target=_run, daemon=True).start()

    # ---------- page fetching ----------

    def _fetch_page(self, url: str, is_home: bool):
        canon = self._canonicalise(url)
        if canon in self.pages_by_url:
            return
        if self._cancel.is_set():
            return
        self.progress(f"page: {url}")
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as e:
            self.result.errors.append(f"GET {url}: {e}")
            return
        if not r.ok:
            self.result.errors.append(f"GET {url} → {r.status_code}")
            return
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype:
            return

        soup = BeautifulSoup(r.text, "html.parser")
        page_url = r.url     # post-redirect

        # rewrite assets (CSS, JS, images, fonts, video sources, icons)
        self._process_assets(soup, base_url=page_url)

        # rewrite internal <a href>
        internal_links_to_follow = self._rewrite_internal_links(soup, page_url, is_home)

        # save page
        local_path = self._page_save_path(page_url, is_home)
        try:
            local_path.write_text(str(soup), encoding="utf-8")
        except OSError as e:
            self.result.errors.append(f"write {local_path}: {e}")
            return
        self.pages_by_url[canon] = local_path
        self.result.pages.append(local_path)
        if is_home:
            self.result.index_path = local_path

        # queue 1-level-deep internal pages discovered on the homepage
        if is_home:
            for link in internal_links_to_follow:
                lc = self._canonicalise(link)
                if lc in self.pages_by_url:
                    continue
                if (link, False) in self.queue:
                    continue
                self.queue.append((link, False))

    def _page_save_path(self, page_url: str, is_home: bool) -> Path:
        if is_home:
            return self.out_dir / "index.html"
        parsed = urlparse(page_url)
        path = unquote(parsed.path or "")
        slug = re.sub(r"[^a-z0-9]+", "-", path.lower().strip("/")).strip("-")
        if not slug:
            slug = "page-" + hashlib.md5(page_url.encode()).hexdigest()[:6]
        return self.out_dir / f"{slug[:80]}.html"

    # ---------- asset handling ----------

    _ASSET_TARGETS = [
        ("link",   "href"),
        ("script", "src"),
        ("img",    "src"),
        ("img",    "data-src"),
        ("source", "src"),
        ("source", "srcset"),
        ("video",  "src"),
        ("video",  "poster"),
        ("audio",  "src"),
        ("iframe", "src"),    # only same-origin ones get rewritten; external left alone
    ]

    def _process_assets(self, soup: BeautifulSoup, base_url: str):
        for tag_name, attr in self._ASSET_TARGETS:
            for tag in soup.find_all(tag_name):
                val = tag.get(attr)
                if not val:
                    continue
                if attr == "srcset":
                    self._rewrite_srcset(tag, attr, val, base_url)
                else:
                    self._rewrite_single_asset(tag, attr, val, base_url)
        # inline style url(...) too
        for el in soup.find_all(style=True):
            new_style = self._rewrite_css_text(el["style"], base_url)
            if new_style != el["style"]:
                el["style"] = new_style

    def _rewrite_single_asset(self, tag, attr, val, base_url):
        if val.startswith(("data:", "javascript:", "#", "mailto:", "tel:")):
            return
        full = urljoin(base_url, val)
        local_rel = self._download_asset(full)
        if local_rel:
            tag[attr] = local_rel

    def _rewrite_srcset(self, tag, attr, val, base_url):
        out_parts = []
        for chunk in val.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(None, 1)
            url = parts[0]
            descriptor = (" " + parts[1]) if len(parts) > 1 else ""
            full = urljoin(base_url, url)
            local_rel = self._download_asset(full)
            out_parts.append((local_rel or url) + descriptor)
        tag[attr] = ", ".join(out_parts)

    def _download_asset(self, url: str) -> Optional[str]:
        if url in self.assets_by_url:
            return "assets/" + self.assets_by_url[url]
        if len(self.assets_by_url) >= self.max_assets:
            return None
        if self._cancel.is_set():
            return None
        try:
            r = self.session.get(url, timeout=self.timeout, stream=False)
        except requests.RequestException:
            return None
        if not r.ok:
            return None

        name = self._asset_filename(url, r)
        local_path = self.assets_dir / name
        try:
            local_path.write_bytes(r.content)
        except OSError as e:
            self.result.errors.append(f"write {local_path}: {e}")
            return None
        self.assets_by_url[url] = name
        self.result.assets.append(local_path)
        self.progress(f"asset ({len(self.assets_by_url)}): {name}")

        # nested CSS url(...) and @import
        if name.lower().endswith(".css"):
            try:
                css_text = r.text
            except Exception:
                css_text = ""
            new_text = self._rewrite_css_text(css_text, url, store_inline=False)
            try:
                local_path.write_text(new_text, encoding="utf-8")
            except OSError:
                pass
        return "assets/" + name

    _CSS_URL_RE  = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""")
    _CSS_IMPORT_RE = re.compile(
        r"""@import\s+(?:url\()?\s*['"]?([^'");\s]+)['"]?\s*\)?""")

    def _rewrite_css_text(self, css_text: str, base_url: str,
                          store_inline: bool = True) -> str:
        def _repl(match: re.Match) -> str:
            ref = match.group(1).strip()
            if not ref or ref.startswith(("data:", "#")):
                return match.group(0)
            full = urljoin(base_url, ref)
            local = self._download_asset(full)
            if not local:
                return match.group(0)
            # When the CSS file is in /assets/, sibling files are just `name.ext`
            local_name = local.split("/")[-1]
            return match.group(0).replace(ref, local_name) if store_inline \
                else match.group(0).replace(ref, local_name)

        text = self._CSS_URL_RE.sub(_repl, css_text)
        text = self._CSS_IMPORT_RE.sub(_repl, text)
        return text

    def _asset_filename(self, url: str, response: requests.Response) -> str:
        parsed = urlparse(url)
        orig_name = Path(parsed.path).name
        ext = Path(orig_name).suffix.lower()
        if not ext or len(ext) > 6:
            ct = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            ext = mimetypes.guess_extension(ct) or ""
            if not ext:
                ext = ""
        digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
        safe_name = re.sub(r"[^A-Za-z0-9._\-]+", "_", orig_name)[:60] if orig_name else "asset"
        if not safe_name.endswith(ext) and ext:
            safe_name = safe_name + ext
        return f"{digest}_{safe_name}" if safe_name else f"{digest}{ext}"

    # ---------- internal-link handling ----------

    def _rewrite_internal_links(self, soup: BeautifulSoup, base_url: str,
                                is_home: bool) -> list[str]:
        """
        Returns the list of internal URLs to enqueue for cloning (only when is_home).
        Same-origin <a href>s get rewritten so the offline site doesn't go to a
        404 the moment you click anything.
        """
        to_follow = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(base_url, href)
            p = urlparse(full)
            if p.netloc and p.netloc.lower() != self.origin_host:
                # external — open in new tab (keep absolute)
                a["href"] = full
                a["target"] = "_blank"
                a["rel"] = "noopener"
                continue
            # fragment-only
            if not p.path or p.path == "/":
                if p.fragment:
                    a["href"] = "#" + p.fragment
                    continue
            # internal: compute the local filename it WILL have when cloned
            local_name = self._page_save_path(full, is_home=False).name
            a["href"] = local_name + (("#" + p.fragment) if p.fragment else "")
            if is_home:
                to_follow.append(self._strip_fragment(full))
        return to_follow

    # ---------- helpers ----------

    def _canonicalise(self, url: str) -> str:
        p = urlparse(url)
        p = p._replace(fragment="")
        return urlunparse(p)

    @staticmethod
    def _strip_fragment(url: str) -> str:
        p = urlparse(url)
        return urlunparse(p._replace(fragment=""))
