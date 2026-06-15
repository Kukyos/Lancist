import json
import os
from paths import CONFIG_FILE

DEFAULTS = {
    # LLM provider selection
    #   "anthropic"          — uses anthropic_api_key + anthropic_model
    #   "openai_compatible"  — uses llm_base_url + llm_model + llm_api_key
    #                          (Ollama / LM Studio / vLLM / Oddyssey / etc.)
    "llm_provider":      "anthropic",

    "anthropic_api_key": "",
    "anthropic_model":   "claude-sonnet-4-6",

    "llm_base_url":      "",         # e.g. http://localhost:11434  (Ollama)
    "llm_model":         "",         # e.g. llama3.1:8b
    "llm_api_key":       "",         # optional; many local servers don't need one

    "from_name":         "Aether, Oryn",
    "from_signature":    "Aether\nOryn",

    "smtp_from_email":   "",
    "smtp_app_password": "",
    "smtp_host":         "smtp.gmail.com",
    "smtp_port":         465,

    # lead-finder defaults (used by lead_finder.py + Find Leads dialog)
    "finder_country":    "in",
    "finder_min_results": 10,
}


def load() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        d = {}
    return {**DEFAULTS, **(d if isinstance(d, dict) else {})}


def save(cfg: dict):
    merged = {**load(), **cfg}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)


def get_api_key() -> str:
    """Legacy: returns Anthropic key. Kept for back-compat."""
    return os.environ.get("ANTHROPIC_API_KEY") or load().get("anthropic_api_key", "")


def get_model() -> str:
    """Legacy: returns Anthropic model. Kept for back-compat."""
    return load().get("anthropic_model", DEFAULTS["anthropic_model"])


def llm_configured() -> bool:
    """True if the currently-selected provider has enough config to attempt a call."""
    cfg = load()
    provider = (cfg.get("llm_provider") or "anthropic").lower()
    if provider == "anthropic":
        return bool(get_api_key())
    return bool(cfg.get("llm_base_url") and cfg.get("llm_model"))
