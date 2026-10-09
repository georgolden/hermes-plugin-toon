"""TOON plugin for Hermes Agent.

Auto-converts JSON tool results to TOON (Token-Oriented Object Notation,
https://toonformat.dev) before they enter the conversation context, saving
30-60% of tokens on uniform/tabular JSON payloads.

Mechanism (pattern borrowed from the toolaria plugin): the
``transform_tool_result`` hook fires on every tool result before it is
stored in context; returning a string replaces the result, returning None
leaves it untouched. Conversion is lossless (``toon_format.loads`` round-
trips to identical data) and fail-open: any parse/encode problem leaves
the original result untouched.

Also registers ``toon_encode`` / ``toon_decode`` tools for explicit use.

No network calls. No files written. No secrets touched — conversion is
pure re-serialization of data the model was going to see anyway.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CWD = Path(__file__).resolve().parent

# Inline legend so the model can read TOON without any system-prompt change.
# Kept to two lines; paid once per converted result, still far cheaper than
# the JSON it replaces.
_HEADER = (
    "[Encoded in TOON (Token-Oriented Object Notation) — same data as JSON, "
    "compact form. Objects are 'key: value' lines; 'name[N]{f1,f2}:' starts a "
    "table of N rows with fields f1,f2, one row per line.]\n"
)

_DEFAULTS = {
    "min_chars": 200,
    "min_savings": 0.12,
    "exclude_tools": [],
    "enabled": True,
}

_cfg: dict = dict(_DEFAULTS)


def _load_defaults() -> dict:
    """Plugin-local config.yaml as the defaults layer (borrowed from toolaria)."""
    try:
        import yaml
        raw = yaml.safe_load((_CWD / "config.yaml").read_text())
        return dict(raw.get("toon", {}))
    except Exception:
        return {}


def _merge_cfg(user_cfg: dict) -> dict:
    cfg = dict(_DEFAULTS)
    cfg.update(_load_defaults())
    if user_cfg:
        cfg.update(user_cfg)
    # Env overrides win last (ops escape hatch).
    if os.environ.get("TOON_MIN_CHARS"):
        try:
            cfg["min_chars"] = int(os.environ["TOON_MIN_CHARS"])
        except ValueError:
            pass
    if os.environ.get("TOON_MIN_SAVINGS"):
        try:
            cfg["min_savings"] = float(os.environ["TOON_MIN_SAVINGS"])
        except ValueError:
            pass
    if os.environ.get("TOON_ENABLED", "").strip().lower() in {"0", "false", "off", "no"}:
        cfg["enabled"] = False
    return cfg


def _safe_cfg(ctx) -> dict:
    """Read user config from PluginContext, config.yaml fallback (toolaria pattern)."""
    try:
        cfg = ctx.config.get("toon")
        if cfg:
            return dict(cfg)
    except Exception:
        pass
    try:
        import yaml
        hp = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        cf = hp / "config.yaml"
        if cf.exists():
            raw = yaml.safe_load(cf.read_text())
            return dict(raw.get("toon", {}))
    except Exception:
        pass
    return {}


def _dumps(data) -> str:
    """Encode with the official TOON library, imported lazily so a missing
    dependency degrades the plugin to a no-op instead of breaking the host."""
    from toon_format import dumps
    return dumps(data)


def _convert_inner_strings(data: dict, min_chars: int, min_savings: float) -> dict | None:
    """Envelope fallback: convert string fields that are themselves JSON payloads.

    Tools like terminal wrap their payload as {"output": "<json string>", ...}
    — whole-result TOON saves nothing on that shape, the payload is the inner
    string. Returns a new dict with inner fields TOON-encoded (each carrying
    the legend header), or None when nothing inner qualified.
    """
    out = dict(data)
    changed = False
    for key, val in data.items():
        if not isinstance(val, str):
            continue
        inner = _maybe_convert(val, min_chars, min_savings)
        if inner is not None:
            out[key] = inner
            changed = True
    return out if changed else None


def _maybe_convert(result: str, min_chars: int, min_savings: float) -> str | None:
    """Return TOON replacement for a JSON tool result, or None to keep original."""
    s = result.strip()
    if len(s) < min_chars:
        return None
    # Already converted (defensive: never double-encode).
    if s.startswith("[Encoded in TOON"):
        return None
    # Whole-result JSON only in v1 — embedded JSON blocks stay untouched.
    if not (s.startswith("{") or s.startswith("[")):
        return None
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        return None
    # Scalars/tiny structures gain nothing and cost the header.
    if not isinstance(data, (dict, list)):
        return None
    try:
        toon = _dumps(data)
    except Exception:
        logger.debug("toon: encode failed, keeping JSON", exc_info=True)
        return None
    savings = 1.0 - (len(toon) / len(s))
    if savings >= min_savings:
        return _HEADER + toon
    # Whole-result TOON didn't pay. Envelope fallback: if this is a dict with
    # JSON payloads in string fields, convert those and keep the JSON wrapper.
    if isinstance(data, dict):
        unwrapped = _convert_inner_strings(data, min_chars, min_savings)
        if unwrapped is not None:
            envelope = json.dumps(unwrapped, ensure_ascii=False)
            if 1.0 - (len(envelope) / len(s)) >= min_savings:
                return envelope
    return None


def _on_transform(
    tool_name: str = "",
    result: str = "",
    args: dict | None = None,
    session_id: str = "",
    **kwargs,
) -> str | None:
    """transform_tool_result hook — replace JSON results with TOON when it pays."""
    if not _cfg.get("enabled", True):
        return None
    if tool_name in set(_cfg.get("exclude_tools") or []):
        return None
    if not result or not isinstance(result, str):
        return None
    try:
        return _maybe_convert(
            result,
            int(_cfg.get("min_chars", _DEFAULTS["min_chars"])),
            float(_cfg.get("min_savings", _DEFAULTS["min_savings"])),
        )
    except Exception:
        # Fail open — a broken converter must never eat a tool result.
        logger.warning("toon: transform failed for %s", tool_name, exc_info=True)
        return None


def _tool_encode(arguments: dict, **kwargs) -> dict:
    """toon_encode tool: JSON string/object -> TOON string."""
    raw = arguments.get("data")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {"error": "data is a string but not valid JSON"}
    try:
        return {"toon": _dumps(raw)}
    except Exception as exc:
        return {"error": f"encode failed: {exc}"}


def _tool_decode(arguments: dict, **kwargs) -> dict:
    """toon_decode tool: TOON string -> JSON string."""
    raw = arguments.get("toon", "")
    try:
        from toon_format import loads
        return {"json": json.dumps(loads(raw), ensure_ascii=False)}
    except Exception as exc:
        return {"error": f"decode failed: {exc}"}


def register(ctx):
    global _cfg
    _cfg = _merge_cfg(_safe_cfg(ctx))

    ctx.register_hook("transform_tool_result", _on_transform)

    ctx.register_tool(
        name="toon_encode",
        toolset="toon",
        schema={
            "name": "toon_encode",
            "description": (
                "Encode JSON data as TOON (Token-Oriented Object Notation), "
                "the compact token-saving serialization. Use when you need to "
                "show structured data compactly or prepare data for another LLM."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "description": "JSON object/array, or a JSON string, to encode."
                    }
                },
                "required": ["data"],
            },
        },
        handler=_tool_encode,
    )
    ctx.register_tool(
        name="toon_decode",
        toolset="toon",
        schema={
            "name": "toon_decode",
            "description": "Decode a TOON string back to JSON.",
            "parameters": {
                "type": "object",
                "properties": {
                    "toon": {"type": "string", "description": "TOON-encoded text."}
                },
                "required": ["toon"],
            },
        },
        handler=_tool_decode,
    )
    logger.info(
        "toon plugin registered (enabled=%s, min_chars=%s, min_savings=%s)",
        _cfg.get("enabled"), _cfg.get("min_chars"), _cfg.get("min_savings"),
    )
