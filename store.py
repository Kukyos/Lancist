import json
from datetime import datetime, timezone

from paths import LEADS_FILE


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_url(u: str) -> str:
    return (u or "").strip().lower().rstrip("/")


def load_leads():
    if not LEADS_FILE.exists():
        return []
    try:
        with open(LEADS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def save_leads(leads):
    tmp = LEADS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)
    tmp.replace(LEADS_FILE)


def upsert_lead(lead: dict) -> dict:
    leads = load_leads()
    key = _norm_url(lead.get("website", ""))
    idx = -1
    if key:
        for i, x in enumerate(leads):
            if _norm_url(x.get("website", "")) == key:
                idx = i
                break

    now = _now()
    if idx >= 0:
        merged = {**leads[idx], **lead}
        merged["updated_at"] = now
        leads[idx] = merged
        result = merged
    else:
        lead = {**lead}
        lead.setdefault("created_at", now)
        lead["updated_at"] = now
        lead.setdefault("status", "new")
        leads.append(lead)
        result = lead

    save_leads(leads)
    return result


def delete_lead(website: str) -> bool:
    leads = load_leads()
    key = _norm_url(website)
    new_leads = [x for x in leads if _norm_url(x.get("website", "")) != key]
    if len(new_leads) == len(leads):
        return False
    save_leads(new_leads)
    return True
