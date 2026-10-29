# gloss

**Learn a term once, own it forever.**

`gloss` is a local-first, AI-powered personal glossary. When you hit an unfamiliar
term while reading, don't open a new tab — gloss it:

```console
$ gloss RAG
╭─ 📖 RAG ────────────────────────────────────────────────╮
│  aka Retrieval-Augmented Generation / 检索增强生成      │
│                                                         │
│ 让模型先检索资料再作答的技术。                           │
│                                                         │
│ 先从外部知识库检索相关片段，再拼进上下文交给大模型生成   │
│ 答案，能显著减少幻觉。                                   │
│                                                         │
│ #machine-learning #nlp                                  │
│  🔗 Embedding  (已关联)                                 │
│  · 向量数据库  (未收录，gloss 它即可建立关联)            │
╰─────────────────────────────────────────────────────────╯
首次记录 2026-10-29 · 来源 https://docs.example.com/agents
```

The card is saved into your local SQLite database. Next time you (or any of your
machines) meet `RAG`, the lookup is an instant **cache hit — 0 tokens, 0 latency**.
Over time, gloss becomes a term-explanation cache and a personal knowledge map
that no chat window can give you.

> **No API key yet?** `gloss demo` seeds a sample glossary (9 cards, linked) so
> you can try every read command offline — `gloss ls`, `gloss RAG`,
> `gloss related RAG`, `gloss search 向量`, `gloss review`. Clean up later with
> `gloss demo --clear`.

## Why

Ask a chatbot "what is RAG?" three times and you pay three times, and the answer
evaporates three times. The expensive, useful step isn't *asking* — it's *keeping*.
So gloss inverts the workflow:

1. **Query-as-cache** — every term is looked up locally first; the LLM is only
   called on a miss, once, ever.
2. **Cards, not chat bubbles** — each explanation is a structured card: aliases,
   one-liner, short detail, tags, related concepts, where you met it.
3. **Concepts link to concepts** — when a new card mentions a term you already
   own, gloss links them automatically (and back-links the other direction when
   you gloss it later). Your glossary grows into a graph.

## Features

- 📖 **Term lookup** with aliases, one-liner, concise detail, domain tags
- ⚡ **Local-first cache** — repeat lookups cost nothing, work offline
- 🔗 **Auto-linking** between related concepts, with pending-name backfill
- 📝 **Pipe anything in**: `pbpaste | gloss` digests a paragraph into a snippet
  card and suggests key terms worth glossing next
- 🖼 **`gloss img screenshot.png`** — parse an image (screenshot, diagram) via a
  vision model
- 🔍 **Full-text search** (SQLite FTS5) across terms, details and tags
- 📅 **`gloss review`** — "what did I learn this week", grouped by domain
- 📤 **Export to Obsidian** — one markdown file per card with `[[wikilinks]]`,
  or a full JSON backup
- 🤝 **Any OpenAI-compatible provider**: OpenAI, DeepSeek, GLM, Ollama,
  SiliconFlow, … — just set `base_url` + `model`

## Install

```bash
# from source (recommended while pre-release)
uv tool install git+https://github.com/YOUR_USERNAME/gloss
# or
pipx install git+https://github.com/YOUR_USERNAME/gloss
```

## Configure

Point it at any OpenAI-compatible endpoint:

```bash
export GLOSS_API_KEY=sk-...                 # or OPENAI_API_KEY
gloss config set llm.base_url https://api.deepseek.com/v1   # e.g. DeepSeek
gloss config set llm.model deepseek-chat
```

Config lives at `~/.gloss/config.toml` (`gloss config show` to inspect; the
database is `~/.gloss/gloss.db`). Override the data directory with `GLOSS_HOME`.

| key | default | meaning |
|---|---|---|
| `llm.base_url` | `https://api.openai.com/v1` | any OpenAI-compatible endpoint |
| `llm.model` | `gpt-4o-mini` | chat model used for glossing |
| `llm.vision_model` | *(falls back to `llm.model`)* | model for `gloss img` |
| `glossary.language` | `中文` | language of explanations |
| `glossary.detail_max_chars` | `160` | length budget for the detail field |

## Usage

```bash
gloss demo                        # seed a sample glossary (no API key needed)
gloss QUIC                        # look up / ingest a term
gloss RAG -n "读 agent 文档时" -s "https://…"   # record context + source
gloss RAG --refresh               # re-ask the model, keep history
gloss show RAG --json             # read locally, never calls the model
gloss ls                          # browse the library
gloss search 向量                  # full-text search
gloss related RAG                 # inspect the concept graph
gloss review                      # weekly digest, grouped by domain
gloss stats                       # card count, cache hits, tokens spent
echo "long text…" | gloss         # digest pasted text into a snippet card
pbpaste | gloss                   # same, from the clipboard
gloss img diagram.png             # digest an image
gloss export --format md --out ~/vault/Glossary   # Obsidian-ready markdown
gloss export --format json --out ~/backups/gloss.json
```

Every result tells you whether it was a cache hit (`⚡ 0 tokens`) or a fresh
card (`✨ 已收录`), so you can watch the compounding savings in `gloss stats`.

## How linking works

When glossing a term, gloss sends your existing term names as `known_terms`.
The model is instructed to reuse those exact spellings in `related`, so:

- related names that match existing cards → stored as **links** (undirected)
- names that don't exist yet → stored as **pending** on the card; when you gloss
  that term later, the link is **back-filled** automatically

`gloss related RAG` shows both kinds.

## Privacy

Your glossary is a local SQLite file. Only the term you look up (plus your
existing term list, used for linking) is sent to the LLM provider you
configure. Nothing is sent anywhere else, and no telemetry exists.

## Roadmap

- [ ] Semantic search with local embeddings (find cards by meaning, not keywords)
- [ ] Spaced-repetition review mode (`gloss quiz`)
- [ ] Global hotkey panel (macOS) and browser-extension capture
- [ ] MCP server so editors/agents can query your glossary
- [ ] WeChat/Telegram bot entry point

## Contributing

PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Development is a
standard hatchling project: `uv venv && uv pip install -e ".[dev]"`, then
`pytest`.

## License

[MIT](LICENSE)
