"""
CSV export of leads/emails for record-keeping and SMTP sending later.
"""
import csv
from paths import EMAILS_CSV

FIELDS = [
    "business_name", "email", "website", "category", "score", "grade",
    "status", "subject", "body", "services_pitched",
    "generated_at", "sent_at", "updated_at",
]


def export_all(leads: list[dict]):
    rows = [_lead_to_row(l) for l in leads if l.get("email_subject")]
    with open(EMAILS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)


def _lead_to_row(lead: dict) -> dict:
    return {
        "business_name": lead.get("business_name", ""),
        "email":         lead.get("email", ""),
        "website":       lead.get("website", ""),
        "category":      lead.get("category_label", ""),
        "score":         lead.get("score", ""),
        "grade":         lead.get("grade", ""),
        "status":        lead.get("status", ""),
        "subject":       lead.get("email_subject", ""),
        "body":          lead.get("email_body", ""),
        "services_pitched": ", ".join(lead.get("email_services_pitched", []) or []),
        "generated_at":  lead.get("email_generated_at", ""),
        "sent_at":       lead.get("email_sent_at", ""),
        "updated_at":    lead.get("updated_at", ""),
    }
