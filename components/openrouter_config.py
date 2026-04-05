"""
openrouter_config.py
====================
Drop-in LLM client using OpenRouter.
Replaces all direct Anthropic/OpenAI API calls in the system.

OpenRouter is OpenAI-compatible — same /v1/chat/completions endpoint,
just a different base URL and your OpenRouter key as the bearer token.

Recommended free/cheap models on OpenRouter:
  - google/gemini-flash-1.5        (fast, cheap, good)
  - anthropic/claude-3.5-haiku     (best quality for scoring)
  - meta-llama/llama-3.1-8b-instruct (free tier)
  - mistralai/mistral-7b-instruct  (free tier)

Usage:
  from openrouter_config import llm_call, LLM_MODEL
  response = llm_call("Your prompt here", max_tokens=500)
"""

import os
import json
import requests
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Load from env or .env file
def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODEL      = os.getenv("LLM_MODEL", "google/gemini-flash-1.5")  # change as needed

# ── CORE CALL ─────────────────────────────────────────────────────────────────

def llm_call(
    prompt: str,
    system: str = "You are a helpful assistant.",
    max_tokens: int = 1000,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> str:
    """
    Call an LLM via OpenRouter. Returns the response text.
    Raises on HTTP error. Returns empty string on parse failure.
    
    json_mode=True: adds instruction to return only JSON, no markdown fences.
    """
    if not OPENROUTER_KEY:
        raise ValueError(
            "OPENROUTER_API_KEY not set. Add it to your .env file:\n"
            "  OPENROUTER_API_KEY=sk-or-v1-..."
        )

    if json_mode:
        prompt = prompt + "\n\nRespond ONLY with a valid JSON object. No markdown fences, no explanation."

    headers = {
        "Authorization":  f"Bearer {OPENROUTER_KEY}",
        "Content-Type":   "application/json",
        "HTTP-Referer":   "https://github.com/recruitment-system",
        "X-Title":        "Recruitment Automation",
    }

    body = {
        "model":       LLM_MODEL,
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
    }

    try:
        resp = requests.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=headers,
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    except requests.HTTPError as e:
        log.error(f"OpenRouter HTTP error: {e.response.status_code} {e.response.text[:200]}")
        raise
    except Exception as e:
        log.error(f"OpenRouter call failed: {e}")
        raise


def llm_json(prompt: str, system: str = "You are a helpful assistant.", max_tokens: int = 1000) -> dict:
    """Call LLM and parse JSON response. Strips markdown fences if present."""
    raw = llm_call(prompt, system=system, max_tokens=max_tokens, json_mode=True)
    raw = raw.strip()
    # Strip common markdown wrappers
    for fence in ["```json", "```"]:
        if raw.startswith(fence):
            raw = raw[len(fence):]
        if raw.endswith("```"):
            raw = raw[:-3]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}\nRaw: {raw[:300]}")
        return {}


def test_connection() -> bool:
    """Quick check that OpenRouter key is valid."""
    try:
        result = llm_call("Say 'ok' and nothing else.", max_tokens=10)
        log.info(f"OpenRouter connection OK. Response: {result}")
        return True
    except Exception as e:
        log.error(f"OpenRouter connection failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Using model: {LLM_MODEL}")
    print(f"Key loaded: {'yes' if OPENROUTER_KEY else 'NO - set OPENROUTER_API_KEY in .env'}")
    if OPENROUTER_KEY:
        test_connection()
