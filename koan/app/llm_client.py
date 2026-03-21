"""Direct API helper for lightweight text-only LLM operations.

Uses Anthropic's Messages API when available and enabled, with graceful
fallback to existing CLI paths in callers.
"""

import os
import sys
from functools import lru_cache
from typing import Optional


_MODEL_ALIASES = {
    "haiku": "claude-3-5-haiku-latest",
    "sonnet": "claude-3-7-sonnet-latest",
    "opus": "claude-3-opus-latest",
}


def _is_enabled() -> bool:
    """Return whether direct API lightweight calls are enabled."""
    raw = os.environ.get("KOAN_DIRECT_API_LIGHTWEIGHT", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _resolve_model(model: str) -> str:
    """Map shorthand model names to Anthropic model IDs."""
    candidate = (model or "").strip()
    if not candidate:
        return _MODEL_ALIASES["haiku"]
    if candidate.startswith("claude-"):
        return candidate
    return _MODEL_ALIASES.get(candidate.lower(), candidate)


@lru_cache(maxsize=1)
def _load_system_prompt(name: str) -> str:
    from app.prompts import load_prompt

    try:
        return load_prompt(name).strip()
    except Exception as e:
        print(f"[llm_client] System prompt load failed ({name}): {e}", file=sys.stderr)
        return ""


def try_complete_with_api(
    user_prompt: str,
    *,
    model: str = "haiku",
    timeout: int = 30,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    system_prompt_name: str = "lightweight-api-system",
) -> Optional[str]:
    """Try a direct Anthropic API completion and return text or ``None``.

    Callers should treat ``None`` as "use existing CLI path".
    """
    if not _is_enabled():
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    system_text = _load_system_prompt(system_prompt_name) if system_prompt_name else ""
    system_blocks = []
    if system_text:
        system_blocks.append(
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        )

    try:
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_resolve_model(model),
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_blocks if system_blocks else None,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=timeout,
        )
    except Exception as e:
        print(f"[llm_client] Direct API call failed: {e}", file=sys.stderr)
        return None

    parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", "") == "text":
            text = getattr(block, "text", "")
            if text:
                parts.append(text)

    merged = "\n".join(parts).strip()
    if not merged:
        print("[llm_client] Direct API returned empty text output", file=sys.stderr)
        return None
    return merged
