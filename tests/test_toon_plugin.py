"""Unit tests for the TOON plugin core logic (no Hermes host required)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from __init__ import _maybe_convert, _HEADER  # noqa: E402

UNIFORM = json.dumps({
    "users": [
        {"id": i, "name": f"user{i}", "role": "admin" if i % 3 == 0 else "user",
         "email": f"user{i}@example.com", "active": i % 2 == 0}
        for i in range(1, 21)
    ]
})


def test_uniform_json_converts_and_saves():
    out = _maybe_convert(UNIFORM, min_chars=200, min_savings=0.12)
    assert out is not None
    assert out.startswith(_HEADER)
    assert len(out) < len(UNIFORM)


def test_roundtrip_is_lossless():
    from toon_format import loads
    out = _maybe_convert(UNIFORM, min_chars=200, min_savings=0.12)
    data = loads(out[len(_HEADER):])
    assert data == json.loads(UNIFORM)


def test_small_result_untouched():
    assert _maybe_convert('{"a": 1}', min_chars=200, min_savings=0.12) is None


def test_non_json_untouched():
    assert _maybe_convert("x" * 500, min_chars=200, min_savings=0.12) is None


def test_invalid_json_untouched():
    assert _maybe_convert("{not json" + "x" * 300, min_chars=200, min_savings=0.12) is None


def test_scalar_json_untouched():
    assert _maybe_convert('"' + "x" * 300 + '"', min_chars=200, min_savings=0.12) is None


def test_below_savings_threshold_untouched():
    # Impossible threshold forces a skip even on highly compressible data.
    assert _maybe_convert(UNIFORM, min_chars=200, min_savings=0.99) is None


def test_never_double_encodes():
    once = _maybe_convert(UNIFORM, min_chars=200, min_savings=0.12)
    assert _maybe_convert(once, min_chars=200, min_savings=0.12) is None


def test_header_teaches_format():
    assert "TOON" in _HEADER and "table" in _HEADER


# --- Envelope unwrapping ---------------------------------------------------
# Real tool results often arrive as {"output": "<json string>", ...}
# (terminal, execute_code). Whole-result conversion can't save anything on
# these; the payload is the INNER JSON string.

ENVELOPE = json.dumps({
    "output": json.dumps([
        {"name": f"sensor-{i:02d}", "value": i * 3, "active": i % 2 == 0,
         "score": round(i * 1.5, 1)} for i in range(40)
    ]),
    "exit_code": 0,
    "error": None,
})


def test_envelope_inner_json_converts():
    out = _maybe_convert(ENVELOPE, min_chars=200, min_savings=0.12)
    assert out is not None
    assert len(out) < len(ENVELOPE)


def test_envelope_result_stays_valid_json_with_wrapper_keys():
    out = _maybe_convert(ENVELOPE, min_chars=200, min_savings=0.12)
    data = json.loads(out)
    assert data["exit_code"] == 0
    assert data["output"].startswith(_HEADER)


def test_envelope_roundtrip_is_lossless():
    from toon_format import loads
    out = _maybe_convert(ENVELOPE, min_chars=200, min_savings=0.12)
    inner_toon = json.loads(out)["output"]
    assert loads(inner_toon[len(_HEADER):]) == json.loads(json.loads(ENVELOPE)["output"])


def test_envelope_with_prose_output_untouched():
    prose = json.dumps({"output": "hello world, not json " * 30, "exit_code": 0})
    assert _maybe_convert(prose, min_chars=200, min_savings=0.12) is None


def test_envelope_with_low_savings_inner_untouched():
    tiny = json.dumps({"output": json.dumps({"a": "x" * 300}), "exit_code": 0})
    assert _maybe_convert(tiny, min_chars=200, min_savings=0.12) is None


def test_envelope_never_double_encodes():
    once = _maybe_convert(ENVELOPE, min_chars=200, min_savings=0.12)
    assert _maybe_convert(once, min_chars=200, min_savings=0.12) is None


@pytest.mark.parametrize("min_chars", [0, 50])
def test_low_min_chars_still_converts(min_chars):
    big = json.dumps([{"k": "v" * 30, "n": i} for i in range(10)])
    assert _maybe_convert(big, min_chars=min_chars, min_savings=0.05) is not None
