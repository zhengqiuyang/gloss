# gloss

**Learn a term once, own it forever.**

English | [简体中文](README.zh-CN.md)

> A vocabulary notebook with an LLM for a heart · local-first · works with any OpenAI-compatible API

[![CI](https://github.com/zhengqiuyang/gloss/actions/workflows/ci.yml/badge.svg)](https://github.com/zhengqiuyang/gloss/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.9%2B-blue)

## You pay for the same word three times

It's 11 PM. You're reading a paper on retrieval-augmented generation when an
acronym blocks the way: **HNSW**.

You switch to the browser, search, skim two blog posts, and get it — layered
graphs, greedy descent, trading a little accuracy for a hundredfold speed.
Back to the paper. Ten minutes later, HNSW appears again. You stare at it for
three seconds: wait… what did I look up about it?

So you search again.

That's not a memory problem — it's a tool-shape problem. Search engines and
chat windows are built for one question at a time: ask, receive, and the
relationship ends. Old-school study tools solved the keeping part long ago —
the vocabulary notebook. Look it up once, write it down, glance at it next
time. Except a notebook has never heard of HNSW, QUIC, or MCP.

**gloss gives the vocabulary notebook an LLM for a heart.** Copy a term, tap
a key, and a card appears: a one-line definition, a few sentences of plain
explanation, related concepts. It lives on your own machine — and you will
never look this word up again. Three months from now, `gloss related RAG`
shows you a graph: not a knowledge map someone else drew, but one your own
reading grew.

## How "once" becomes "forever"

1. **Lookup is caching** — every term is checked against local SQLite first;
   the LLM is called only on a miss, once, ever. Repeats cost 0 tokens,
   0 latency, and work offline.
2. **Cards, not chat bubbles** — aliases, a one-line definition, a short
   explanation, domain tags, where you met it: stored structured, searchable.
3. **Concepts link to concepts** — when a new card mentions a term you already
   own, they're linked automatically; unknown names are parked and back-filled
   into a two-way link when you gloss them later.

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

Every lookup tells you whether it was a `⚡ 0 tokens` cache hit or a fresh
`✨ card` — and `gloss stats` shows the saved tokens compounding.

## 30 seconds (no API key needed)

```bash
uv tool install git+https://github.com/zhengqiuyang/gloss
gloss demo            # 9 interlinked sample cards, every feature works offline
gloss ls              # what the library looks like
gloss related RAG     # how concepts connect
gloss search 向量      # full-text search
gloss review          # last 7 days
```

## Configure (30 seconds, any OpenAI-compatible API)

```bash
gloss config set llm.api_key sk-...
gloss config set llm.base_url https://api.deepseek.com/v1   # DeepSeek, for example
gloss config set llm.model deepseek-chat
```

DeepSeek, GLM, Ollama (local and free), SiliconFlow… change two lines to
switch. Other options (explanation language, detail length, vision model,
hotkey, port) — see `gloss config show`. Config lives in
`~/.gloss/config.toml`; the library is `gloss.db` next to it.

## Zero typing: copy, then one key

Day-to-day usage isn't typing commands — it's **copy → one action → back to
reading**:

| Platform | Daily shape |
|---|---|
| macOS | `gloss app` lives in the menu bar; **⌘G** pops the floating card (`⌘⇧⌃4` screenshots get parsed too); `gp` for one-key capture; `gloss paste --notify` for notification mode |
| Windows | `gloss hotkey` lives in the tray; **Ctrl+G** pops a frameless card (`Win+Shift+S` screenshots likewise) |
| Everywhere | `echo "a passage" \| gloss` digests it into a snippet card; `gloss tr` translates the clipboard; `gloss serve` is a local web popup + HTTP API; `gloss export` writes Obsidian wikilinks / JSON backups |

Hotkey, Raycast, Shortcuts and AutoHotkey recipes live in
[extras/README.md](extras/README.md).

## Platform support

| Layer | Where it runs |
|---|---|
| Core CLI — lookup, glossary, edit/rm, translate, review, stats, export, `gloss serve`, `gloss demo` | **Cross-platform** (any Python 3.9+; runtime deps only click/rich/openai) |
| `gloss paste` (clipboard capture: text + screenshots) | **macOS & Windows**; Linux needs adapters (see `src/gloss/clipboard.py`) |
| `gloss hotkey` (global-hotkey popup + tray) | **Windows only** — frameless WebView2 card; the macOS counterpart is `gloss app` |
| `gloss app` (menu bar + ⌘G popup) | **macOS only** — pyobjc, Carbon hotkey |

## Why "gloss"?

A *gloss* is the note a scribe writes in the margin of a manuscript to explain
a difficult word — the word is Greek in origin, *glōssa*, "tongue, language".
Collect the margins and you have a glossary. gloss does the same thing: it
writes the margin notes for you, and keeps every one of them.

## Roadmap

- [x] Zero-typing capture: `gloss paste` + global hotkeys (⌘G on macOS, Ctrl+G on Windows)
- [x] Local web popup + HTTP API (`gloss serve` — the base for every future surface)
- [x] Menu-bar app (macOS) / tray frameless card (Windows)
- [ ] Semantic search with local embeddings
- [ ] Spaced-repetition review (`gloss quiz`)
- [ ] MCP server so editors/agents can query your glossary
- [ ] WeChat/Telegram bot (built on `gloss serve`'s API)
- [ ] Multi-machine sync

## Privacy

Your glossary is a local SQLite file. Only the term you look up (plus your
existing term list, used for linking) is sent to the LLM provider you
configure. No telemetry, nothing else leaves your machine.

## Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Development is a
standard hatchling project: `uv venv && uv pip install -e ".[dev]"`, then
`pytest`.

## License

[MIT](LICENSE)
