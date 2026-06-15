"""
Claude-powered cold email writer for Oryn.

Takes a lead record (with `scrape` + `analysis` populated by scraper + analyzer)
and generates a short, personalised cold email pitching ONLY services that
make sense for the detected business category.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from config import load as load_config
import llm


ORYN_SERVICES_DESC = """\
Oryn's offerings:
- modern_website     — Custom modern websites with animations and effects
- whatsapp_bot       — WhatsApp button / bot integrated into the website
- ai_chatbot         — On-site AI chatbot (lead capture, FAQ, booking)
- ecommerce          — Full e-commerce setup with checkout
- booking_system     — Online appointment / booking flow
- quote_form         — Instant quote / estimate form
- online_ordering    — Direct order page (for restaurants etc.)
- newsletter         — Newsletter signup + email capture
- web_presence       — General SEO / trust / engagement improvements
"""

SYSTEM_PROMPT = (
    "You are Aether, the founder of Oryn — a small web agency. You're writing a "
    "cold email to a real business whose website you just audited. You write the "
    "way a smart person texts a friend: short, specific, no corporate fluff, no "
    "AI tells.\n\n"

    "HARD RULES\n"
    "----------\n"
    "1. **Length**: 6–9 short sentences. Two or three small paragraphs. Never longer.\n"
    "2. **Opening line**: must prove you actually looked at THEIR site. Reference one "
    "of: their hero/title text, the category you detected, their tech stack, an "
    "exact missing feature, a city/location from their address — something specific. "
    "Generic openings ('I came across your site and was impressed') are forbidden.\n"
    "3. **The gap**: pick the 1 or 2 HIGHEST-priority recommendations and describe "
    "what they're losing because of it (lost leads, missed bookings, calls that "
    "never happen). Don't list a menu of fixes. Pick the sharpest one.\n"
    "4. **Category fit**: only pitch services from `applicable_oryn_services`. Never "
    "suggest ecommerce to a movers / law firm / NGO. Never pitch WhatsApp to a US-only "
    "SaaS. Stay in lane.\n"
    "5. **Proof / demo**: if `demo_ready` is true and `demo_url` is provided, mention "
    "casually that you already built a working mock-up of how their site could look "
    "and offer the link. Don't oversell it.\n"
    "6. **CTA**: soft. A single sentence: a reply or a 15-minute call this week. "
    "No 'jump on a call', no 'circle back', no 'low-hanging fruit'.\n"
    "7. **Signoff**: use the value of `sender_signoff` exactly. No 'best regards', "
    "no 'warmly'.\n"
    "8. **Forbidden words/phrases**: leverage, synergy, unlock, supercharge, "
    "elevate, robust, holistic, cutting-edge, in today's digital landscape, "
    "I hope this email finds you well, I wanted to reach out, just circling back, "
    "to whom it may concern, dear sir/madam. No emojis. No exclamation marks "
    "beyond the subject.\n"
    "9. **Subject line**: lowercase if you want. Curious, specific, 4–8 words. "
    "Reference the business, the gap, or the city. Examples of the right shape: "
    "'quick note on the slam fitness site', 'movers in chennai — one quick fix', "
    "'noticed something on agarwalpackers.com'. Bad: 'Boost Your Online Presence!!'\n"
    "10. **Tone**: like a competent peer who noticed something. Not a salesperson. "
    "Confident but not pushy.\n\n"

    "OUTPUT FORMAT\n"
    "-------------\n"
    "Return STRICT JSON only, no prose around it:\n"
    "{\n"
    '  "subject": "...",\n'
    '  "body": "the full email body, including blank lines between paragraphs and the signoff",\n'
    '  "services_pitched": ["modern_website", "whatsapp_bot", ...]\n'
    "}"
)


def _summarise_lead(lead: dict, demo_url: Optional[str] = None) -> dict:
    """Strip the lead down to just what Claude needs — saves tokens."""
    scrape = lead.get("scrape") or {}
    analysis = lead.get("analysis") or {}
    cat = analysis.get("category") or {}
    sc = analysis.get("scorecard") or {}
    recos = analysis.get("recommendations") or []
    applicable = _applicable_services_for_category(cat.get("primary"))

    text_sample = (scrape.get("text_sample") or "")[:600]

    return {
        "business_name": lead.get("business_name"),
        "website":       lead.get("website"),
        "location":      lead.get("location"),
        "user_description": lead.get("description"),
        "site_title":    scrape.get("title"),
        "site_meta_description": scrape.get("description") or scrape.get("og_description"),
        "site_first_words":      text_sample,
        "detected_category":      cat.get("primary_label"),
        "category_confidence":    cat.get("confidence"),
        "secondary_category":     cat.get("secondary_label"),
        "site_score":             sc.get("total"),
        "site_grade":             sc.get("grade"),
        "tech_stack": scrape.get("detected_frameworks"),
        "ecommerce_platform":     scrape.get("detected_ecommerce_platform"),
        "chat_provider":          scrape.get("detected_chat_provider"),
        "has_whatsapp":           scrape.get("has_whatsapp"),
        "has_live_chat":          scrape.get("has_live_chat"),
        "has_ecommerce":          scrape.get("has_ecommerce"),
        "has_contact_form":       scrape.get("has_contact_form"),
        "has_modern_design":      scrape.get("has_modern_design"),
        "has_mobile_responsive":  scrape.get("has_mobile_responsive"),
        "has_address_or_map":     scrape.get("has_address_or_map"),
        "extracted_addresses":    (scrape.get("extracted_addresses") or [])[:2],
        "social_links_count":     len(scrape.get("social_links") or []),
        "top_recommendations":    [
            {"title": r.get("title"),
             "severity": r.get("severity"),
             "rationale": r.get("rationale"),
             "oryn_service": r.get("oryn_service")}
            for r in recos[:6]
        ],
        "applicable_oryn_services": sorted(applicable),
        "demo_ready":  bool(demo_url),
        "demo_url":    demo_url or "",
    }


def _applicable_services_for_category(category_key: Optional[str]) -> set[str]:
    if not category_key:
        return {"modern_website", "contact_form", "ai_chatbot"}
    try:
        from analyzer import CATEGORIES, DEFAULT_CATEGORY
    except ImportError:
        return {"modern_website", "contact_form", "ai_chatbot"}
    cat = CATEGORIES.get(category_key) or CATEGORIES.get(DEFAULT_CATEGORY)
    return set(cat.applicable_services) if cat else set()


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # fallback: take the first {...} block
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
        raise


def generate_email(lead: dict, sender_name: str = "Aether, Oryn",
                   demo_url: Optional[str] = None) -> dict:
    """
    Returns: {"subject", "body", "services_pitched", "generated_at", "model"}
    Raises:  RuntimeError if the selected LLM provider isn't configured.

    The provider (Anthropic vs OpenAI-compatible / Oddyssey / Ollama) is
    chosen from Settings — see llm.py.
    """
    cfg = load_config()
    sender = cfg.get("from_name") or sender_name

    summary = _summarise_lead(lead, demo_url=demo_url)
    summary["sender_signoff"] = sender
    user_payload = (
        "Here's everything I extracted about the prospect. Write the cold "
        "email exactly per the rules in the system message.\n\n"
        + json.dumps(summary, indent=2, ensure_ascii=False)
    )

    result = llm.complete(
        system=SYSTEM_PROMPT, user=user_payload,
        max_tokens=1024, temperature=0.7,
    )
    data = _extract_json(result["text"])

    return {
        "subject":          data.get("subject", "").strip(),
        "body":             data.get("body", "").strip(),
        "services_pitched": data.get("services_pitched", []),
        "generated_at":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model":            result.get("model", "?"),
        "input_tokens":     result.get("input_tokens"),
        "output_tokens":    result.get("output_tokens"),
    }
