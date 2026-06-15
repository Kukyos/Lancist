"""
Lead finder — find SMB websites by querying OpenStreetMap directly.

Why OSM instead of Bing/DDG scraping?
  - search engines return news, dictionaries, and aggregator spam
  - OSM has structured business listings: name, address, website, phone
  - free, no API key, no per-query cost
  - "every dentist within 5km of point X" is a one-shot query (Overpass)

Stack:
  - geolocate_ip()    -> ipapi.co (free, no key, ~1000 req/day)
  - geocode_place()   -> Nominatim (OSM's free geocoder, 1 req/sec polite)
  - find_nearby()     -> Overpass API (free, no key, queries OSM tag DB)

All three require a polite User-Agent that identifies the app.

Public surface kept compatible with the old finder so main.py only needs
small changes:
  - dataclasses FoundLead, FindResult
  - class Finder (now takes lat/lng/radius/category instead of city/text)
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

import requests


# ---------------------------------------------------------------------------
# politeness — every OSM service insists on a UA that identifies the app
# ---------------------------------------------------------------------------

UA = "Lancist/0.1 (Oryn Outreach Bot; contact: amohamedarmaan@gmail.com)"
HEADERS = {"User-Agent": UA, "Accept": "application/json"}


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

@dataclass
class FoundLead:
    website: str
    business_name: str
    description: str
    source_query: str
    # new in v2 — populated when we know the location
    lat: Optional[float] = None
    lng: Optional[float] = None
    address: str = ""
    phone: str = ""
    osm_id: str = ""

    def to_dict(self) -> dict:
        return {
            "website":       self.website,
            "business_name": self.business_name,
            "description":   self.description,
            "source_query":  self.source_query,
            "lat":           self.lat,
            "lng":           self.lng,
            "address":       self.address,
            "phone":         self.phone,
            "osm_id":        self.osm_id,
            "status":        "new",
        }


@dataclass
class FindResult:
    leads: list[FoundLead] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    errors:     list[str]  = field(default_factory=list)
    # so the UI can show "23 places found in OSM, 11 had websites"
    raw_count: int = 0


# ---------------------------------------------------------------------------
# category -> OSM tag selectors
# ---------------------------------------------------------------------------
# Each value is a list of (key, value) pairs OR'd together. OSM tag a place
# might have multiple of these — that's fine, dedupe by id handles it.
#
# Reference: https://wiki.openstreetmap.org/wiki/Map_features

CATEGORY_TAGS: dict[str, list[tuple[str, str]]] = {
    # food & drink
    "cafe":         [("amenity", "cafe")],
    "restaurant":   [("amenity", "restaurant")],
    "bakery":       [("shop", "bakery")],
    "ice_cream":    [("amenity", "ice_cream")],
    "bar_pub":      [("amenity", "bar"), ("amenity", "pub")],
    # health
    "dentist":      [("amenity", "dentist"), ("healthcare", "dentist")],
    "doctor":       [("amenity", "doctors"), ("healthcare", "doctor")],
    "clinic":       [("amenity", "clinic"), ("healthcare", "clinic")],
    "pharmacy":     [("amenity", "pharmacy")],
    "veterinary":   [("amenity", "veterinary")],
    "physio":       [("healthcare", "physiotherapist")],
    # beauty / fitness
    "gym":          [("leisure", "fitness_centre"), ("sport", "fitness")],
    "yoga":         [("sport", "yoga"), ("leisure", "fitness_centre")],
    "salon":        [("shop", "hairdresser"), ("shop", "beauty")],
    "spa":          [("leisure", "spa"), ("amenity", "spa")],
    # services
    "lawyer":       [("office", "lawyer")],
    "accountant":   [("office", "accountant")],
    "architect":    [("office", "architect")],
    "real_estate":  [("office", "estate_agent")],
    "moving":       [("office", "moving_company")],
    "car_repair":   [("shop", "car_repair")],
    "car_rental":   [("amenity", "car_rental")],
    "tailor":       [("shop", "tailor")],
    # retail (small enough to be SMB pitchable)
    "boutique":     [("shop", "boutique"), ("shop", "clothes")],
    "jewellery":    [("shop", "jewelry")],
    "florist":      [("shop", "florist")],
    "bookshop":     [("shop", "books")],
    # hospitality
    "hotel":        [("tourism", "hotel")],
    "guesthouse":   [("tourism", "guest_house")],
    "hostel":       [("tourism", "hostel")],
    # education-adjacent (private tutoring etc.)
    "tutor":        [("amenity", "language_school"), ("amenity", "music_school")],
    "driving_school": [("amenity", "driving_school")],
}

# pretty label used in the UI dropdown
CATEGORY_LABELS: dict[str, str] = {
    "cafe": "Cafe / Coffee shop",
    "restaurant": "Restaurant",
    "bakery": "Bakery",
    "ice_cream": "Ice cream parlour",
    "bar_pub": "Bar / Pub",
    "dentist": "Dentist",
    "doctor": "Doctor's clinic",
    "clinic": "Multi-specialty clinic",
    "pharmacy": "Pharmacy",
    "veterinary": "Veterinary clinic",
    "physio": "Physiotherapist",
    "gym": "Gym / Fitness centre",
    "yoga": "Yoga studio",
    "salon": "Salon / Beauty parlour",
    "spa": "Spa",
    "lawyer": "Lawyer / Law firm",
    "accountant": "Accountant / CA",
    "architect": "Architect",
    "real_estate": "Real estate agent",
    "moving": "Movers / Packers",
    "car_repair": "Car repair / Garage",
    "car_rental": "Car rental",
    "tailor": "Tailor",
    "boutique": "Boutique / Clothing",
    "jewellery": "Jeweller",
    "florist": "Florist",
    "bookshop": "Bookshop",
    "hotel": "Hotel",
    "guesthouse": "Guesthouse",
    "hostel": "Hostel",
    "tutor": "Tutor / Coaching",
    "driving_school": "Driving school",
}


# domains we drop even when OSM lists them — directories, social, marketplaces
SKIP_DOMAINS = {
    "facebook.com", "m.facebook.com", "instagram.com", "twitter.com",
    "x.com", "linkedin.com", "youtube.com", "pinterest.com",
    "justdial.com", "sulekha.com", "indiamart.com", "tradeindia.com",
    "yelp.com", "tripadvisor.com", "tripadvisor.in", "zomato.com",
    "swiggy.com", "booking.com", "airbnb.com", "agoda.com", "makemytrip.com",
    "goibibo.com", "urbancompany.com", "urbanclap.com", "wa.me", "whatsapp.com",
    "linktr.ee", "carrd.co",
}


def _bare_domain(netloc: str) -> str:
    return netloc.lower().lstrip(".").removeprefix("www.")


def _is_skipped(url: str) -> bool:
    try:
        p = urlparse(url)
        if not p.netloc:
            return True
        bare = _bare_domain(p.netloc)
        for sd in SKIP_DOMAINS:
            if bare == sd or bare.endswith("." + sd):
                return True
        return False
    except Exception:
        return True


def _homepage(url: str) -> str:
    """Normalize to scheme://host (strip path, query, fragment)."""
    try:
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        p = urlparse(url)
        if not p.scheme or not p.netloc:
            return ""
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# IP geolocation — for the "centre map on my location" button
# ---------------------------------------------------------------------------

