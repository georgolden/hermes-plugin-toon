# hermes-plugin-toon

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that auto-converts JSON tool results to **TOON** ([Token-Oriented Object Notation](https://toonformat.dev)) before they enter the conversation context — saving 30–60% of tokens on uniform/tabular JSON payloads, with zero behavior change.

## What it does

Every tool result passes through the plugin's `transform_tool_result` hook. When a result is:

- valid whole-payload JSON (object or array),
- larger than `min_chars` (default 200),
- and TOON saves at least `min_savings` (default 12%) of its size,

…the model sees the TOON encoding instead of the JSON, prefixed with a two-line legend that teaches the format inline:

```text
[Encoded in TOON (Token-Oriented Object Notation) — same data as JSON, compact form. ...]
users[20]{id,name,role,email,active}:
  1,user1,admin,user1@example.com,false
  2,user2,user,user2@example.com,true
```

Everything else passes through untouched. Any parse/encode failure keeps the original result (fail-open). Conversion is **lossless** — `toon_format.loads` round-trips to identical data.

Also registers two tools for explicit use: `toon_encode` (JSON → TOON) and `toon_decode` (TOON → JSON).

## Why

Tool results (web search/extract, MCP servers, APIs) are some of the largest token consumers in an agent loop, and they're usually uniform JSON — exactly the shape TOON compresses best. This is the same interception pattern the `toolaria` plugin uses for oversized results, applied to encoding instead of spill-to-disk.

## Install

```bash
hermes plugins install georgolden/hermes-plugin-toon --enable
```

Requires the official [`toon-format`](https://pypi.org/project/toon-format/) Python package (declared in the manifest's `python_dependencies`, installed automatically on enable).

## Configuration

Defaults live in `config.yaml` in this repo. Override per-install in `~/.hermes/config.yaml`:

```yaml
toon:
  enabled: true
  min_chars: 200        # don't bother converting small payloads
  min_savings: 0.12     # fraction of chars TOON must save
  exclude_tools: []     # tool names that must never be converted
```

Env overrides (win over config): `TOON_ENABLED=0`, `TOON_MIN_CHARS`, `TOON_MIN_SAVINGS`.

## Privacy / safety

- **No network calls.** Conversion is pure local re-serialization.
- **No files written.** Nothing is stored, logged, or exfiltrated.
- **No data hidden.** TOON encodes exactly the data the model was going to see anyway — it is a compression of the same bytes, not a redaction.

## Development

```bash
pip install -r requirements.txt pytest
pytest tests/ -v
hermes plugins doctor /path/to/hermes-plugin-toon
```

## License

MIT
