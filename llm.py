"""
LLM provider abstraction.

Two backends, selected from config["llm_provider"]:

  - "anthropic"           — Anthropic Messages API (api key from config or env)
  - "openai_compatible"   — any OpenAI-style /v1/chat/completions endpoint
                            (Ollama, LM Studio, vLLM, llama.cpp server,
                            PewDiePie's Oddyssey, etc.)

All call sites go through `complete(system, user, max_tokens)` and get back
a uniform dict:  {"text", "model", "input_tokens", "output_tokens"}.

Adding a new backend = add a key + a `_call_xxx` function.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import requests

from config import load as load_config


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def complete(*, system: str, user: str, max_tokens: int = 1024,
             temperature: float = 0.7) -> dict:
    cfg = load_config()
    provider = (cfg.get("llm_provider") or "anthropic").lower()
    if provider == "anthropic":
        return _call_anthropic(cfg, system, user, max_tokens, temperature)
    if provider in ("openai_compatible", "openai", "oddyssey", "ollama"):
        return _call_openai_compatible(cfg, system, user, max_tokens, temperature)
    raise RuntimeError(
        f"Unknown llm_provider '{provider}'. "
        f"Set it to 'anthropic' or 'openai_compatible' in Settings."
    )


def current_provider_label() -> str:
    """Short string for UI."""
    cfg = load_config()
    p = (cfg.get("llm_provider") or "anthropic").lower()
    if p == "anthropic":
        return f"Anthropic · {cfg.get('anthropic_model', '?')}"
    return f"OpenAI-compat · {cfg.get('llm_model') or '?'} @ {cfg.get('llm_base_url') or '?'}"


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

def _call_anthropic(cfg: dict, system: str, user: str,
                    max_tokens: int, temperature: float) -> dict:
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "anthropic package not installed. `pip install anthropic`."
        ) from e

    api_key = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key", "")
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key. Open Settings and paste your key, "
            "or switch LLM provider to OpenAI-compatible."
        )
    model = cfg.get("anthropic_model", "claude-sonnet-4-6")

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    return {
        "text":          text,
        "model":         getattr(resp, "model", model),
        "input_tokens":  getattr(resp.usage, "input_tokens", None)
                         if hasattr(resp, "usage") else None,
        "output_tokens": getattr(resp.usage, "output_tokens", None)
                         if hasattr(resp, "usage") else None,
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible backend  (Oddyssey, Ollama, LM Studio, vLLM, ...)
# ---------------------------------------------------------------------------

def _call_openai_compatible(cfg: dict, system: str, user: str,
                            max_tokens: int, temperature: float) -> dict:
    base_url = (cfg.get("llm_base_url") or "").strip().rstrip("/")
    model    = (cfg.get("llm_model") or "").strip()
    api_key  = (cfg.get("llm_api_key") or "").strip()

    if not base_url:
        raise RuntimeError(
            "No base URL set for the OpenAI-compatible LLM. "
            "Open Settings and fill the LLM section."
        )
    if not model:
        raise RuntimeError(
            "No model name set for the OpenAI-compatible LLM. "
            "Open Settings and fill the LLM section."
        )

    # Most servers expose /v1/chat/completions.  If user already pasted that
    # full path, don't double it up.
    if base_url.endswith("/chat/completions"):
        url = base_url
    elif base_url.endswith("/v1"):
        url = base_url + "/chat/completions"
    else:
        url = base_url + "/v1/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }

    try:
        r = requests.post(url, headers=headers,
                          data=json.dumps(payload), timeout=120)
    except requests.RequestException as e:
        raise RuntimeError(
            f"Couldn't reach LLM server at {url}: {e}"
        ) from e

    if r.status_code >= 400:
        raise RuntimeError(
            f"LLM server returned HTTP {r.status_code}: {r.text[:300]}"
        )

    try:
        data = r.json()
    except ValueError as e:
        raise RuntimeError(
            f"LLM server returned non-JSON: {r.text[:300]}"
        ) from e

    text = ""
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        # some servers (older Ollama) put it in {"choices":[{"text":...}]}
        try:
            text = data["choices"][0].get("text", "")
        except (KeyError, IndexError, TypeError):
            text = ""
    text = (text or "").strip()

    usage = data.get("usage") or {}
    return {
        "text":          text,
        "model":         data.get("model", model),
        "input_tokens":  usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
    }