def geolocate_ip(*, timeout: int = 8) -> Optional[dict]:
    """
    Returns {lat, lng, city, region, country, country_code} or None.

    Uses ipapi.co (no key, free for ~1000 req/day). We accept None silently
    on any error — the UI just falls back to a hard-coded city.
    """
    try:
        r = requests.get(
            "https://ipapi.co/json/",
            headers=HEADERS, timeout=timeout,
        )
        if r.status_code != 200:
            return None
        d = r.json()
        if "latitude" not in d or "longitude" not in d:
            return None
        return {
            "lat":          float(d["latitude"]),
            "lng":          float(d["longitude"]),
            "city":         d.get("city") or "",
            "region":       d.get("region") or "",
            "country":      d.get("country_name") or "",
            "country_code": (d.get("country_code") or "").lower(),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Nominatim — geocode a place name to lat/lng
# ---------------------------------------------------------------------------

_NOMINATIM_LAST_CALL = 0.0  # process-global rate limiter; OSM asks <= 1 req/s


def _nominatim_throttle():
    global _NOMINATIM_LAST_CALL
    elapsed = time.monotonic() - _NOMINATIM_LAST_CALL
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _NOMINATIM_LAST_CALL = time.monotonic()


def geocode_place(query: str, *, timeout: int = 15,
                  country_code: str = "in") -> Optional[dict]:
    """
    Returns {lat, lng, display_name} for the top Nominatim match, or None.
    country_code biases the search; pass "" to search globally.
    """
    q = (query or "").strip()
    if not q:
        return None
    _nominatim_throttle()
    try:
        params = {"q": q, "format": "json", "limit": 1, "addressdetails": 0}
        if country_code:
            params["countrycodes"] = country_code
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params=params, headers=HEADERS, timeout=timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        top = data[0]
        return {
            "lat":          float(top["lat"]),
            "lng":          float(top["lon"]),
            "display_name": top.get("display_name") or q,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Overpass — find businesses near a point
# ---------------------------------------------------------------------------

# Multiple endpoints so we have a fallback if one is under load.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


def _build_overpass_query(lat: float, lng: float, radius_m: int,
                          category_key: str) -> str:
    tags = CATEGORY_TAGS.get(category_key)
    if not tags:
        raise ValueError(f"unknown category: {category_key!r}")

    # Build a node/way/relation block per tag pair. center for ways/relations.
    blocks = []
    for k, v in tags:
        blocks.append(f'  node["{k}"="{v}"](around:{radius_m},{lat},{lng});')
        blocks.append(f'  way["{k}"="{v}"](around:{radius_m},{lat},{lng});')
        blocks.append(f'  relation["{k}"="{v}"](around:{radius_m},{lat},{lng});')

    return (
        "[out:json][timeout:25];\n"
        "(\n"
        + "\n".join(blocks) + "\n"
        ");\n"
        "out center tags;\n"
    )


def _element_latlng(el: dict) -> tuple[Optional[float], Optional[float]]:
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    c = el.get("center") or {}
    if "lat" in c and "lon" in c:
        return float(c["lat"]), float(c["lon"])
    return None, None


def _format_address(tags: dict) -> str:
    # Try the friendliest combination first, then fall back.
    full = tags.get("addr:full") or ""
    if full:
        return full
    parts = []
    h = tags.get("addr:housenumber")
    s = tags.get("addr:street")
    if h and s:
        parts.append(f"{h} {s}")
    elif s:
        parts.append(s)
    for k in ("addr:suburb", "addr:city", "addr:state", "addr:postcode"):
        v = tags.get(k)
        if v:
            parts.append(v)
    return ", ".join(parts)


def _website_from_tags(tags: dict) -> str:
    for k in ("website", "contact:website", "url"):
        v = tags.get(k)
        if v:
            return v.strip()
    return ""


def _phone_from_tags(tags: dict) -> str:
    for k in ("phone", "contact:phone", "mobile"):
        v = tags.get(k)
        if v:
            return v.strip()
    return ""


def _name_from_tags(tags: dict) -> str:
    for k in ("name:en", "name", "operator", "brand"):
        v = tags.get(k)
        if v:
            return v.strip()[:120]
    return ""


def find_nearby(
    lat: float, lng: float, radius_m: int, category_key: str,
    *,
    require_website: bool = True,
    existing_websites: Optional[set[str]] = None,
    timeout: int = 30,
    progress: Optional[Callable[[str], None]] = None,
) -> FindResult:
    """
    Query Overpass for all OSM elements matching `category_key` within
    `radius_m` of (lat, lng). Returns dedup'd FoundLeads.

    By default only elements that have a website tag are returned, since the
    downstream pipeline (scrape/audit/email) needs a URL.
    """
    res = FindResult()
    query = _build_overpass_query(lat, lng, radius_m, category_key)
    res.queries_run.append(f"{category_key}@({lat:.4f},{lng:.4f}) r={radius_m}m")

    def emit(msg: str):
        if progress:
            try: progress(msg)
            except Exception: pass

    existing = {(_homepage(u) or "").lower().rstrip("/")
                for u in (existing_websites or []) if u}

    data = None
    last_err = ""
    for ep in OVERPASS_ENDPOINTS:
        emit(f"Querying OSM ({urlparse(ep).netloc})...")
        try:
            r = requests.post(ep, data={"data": query},
                              headers={"User-Agent": UA}, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                break
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(0.6)

    if data is None:
        res.errors.append(f"overpass failed: {last_err or 'unknown'}")
        return res

    elements = data.get("elements") or []
    res.raw_count = len(elements)
    emit(f"OSM returned {len(elements)} place(s). Filtering...")

    seen_domains: set[str] = set()
    seen_osm: set[str] = set()
    leads: list[FoundLead] = []

    for el in elements:
        osm_id = f"{el.get('type','?')}/{el.get('id','?')}"
        if osm_id in seen_osm:
            continue
        seen_osm.add(osm_id)

        tags = el.get("tags") or {}
        name = _name_from_tags(tags)
        if not name:
            continue

        website = _website_from_tags(tags)
        if require_website and not website:
            continue
        home = _homepage(website) if website else ""
        if require_website:
            if not home or _is_skipped(home):
                continue
            key = home.lower().rstrip("/")
            if key in seen_domains or key in existing:
                continue
            seen_domains.add(key)

        plat, plng = _element_latlng(el)
        leads.append(FoundLead(
            website=home,
            business_name=name,
            description=_format_address(tags) or tags.get("description") or "",
            source_query=res.queries_run[0],
            lat=plat,
            lng=plng,
            address=_format_address(tags),
            phone=_phone_from_tags(tags),
            osm_id=osm_id,
        ))

    res.leads = leads
    emit(f"Done. {len(leads)} usable lead(s) (had a website).")
    return res


# ---------------------------------------------------------------------------
# threaded wrapper (for the UI)
# ---------------------------------------------------------------------------

class Finder:
    """Run find_nearby on a worker thread without freezing the UI."""

    def __init__(self, *, lat: float, lng: float, radius_m: int,
                 category_key: str,
                 require_website: bool = True,
                 existing_websites: Optional[set[str]] = None,
                 progress: Optional[Callable[[str], None]] = None):
        self.lat = lat
        self.lng = lng
        self.radius_m = radius_m
        self.category_key = category_key
        self.require_website = require_website
        self.existing_websites = existing_websites or set()
        self.progress = progress
        self._thread: Optional[threading.Thread] = None
        self.result: Optional[FindResult] = None

    def start(self, on_done: Callable[[FindResult], None]):
        def run():
            try:
                self.result = find_nearby(
                    self.lat, self.lng, self.radius_m, self.category_key,
                    require_website=self.require_website,
                    existing_websites=self.existing_websites,
                    progress=self.progress,
                )
            except Exception as e:
                self.result = FindResult(errors=[str(e)])
            on_done(self.result)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self._thread = t


# ---------------------------------------------------------------------------
# geometry helpers used by the UI to draw the radius circle on the map
# ---------------------------------------------------------------------------

def circle_polygon(lat: float, lng: float, radius_m: int,
                   segments: int = 48) -> list[tuple[float, float]]:
    """
    Returns a closed polygon (list of (lat, lng) tuples) approximating a
    circle of `radius_m` metres around the centre. Used to draw the
    search-area ring on the map.
    """
    # ~111_000 m per degree of latitude; longitude shrinks with cos(lat)
    deg_lat = radius_m / 111_000.0
    deg_lng = radius_m / (111_000.0 * max(0.01, math.cos(math.radians(lat))))
    pts: list[tuple[float, float]] = []
    for i in range(segments):
        theta = 2 * math.pi * i / segments
        pts.append((lat + deg_lat * math.sin(theta),
                    lng + deg_lng * math.cos(theta)))
    pts.append(pts[0])
    return pts
