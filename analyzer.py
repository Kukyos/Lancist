"""
Analysis pipeline on top of scraper.ScrapeResult:

    1. Categorise the business (so we never pitch ecommerce to a movers site).
    2. Score the site across 6 weighted dimensions, total /100.
    3. Generate a prioritised list of recommendations, filtered by what
       actually makes sense for the detected category.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from scraper import ScrapeResult


# ---------- Category definitions ----------

@dataclass
class CategoryDef:
    key: str
    label: str
    keywords: list[str]                 # frequency-counted in text/title/desc
    boost_signals: list[str] = field(default_factory=list)  # ScrapeResult attrs that boost this category
    applicable_services: set[str] = field(default_factory=set)  # which Oryn-style services make sense


CATEGORIES: dict[str, CategoryDef] = {
    "restaurant_food": CategoryDef(
        "restaurant_food", "Restaurant / Food",
        ["menu", "reservation", "dine", "cuisine", "restaurant", "cafe", "café",
         "coffee shop", "coffee", "tea", "bistro", "kitchen", "chef", "dish",
         "appetizer", "dessert", "lunch", "dinner", "breakfast", "brunch",
         "delivery", "takeout", "takeaway", "order online", "swiggy", "zomato",
         "bakery", "pastry", "bar &", "pub", "diner", "eatery", "food truck",
         "beverage", "snack", "ice cream", "juice", "smoothie"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "online_ordering", "contact_form", "mobile_responsive",
                             "booking_system", "gallery"},
    ),
    "movers_logistics": CategoryDef(
        "movers_logistics", "Movers / Logistics",
        ["moving", "movers", "packers", "relocation", "shifting", "logistics",
         "transport", "freight", "cargo", "shipping", "warehouse", "haulage"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "quote_form", "contact_form", "mobile_responsive"},
    ),
    "ecommerce_retail": CategoryDef(
        "ecommerce_retail", "Retail / E-commerce",
        ["shop", "store", "buy", "product", "cart", "checkout", "wishlist",
         "collection", "shipping", "returns", "sale", "discount", "deals"],
        boost_signals=["has_ecommerce"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "ecommerce", "contact_form", "mobile_responsive",
                             "newsletter", "reviews"},
    ),
    "salon_spa_beauty": CategoryDef(
        "salon_spa_beauty", "Salon / Spa / Beauty",
        ["salon", "spa", "beauty", "hair", "nails", "facial", "massage",
         "waxing", "makeup", "stylist", "appointment", "booking", "treatment"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "booking_system", "ecommerce", "contact_form",
                             "gallery", "mobile_responsive"},
    ),
    "professional_services": CategoryDef(
        "professional_services", "Professional Services (Law / CA / Consulting)",
        ["lawyer", "attorney", "law firm", "advocate", "accountant", "audit",
         "consultant", "consulting", "advisory", "tax", "compliance", "litigation"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "contact_form", "mobile_responsive"},
    ),
    "healthcare_clinic": CategoryDef(
        "healthcare_clinic", "Healthcare / Clinic",
        ["clinic", "doctor", "dentist", "dental", "hospital", "patient",
         "appointment", "treatment", "health", "wellness", "medical",
         "physician", "surgery", "diagnostic", "pharmacy"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "booking_system", "contact_form", "mobile_responsive"},
    ),
    "real_estate": CategoryDef(
        "real_estate", "Real Estate",
        ["property", "real estate", "apartment", "villa", "flat", "rent",
         "lease", "broker", "listing", "for sale", "bhk", "sq ft", "sq.ft"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "contact_form", "gallery", "mobile_responsive"},
    ),
    "fitness_gym": CategoryDef(
        "fitness_gym", "Fitness / Gym / Yoga",
        ["gym", "fitness", "workout", "trainer", "yoga", "pilates", "crossfit",
         "weights", "cardio", "membership", "personal training"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "booking_system", "contact_form", "ecommerce",
                             "mobile_responsive"},
    ),
    "education_coaching": CategoryDef(
        "education_coaching", "Education / Coaching",
        ["school", "academy", "tuition", "coaching", "classes", "course",
         "students", "teacher", "exam", "training", "education", "institute",
         "iit", "neet", "syllabus", "admission"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "contact_form", "ecommerce", "mobile_responsive",
                             "newsletter"},
    ),
    "hotel_hospitality": CategoryDef(
        "hotel_hospitality", "Hotel / Hospitality",
        ["hotel", "resort", "lodge", "stay", "rooms", "suite", "amenities",
         "guest", "check-in", "check-out", "bed & breakfast", "homestay"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "booking_system", "contact_form", "gallery",
                             "mobile_responsive"},
    ),
    "saas_tech": CategoryDef(
        "saas_tech", "SaaS / Tech Product",
        ["saas", "software", "platform", "api", "integration", "dashboard",
         "analytics", "automation", "pricing", "free trial", "sign up",
         "login", "open source", "developer"],
        applicable_services={"modern_website", "ai_chatbot", "contact_form",
                             "newsletter", "mobile_responsive"},
    ),
    "agency_studio": CategoryDef(
        "agency_studio", "Agency / Studio (Design / Marketing)",
        ["agency", "studio", "branding", "creative", "marketing",
         "portfolio", "case study", "clients", "we craft", "design studio"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "contact_form", "gallery", "mobile_responsive"},
    ),
    "automotive": CategoryDef(
        "automotive", "Automotive (Sales / Service)",
        ["car", "auto", "vehicle", "service", "repair", "garage", "mechanic",
         "dealership", "showroom", "bike", "scooter", "tyre"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "booking_system", "contact_form", "gallery",
                             "mobile_responsive"},
    ),
    "home_services": CategoryDef(
        "home_services", "Home Services (Plumber / Electrician / Cleaning)",
        ["plumber", "electrician", "cleaning", "pest control", "ac repair",
         "appliance", "handyman", "service at home", "maid", "deep clean"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "quote_form", "contact_form", "mobile_responsive"},
    ),
    "events_wedding": CategoryDef(
        "events_wedding", "Events / Wedding / Photography",
        ["wedding", "event", "decor", "photographer", "videographer",
         "planner", "celebration", "venue", "catering", "bride", "groom"],
        applicable_services={"modern_website", "whatsapp_bot", "ai_chatbot",
                             "gallery", "contact_form", "booking_system",
                             "mobile_responsive"},
    ),
    "nonprofit_ngo": CategoryDef(
        "nonprofit_ngo", "Non-profit / NGO",
        ["ngo", "non-profit", "non profit", "charity", "donate", "donation",
         "cause", "volunteer", "mission", "foundation"],
        applicable_services={"modern_website", "ai_chatbot", "donation_system",
                             "contact_form", "newsletter", "mobile_responsive"},
    ),
    "blog_media": CategoryDef(
        "blog_media", "Blog / Media / Publication",
        ["blog", "article", "magazine", "publication", "news", "editorial",
         "subscribe", "newsletter"],
        applicable_services={"modern_website", "newsletter", "contact_form",
                             "mobile_responsive"},
    ),
    "portfolio_personal": CategoryDef(
        "portfolio_personal", "Personal Portfolio",
        ["portfolio", "about me", "i'm a", "my work", "freelance", "hire me", "resume"],
        applicable_services={"modern_website", "contact_form", "gallery",
                             "mobile_responsive"},
    ),
}

DEFAULT_CATEGORY = "professional_services"


# ---------- Result types ----------

@dataclass
class CategoryDetection:
    primary: str
    primary_label: str
    confidence: float                       # 0..1
    secondary: Optional[str] = None
    secondary_label: Optional[str] = None
    rationale: str = ""
    scores: dict = field(default_factory=dict)


@dataclass
class DimensionScore:
    key: str
    label: str
    score: int
    max: int
    items: list = field(default_factory=list)   # list of {label, gained, possible, met}


@dataclass
class ScoreCard:
    total: int
    grade: str
    dimensions: list = field(default_factory=list)  # list[DimensionScore]


@dataclass
class Recommendation:
    title: str
    severity: str                           # "high" | "medium" | "low"
    bucket: str                             # "Conversion" | "SEO" | "Trust" | ...
    rationale: str
    oryn_service: Optional[str] = None      # which Oryn offering this maps to


@dataclass
class Analysis:
    category: CategoryDetection
    scorecard: ScoreCard
    recommendations: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        return d


# ---------- Category detection ----------

def categorize(scrape: ScrapeResult, user_description: str = "") -> CategoryDetection:
    # weighted text bag
    bag = []
    if user_description:
        bag.append((user_description.lower(), 4.0))
    if scrape.title:
        bag.append((scrape.title.lower(), 3.0))
    if scrape.description:
        bag.append((scrape.description.lower(), 3.0))
    if scrape.text_sample:
        bag.append((scrape.text_sample.lower(), 1.0))

    scores: dict[str, float] = {k: 0.0 for k in CATEGORIES}
    for key, cat in CATEGORIES.items():
        for kw in cat.keywords:
            kw_l = kw.lower()
            for text, weight in bag:
                if not text:
                    continue
                count = text.count(kw_l)
                if count:
                    scores[key] += count * weight
        for sig in cat.boost_signals:
            if getattr(scrape, sig, False):
                scores[key] += 8.0

    # pick top two
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top_score = ranked[0]
    second_key, second_score = ranked[1]

    if top_score <= 0:
        return CategoryDetection(
            primary=DEFAULT_CATEGORY,
            primary_label=CATEGORIES[DEFAULT_CATEGORY].label,
            confidence=0.15,
            rationale="No strong keyword match — defaulted to professional services.",
            scores=scores,
        )

    # confidence based on margin between top and second
    margin = (top_score - second_score) / max(top_score, 1.0)
    raw_strength = min(top_score / 25.0, 1.0)
    confidence = round(0.4 * raw_strength + 0.6 * margin, 2)
    confidence = max(0.1, min(confidence, 0.99))

    rationale_bits = []
    cat = CATEGORIES[top_key]
    matched = [kw for kw in cat.keywords if (
        (user_description or "").lower().count(kw) +
        (scrape.title or "").lower().count(kw) +
        (scrape.description or "").lower().count(kw) +
        (scrape.text_sample or "").lower().count(kw)
    ) > 0]
    if matched:
        rationale_bits.append("keywords: " + ", ".join(matched[:6]))
    for sig in cat.boost_signals:
        if getattr(scrape, sig, False):
            rationale_bits.append(f"signal: {sig}")
    rationale = "; ".join(rationale_bits) if rationale_bits else "weak signals"

    return CategoryDetection(
        primary=top_key,
        primary_label=cat.label,
        confidence=confidence,
        secondary=second_key if second_score > 0 else None,
        secondary_label=CATEGORIES[second_key].label if second_score > 0 else None,
        rationale=rationale,
        scores={k: round(v, 1) for k, v in scores.items() if v > 0},
    )


# ---------- Scoring ----------

def _item(label, gained, possible, met=None):
    if met is None:
        met = gained >= possible
    return {"label": label, "gained": gained, "possible": possible, "met": bool(met)}


def score(scrape: ScrapeResult) -> ScoreCard:
    dims: list[DimensionScore] = []

    # 1. Design & Polish (20)
    items = []
    MODERN_BUILDERS = {"Next.js", "Nuxt", "Gatsby", "Astro", "Remix",
                       "SvelteKit", "React", "Vue", "Webflow", "Framer"}
    has_modern_fw = bool(MODERN_BUILDERS.intersection(scrape.detected_frameworks or []))
    p = 0
    items.append(_item("Built with a modern framework/builder", 8 if has_modern_fw else 0, 8))
    p += 8 if has_modern_fw else 0
    is_template = "WordPress" in (scrape.detected_frameworks or []) and not has_modern_fw
    has_custom = has_modern_fw or "Webflow" in (scrape.detected_frameworks or []) or "Framer" in (scrape.detected_frameworks or [])
    items.append(_item("Custom design (not a stock CMS template)", 4 if has_custom else (1 if not is_template else 0), 4))
    p += 4 if has_custom else (1 if not is_template else 0)
    items.append(_item("Has animation libs (GSAP / Framer Motion / AOS / Lottie / etc.)",
                       4 if scrape.detected_animations else 0, 4))
    p += 4 if scrape.detected_animations else 0
    has_visual = scrape.has_gallery or scrape.has_video_embed or scrape.images_total >= 6
    items.append(_item("Strong visuals (gallery / video / 6+ images)", 4 if has_visual else 0, 4))
    p += 4 if has_visual else 0
    dims.append(DimensionScore("design", "Design & Polish", p, 20, items))

    # 2. Mobile UX (15)
    items = []
    p = 0
    items.append(_item("Viewport meta + responsive markup",
                       8 if scrape.has_mobile_responsive else 0, 8))
    p += 8 if scrape.has_mobile_responsive else 0
    items.append(_item("Click-to-call (tel: link) on mobile",
                       4 if scrape.has_click_to_call else 0, 4))
    p += 4 if scrape.has_click_to_call else 0
    items.append(_item("Images lazy-loaded", 3 if scrape.has_lazy_loading else 0, 3))
    p += 3 if scrape.has_lazy_loading else 0
    dims.append(DimensionScore("mobile", "Mobile UX & Performance", p, 15, items))

    # 3. SEO (15)
    items = []
    p = 0
    pairs = [
        ("Title tag", 2, bool(scrape.title)),
        ("Meta description", 2, scrape.has_meta_description),
        ("Open Graph tags (social sharing previews)", 2, scrape.has_og_tags),
        ("Twitter card", 1, scrape.has_twitter_card),
        ("Structured data (JSON-LD)", 2, scrape.has_structured_data),
        ("H1 heading present", 2, scrape.has_h1),
        ("Sitemap.xml accessible", 2, scrape.has_sitemap),
        ("Robots.txt present", 1, scrape.has_robots_txt),
        ("Favicon", 1, scrape.has_favicon),
    ]
    for label, possible, met in pairs:
        gained = possible if met else 0
        items.append(_item(label, gained, possible))
        p += gained
    dims.append(DimensionScore("seo", "SEO Fundamentals", p, 15, items))

    # 4. Trust Signals (15)
    items = []
    p = 0
    items.append(_item("Served over HTTPS", 3 if scrape.is_https else 0, 3))
    p += 3 if scrape.is_https else 0
    has_contact_info = scrape.has_email_link or scrape.has_click_to_call
    items.append(_item("Visible contact info (email or phone link)",
                       3 if has_contact_info else 0, 3))
    p += 3 if has_contact_info else 0
    items.append(_item("Social profiles linked (≥ 2)",
                       3 if len(scrape.social_links) >= 2 else (1 if scrape.social_links else 0), 3))
    p += 3 if len(scrape.social_links) >= 2 else (1 if scrape.social_links else 0)
    items.append(_item("Testimonials / reviews section",
                       2 if scrape.has_testimonials else 0, 2))
    p += 2 if scrape.has_testimonials else 0
    items.append(_item("Physical location (Google Maps embed)",
                       2 if scrape.has_address_or_map else 0, 2))
    p += 2 if scrape.has_address_or_map else 0
    items.append(_item("Privacy policy link", 1 if scrape.has_privacy_policy else 0, 1))
    p += 1 if scrape.has_privacy_policy else 0
    items.append(_item("About page", 1 if scrape.has_about_page else 0, 1))
    p += 1 if scrape.has_about_page else 0
    dims.append(DimensionScore("trust", "Trust & Credibility", p, 15, items))

    # 5. Conversion Tools (20)
    items = []
    p = 0
    items.append(_item("Working contact form", 4 if scrape.has_contact_form else 0, 4))
    p += 4 if scrape.has_contact_form else 0
    items.append(_item("Click-to-call link", 3 if scrape.has_click_to_call else 0, 3))
    p += 3 if scrape.has_click_to_call else 0
    items.append(_item("Email link (mailto:)", 2 if scrape.has_email_link else 0, 2))
    p += 2 if scrape.has_email_link else 0
    items.append(_item("WhatsApp button / link", 3 if scrape.has_whatsapp else 0, 3))
    p += 3 if scrape.has_whatsapp else 0
    items.append(_item("Live chat or AI chatbot", 3 if scrape.has_live_chat else 0, 3))
    p += 3 if scrape.has_live_chat else 0
    items.append(_item("Newsletter signup", 2 if scrape.has_newsletter_signup else 0, 2))
    p += 2 if scrape.has_newsletter_signup else 0
    cta_pts = 3 if scrape.cta_count >= 3 else (2 if scrape.cta_count == 2 else (1 if scrape.cta_count == 1 else 0))
    items.append(_item(f"Clear CTA buttons (detected: {scrape.cta_count})", cta_pts, 3))
    p += cta_pts
    dims.append(DimensionScore("conversion", "Conversion Tools", p, 20, items))

    # 6. Engagement & Content (15)
    items = []
    p = 0
    items.append(_item("Blog / news section", 3 if scrape.has_blog else 0, 3))
    p += 3 if scrape.has_blog else 0
    items.append(_item("FAQ section", 2 if scrape.has_faq else 0, 2))
    p += 2 if scrape.has_faq else 0
    items.append(_item("Gallery / portfolio", 2 if scrape.has_gallery else 0, 2))
    p += 2 if scrape.has_gallery else 0
    items.append(_item("Video embed", 2 if scrape.has_video_embed else 0, 2))
    p += 2 if scrape.has_video_embed else 0
    has_analytics = bool(scrape.detected_analytics)
    items.append(_item("Analytics installed (GA / Pixel / Hotjar)",
                       2 if has_analytics else 0, 2))
    p += 2 if has_analytics else 0
    items.append(_item("Cookie/consent banner", 1 if scrape.has_cookie_banner else 0, 1))
    p += 1 if scrape.has_cookie_banner else 0
    if scrape.images_total > 0:
        alt_ratio = scrape.images_with_alt / scrape.images_total
    else:
        alt_ratio = 0
    alt_pts = 1 if alt_ratio >= 0.7 else 0
    items.append(_item(f"Image alt-text coverage ({int(alt_ratio*100)}%)",
                       alt_pts, 1))
    p += alt_pts
    sufficient_copy = scrape.body_word_count >= 300
    items.append(_item(f"Substantive copy (≥ 300 words; got {scrape.body_word_count})",
                       2 if sufficient_copy else 0, 2))
    p += 2 if sufficient_copy else 0
    dims.append(DimensionScore("engagement", "Engagement & Content", p, 15, items))

    total = sum(d.score for d in dims)
    grade = "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "D" if total >= 40 else "F"
    return ScoreCard(total=total, grade=grade, dimensions=dims)


# ---------- Recommendations ----------

# Map an "improvement" key to (title template, bucket, oryn_service, base severity)
RECO_BLUEPRINTS = {
    "modern_redesign": ("Redesign with a custom modern site (animations + polish)",
                        "Design", "modern_website", "high"),
    "whatsapp_bot":    ("Add a WhatsApp chat button / bot",
                        "Conversion", "whatsapp_bot", "high"),
    "ai_chatbot":      ("Install an on-site AI chatbot",
                        "Conversion", "ai_chatbot", "medium"),
    "ecommerce":       ("Add an online store / e-commerce checkout",
                        "Conversion", "ecommerce", "high"),
    "online_ordering": ("Add online ordering with payment",
                        "Conversion", "ecommerce", "high"),
    "booking_system":  ("Add an online booking / appointment system",
                        "Conversion", "ai_chatbot", "high"),
    "quote_form":      ("Add an instant quote-request form",
                        "Conversion", "ai_chatbot", "high"),
    "contact_form":    ("Add a real contact form (not just an email link)",
                        "Conversion", "contact_form", "high"),
    "newsletter":      ("Add a newsletter signup",
                        "Engagement", "modern_website", "low"),
    "click_to_call":   ("Add a tap-to-call phone link",
                        "Conversion", "modern_website", "medium"),
    "mobile":          ("Make the site mobile responsive",
                        "Mobile", "modern_website", "high"),
    "seo_meta":        ("Add a title + meta description on every page",
                        "SEO", "modern_website", "medium"),
    "seo_og":          ("Add Open Graph tags for social previews",
                        "SEO", "modern_website", "low"),
    "seo_structured":  ("Add JSON-LD structured data (LocalBusiness / Product / FAQ)",
                        "SEO", "modern_website", "medium"),
    "seo_sitemap":     ("Publish a sitemap.xml + robots.txt",
                        "SEO", "modern_website", "low"),
    "https":           ("Switch the site to HTTPS",
                        "Trust", "modern_website", "high"),
    "testimonials":    ("Add a testimonials / Google-reviews section",
                        "Trust", "modern_website", "medium"),
    "social":          ("Link your social profiles in the footer",
                        "Trust", "modern_website", "low"),
    "address_map":     ("Embed a Google Maps location",
                        "Trust", "modern_website", "low"),
    "analytics":       ("Install Google Analytics so you can actually measure visits",
                        "Engagement", "modern_website", "low"),
    "gallery":         ("Add a gallery / portfolio section",
                        "Engagement", "modern_website", "medium"),
    "blog":            ("Start a blog for SEO + authority",
                        "Engagement", "modern_website", "low"),
    "faq":             ("Add an FAQ section",
                        "Engagement", "ai_chatbot", "low"),
    "video":           ("Add a hero / explainer video",
                        "Engagement", "modern_website", "low"),
    "ctas":            ("Add clearer call-to-action buttons throughout",
                        "Conversion", "modern_website", "medium"),
    "alt_text":        ("Add alt text to every image (accessibility + SEO)",
                        "SEO", "modern_website", "low"),
}

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _add_reco(out: list[Recommendation], key: str, rationale: str,
              severity_override: Optional[str] = None,
              title_override: Optional[str] = None,
              applicable: Optional[set[str]] = None):
    if key not in RECO_BLUEPRINTS:
        return
    title, bucket, svc, base_sev = RECO_BLUEPRINTS[key]
    # filter against category-applicable services where relevant
    if applicable is not None and svc and svc not in applicable and key not in {
        "https", "seo_meta", "seo_og", "seo_structured", "seo_sitemap",
        "testimonials", "social", "address_map", "analytics", "alt_text",
        "click_to_call", "ctas", "mobile",
    }:
        return
    out.append(Recommendation(
        title=title_override or title,
        severity=severity_override or base_sev,
        bucket=bucket,
        rationale=rationale,
        oryn_service=svc,
    ))


def recommend(scrape: ScrapeResult, category: CategoryDetection,
              scorecard: ScoreCard) -> list[Recommendation]:
    cat_def = CATEGORIES.get(category.primary, CATEGORIES[DEFAULT_CATEGORY])
    apps = cat_def.applicable_services

    out: list[Recommendation] = []

    # --- Design / framework ---
    is_dated = ("WordPress" in (scrape.detected_frameworks or [])
                and not scrape.has_modern_design
                and not scrape.detected_animations)
    is_template_builder = any(b in (scrape.detected_frameworks or []) for b in ("Wix", "Squarespace"))
    if not scrape.has_modern_design or is_dated:
        rationale = "Site looks like a stock CMS template — Oryn ships custom, animated builds."
        if "WordPress" in (scrape.detected_frameworks or []):
            rationale = "WordPress with no animation libs / modern stack — feels dated next to competitors."
        elif is_template_builder:
            rationale = "Built on a template builder — limited polish, slow on mobile, hard to customise."
        _add_reco(out, "modern_redesign", rationale)

    # --- Conversion tooling ---
    if not scrape.has_contact_form and "contact_form" in apps:
        _add_reco(out, "contact_form",
                  "No working contact form found — every visitor who wants to ask a question bounces.",
                  applicable=apps)

    if not scrape.has_whatsapp and "whatsapp_bot" in apps:
        _add_reco(out, "whatsapp_bot",
                  f"Standard for {category.primary_label.lower()} businesses in India — most visitors expect to DM on WhatsApp.",
                  applicable=apps)

    if not scrape.has_live_chat:
        # chatbot makes sense for almost any site, but we phrase it per category
        if "ai_chatbot" in apps:
            _add_reco(out, "ai_chatbot",
                      "Capture leads after-hours — chatbot can qualify, book, or hand off to WhatsApp.",
                      applicable=apps)

    # Category-specific conversion tools
    if "booking_system" in apps and not (scrape.has_contact_form and scrape.has_whatsapp):
        _add_reco(out, "booking_system",
                  "Customers in this category typically book online — let them self-serve.",
                  applicable=apps)
    if "quote_form" in apps and not scrape.has_contact_form:
        _add_reco(out, "quote_form",
                  "Visitors want a price fast — an instant quote form converts much better than 'call us'.",
                  applicable=apps)
    if "online_ordering" in apps and not scrape.has_ecommerce:
        _add_reco(out, "online_ordering",
                  "Restaurants without online ordering lose to Swiggy/Zomato margins — a direct order page keeps the cut.",
                  applicable=apps)

    # Plain ecommerce only when the category supports retail
    if "ecommerce" in apps and not scrape.has_ecommerce:
        _add_reco(out, "ecommerce",
                  "Sells physical goods but has no online checkout — leaving money on the table.",
                  applicable=apps)

    if not scrape.has_click_to_call:
        _add_reco(out, "click_to_call",
                  "Phone visible as text but not as a tappable link — kills mobile conversion.")

    if scrape.cta_count < 2:
        _add_reco(out, "ctas",
                  f"Only {scrape.cta_count} clear CTA detected — visitors don't know what to do next.")

    if not scrape.has_newsletter_signup and "newsletter" in apps:
        _add_reco(out, "newsletter",
                  "No way to capture interested visitors who aren't ready to buy yet.",
                  applicable=apps)

    # --- Mobile ---
    if not scrape.has_mobile_responsive:
        _add_reco(out, "mobile",
                  "No mobile-friendly viewport / responsive markup — site breaks on phones.")

    # --- SEO ---
    if not scrape.has_meta_description:
        _add_reco(out, "seo_meta",
                  "Missing meta description — Google may show random page text in search results.")
    if not scrape.has_og_tags:
        _add_reco(out, "seo_og",
                  "Sharing the link on WhatsApp/FB shows no preview image — looks unprofessional.")
    if not scrape.has_structured_data:
        _add_reco(out, "seo_structured",
                  "No structured data — missing rich-result eligibility (reviews, FAQs, business info).")
    if not (scrape.has_sitemap and scrape.has_robots_txt):
        _add_reco(out, "seo_sitemap",
                  "Sitemap/robots not detected — search engines may miss pages.")

    # --- Trust ---
    if not scrape.is_https:
        _add_reco(out, "https",
                  "Site is HTTP, not HTTPS — browsers show 'Not Secure', kills trust instantly.")
    if not scrape.has_testimonials:
        _add_reco(out, "testimonials",
                  "No reviews/testimonials on the page — social proof is the easiest trust win.")
    if len(scrape.social_links) < 2:
        _add_reco(out, "social",
                  "Few or no social links — visitors can't validate the business is real & active.")
    if not scrape.has_address_or_map and any(s in apps for s in ("booking_system", "contact_form")):
        _add_reco(out, "address_map",
                  "No Maps embed — local visitors can't see where you are.")

    # --- Engagement ---
    if not scrape.detected_analytics:
        _add_reco(out, "analytics",
                  "No analytics installed — you're flying blind on visitor behaviour.")
    if not scrape.has_gallery and "gallery" in apps:
        _add_reco(out, "gallery",
                  "Category benefits hugely from visual proof of work / venue / dishes.",
                  applicable=apps)
    if not scrape.has_blog:
        _add_reco(out, "blog",
                  "A simple blog/news section helps rank for long-tail searches and shows you're alive.")
    if not scrape.has_faq:
        _add_reco(out, "faq",
                  "FAQ reduces support load and boosts SEO via FAQ rich results.")
    if not scrape.has_video_embed:
        _add_reco(out, "video",
                  "No video — a 30-second hero clip dramatically lifts time-on-page.")
    if scrape.images_total > 0 and scrape.images_with_alt / scrape.images_total < 0.5:
        _add_reco(out, "alt_text",
                  f"{scrape.images_with_alt}/{scrape.images_total} images have alt text — bad for SEO & screen readers.")

    # Sort: high → medium → low, preserve insertion order otherwise
    out.sort(key=lambda r: SEVERITY_RANK.get(r.severity, 3))
    return out


# ---------- Top-level ----------

def analyze(scrape: ScrapeResult, user_description: str = "") -> Analysis:
    category = categorize(scrape, user_description)
    sc = score(scrape)
    recos = recommend(scrape, category, sc)
    return Analysis(category=category, scorecard=sc, recommendations=recos)
