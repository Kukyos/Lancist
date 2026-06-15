"""
Writes ORYN.md into the demo folder.

Drops the full analysis (category, score, every dimension, every recommendation
with rationale + which Oryn service it maps to) as a plain-text checklist so
you can work through the improvements before sending the demo.
"""
from pathlib import Path
from datetime import datetime, timezone


def write_oryn_md(folder: Path, lead: dict, clone_summary: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    md = _render(lead, clone_summary or {})
    out = folder / "ORYN.md"
    out.write_text(md, encoding="utf-8")
    return out


def _render(lead: dict, clone_summary: dict) -> str:
    name = lead.get("business_name") or "(unnamed lead)"
    site = lead.get("website") or ""
    cat = lead.get("category_label") or "(uncategorised)"
    score = lead.get("score")
    grade = lead.get("grade") or "?"
    analysis = lead.get("analysis") or {}
    sc = analysis.get("scorecard") or {}
    recos = analysis.get("recommendations") or []
    scrape = lead.get("scrape") or {}

    L = []
    L.append(f"# Oryn improvement plan — {name}")
    L.append("")
    L.append(f"- **Original site:** <{site}>")
    L.append(f"- **Category:** {cat}")
    if score is not None:
        L.append(f"- **Current Oryn score:** {score} / 100  (grade {grade})")
    L.append(f"- **Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    if clone_summary.get("folder"):
        L.append(f"- **Demo folder:** `{clone_summary['folder']}`")
    if clone_summary.get("asset_count"):
        L.append(f"- **Assets cloned:** {clone_summary['asset_count']}  "
                 f"·  pages: {len(clone_summary.get('pages') or [])}")
    L.append("")

    L.append("## What was detected on the live site")
    L.append("")
    L.append(_yes_no_table([
        ("Modern design / framework",   scrape.get("has_modern_design")),
        ("Mobile responsive",            scrape.get("has_mobile_responsive")),
        ("WhatsApp button / bot",        scrape.get("has_whatsapp")),
        ("Live chat / AI chatbot",       scrape.get("has_live_chat")),
        ("E-commerce checkout",          scrape.get("has_ecommerce")),
        ("Contact form",                 scrape.get("has_contact_form")),
        ("Address / map",                scrape.get("has_address_or_map")),
        ("Testimonials / reviews",       scrape.get("has_testimonials")),
        ("Blog / news",                  scrape.get("has_blog")),
        ("FAQ",                          scrape.get("has_faq")),
        ("Gallery / portfolio",          scrape.get("has_gallery")),
        ("Newsletter signup",            scrape.get("has_newsletter_signup")),
        ("Analytics installed",          bool(scrape.get("detected_analytics"))),
        ("HTTPS",                        scrape.get("is_https")),
        ("Structured data (JSON-LD)",    scrape.get("has_structured_data")),
    ]))
    L.append("")

    L.append("## Score breakdown")
    L.append("")
    for d in sc.get("dimensions") or []:
        L.append(f"### {d['label']} — {d['score']} / {d['max']}")
        L.append("")
        for it in d.get("items") or []:
            mark = "x" if it.get("met") else " "
            L.append(f"- [{mark}] {it['label']}  ({it['gained']}/{it['possible']})")
        L.append("")

    L.append("## Improvements to ship in this demo")
    L.append("")
    L.append("Work through these in order. Each gets you a measurable score lift "
             "and gives the prospect something concrete to react to.")
    L.append("")

    sev_order = {"high": 0, "medium": 1, "low": 2}
    recos_sorted = sorted(recos, key=lambda r: sev_order.get(r.get("severity", "low"), 9))
    for i, r in enumerate(recos_sorted, 1):
        sev = (r.get("severity") or "low").upper()
        title = r.get("title") or ""
        bucket = r.get("bucket") or ""
        rationale = r.get("rationale") or ""
        svc = r.get("oryn_service") or "—"
        L.append(f"### {i}. [{sev}] {title}")
        L.append(f"*Bucket: {bucket}  ·  Pitches Oryn service: `{svc}`*")
        L.append("")
        L.append(f"> {rationale}")
        L.append("")
        L.append("- [ ] Implemented in this demo")
        L.append("")

    L.append("---")
    L.append("")
    L.append("## How to use this folder")
    L.append("")
    L.append("1. Open `index.html` in a browser to see the current state of their site.")
    L.append("2. Edit the HTML / CSS / JS in place. Save. Refresh.")
    L.append("3. Replace stock assets in `assets/` if needed.")
    L.append("4. When the demo looks convincing, host it (Vercel, Netlify, "
             "or just a static `python -m http.server`) and paste the link "
             "into the cold email.")
    L.append("")
    L.append("> Tip: don't redesign everything. Pick the 2–3 HIGH-severity items "
             "above; that's enough to make the prospect feel the difference.")
    L.append("")
    return "\n".join(L)


def _yes_no_table(rows: list[tuple[str, bool]]) -> str:
    lines = ["| Feature | Detected |", "| --- | --- |"]
    for label, present in rows:
        mark = "yes" if present else "**no**"
        lines.append(f"| {label} | {mark} |")
    return "\n".join(lines)
