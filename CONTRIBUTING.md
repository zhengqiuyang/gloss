# Contributing to gloss

Thanks for wanting to make gloss better! This is a young project, so the
fastest way to help is: use it, hit friction, and open an issue describing
what you tried to do.

## Development setup

```bash
git clone https://github.com/YOUR_USERNAME/gloss
cd gloss
uv venv && uv pip install -e ".[dev]"    # or: python -m venv .venv && pip install -e ".[dev]"
.venv/bin/pytest
```

The test suite never touches the network — every LLM call goes through the
`FakeLLM` in `tests/conftest.py`. If you add a feature that calls the LLM,
extend `FakeLLM` instead of mocking HTTP.

## Code layout

```
src/gloss/
  cli.py      click CLI; unknown subcommands fall through to term lookup
  ingest.py   lookup-or-ingest flow (cache first, LLM on miss, link + backfill)
  llm.py      OpenAI-compatible client + prompts + JSON extraction
  db.py       SQLite schema, FTS5 search, undirected links
  export.py   markdown (Obsidian) / JSON export
  ui.py       rich rendering
  config.py   ~/.gloss/config.toml handling
```

## Ground rules

- Python >= 3.9 compatible (`from __future__ import annotations` everywhere).
- No new heavy dependencies without discussion — the appeal of gloss is being small.
- User-facing strings follow the existing style: concise 中文 for UX copy,
  English for code identifiers and comments.
- Every PR adds or updates tests for the behavior it changes.
