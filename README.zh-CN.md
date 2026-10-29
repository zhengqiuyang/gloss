# gloss

**查一次，永久拥有。**

[English](README.md) | 简体中文

> 给生词本换上一颗大模型的心脏 · 本地优先 · 任意 OpenAI 兼容服务

[![CI](https://github.com/zhengqiuyang/gloss/actions/workflows/ci.yml/badge.svg)](https://github.com/zhengqiuyang/gloss/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.9%2B-blue)

## 同一个词，你付了三次钱

晚上十一点，你在读一篇讲检索增强的文档。第三段，一个缩写挡住了你：**HNSW**。

你切开浏览器，搜索，读了两篇博客，懂了——分层图、粗跳细找、拿一点准确率换一百倍
速度。切回文档继续读。十分钟后，HNSW 又出现了。你盯着它看了三秒：刚才……查了什么
来着？

于是你又搜了一遍。

这不是记性的问题，是工具形状的问题。搜索引擎和聊天窗口都是为"一次一问"设计的：
问完即散，什么也攒不下来。而所有老式学习工具里，其实早有人解决了"留住"：生词本。
查过一次，抄下来，下次遇见翻一眼——只是它不认识 HNSW、QUIC、MCP。

**gloss 给生词本换上大模型的心脏。** 复制一个词，敲一下，一张中文卡片立在那里：
一句话定义、几句人话解释、相关概念。它存在你自己的电脑里——这个词，你只查这一次。
三个月后 `gloss related RAG` 会给你一张网：不是别人画给你看的知识图谱，
是你的阅读自己长出来的。

## 它怎么做到"查一次，永久拥有"

1. **查询即缓存** — 每个词先查本地 SQLite，未命中才调一次模型，终身一次。
   此后 0 token、0 延迟、可离线。
2. **卡片，而不是聊天气泡** — 别名、一句话定义、简短详情、领域标签、来源语境，
   结构化地存着，随时全文检索。
3. **概念连着概念** — 新卡片提到你已收录的词，自动建链；没收录的先挂起，
   将来 gloss 它时自动回填成双向链接。

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

每次查询都会告诉你是 `⚡ 0 tokens` 的缓存命中，还是 `✨ 已收录` 的新卡——
`gloss stats` 里能看着省下的 token 复利增长。

## 30 秒上手（不需要 API key）

```bash
uv tool install git+https://github.com/zhengqiuyang/gloss
gloss demo            # 导入 9 张互相关联的示例卡，离线体验全部功能
gloss ls              # 这份词库长什么样
gloss related RAG     # 概念怎么连成网
gloss search 向量      # 全文搜索
gloss review          # 近 7 天复盘
```

## 配置（30 秒，任意 OpenAI 兼容服务）

```bash
gloss config set llm.api_key sk-...
gloss config set llm.base_url https://api.deepseek.com/v1   # DeepSeek 为例
gloss config set llm.model deepseek-chat
```

DeepSeek、GLM、Ollama（本地、免费）、硅基流动……改两行就能换。其他可调项
（解释语言、详情长度、视觉模型、热键、端口）用 `gloss config show` 查看。
配置在 `~/.gloss/config.toml`，词库是同目录的 `gloss.db`。

## 零打字：复制即收录

日常的最佳用法不是敲命令，是**复制 → 一个动作 → 继续阅读**：

| 平台 | 日常形态 |
|---|---|
| macOS | `gloss app` 菜单栏常驻，**⌘G** 呼出浮窗（`⌘⇧⌃4` 截图直接解析）；`gp` 一键收录；`gloss paste --notify` 通知模式 |
| Windows | `gloss hotkey` 托盘常驻，**Ctrl+G** 呼出无边框卡片（`Win+Shift+S` 截图同理） |
| 全平台 | `echo "一段话" \| gloss` 消化成摘要卡；`gloss tr` 翻译剪贴板；`gloss serve` 网页弹窗 + HTTP API；`gloss export` 导出 Obsidian 双链 / JSON 备份 |

热键、Raycast、快捷指令、AutoHotkey 等配方见 [extras/README.md](extras/README.md)。

## 平台支持

| 层 | 可运行范围 |
|---|---|
| 核心 CLI——查询/词库/edit/rm/翻译/review/stats/export/`gloss serve`/`gloss demo` | **全平台**（Python 3.9+，运行时依赖仅 click/rich/openai） |
| `gloss paste`（剪贴板捕获：文字 + 截图） | **macOS & Windows**；Linux 需适配层（见 `src/gloss/clipboard.py`） |
| `gloss hotkey`（全局热键弹窗 + 托盘） | **仅 Windows**——WebView2 无边框卡片；macOS 对应能力是 `gloss app` |
| `gloss app`（菜单栏 + ⌘G 浮窗） | **仅 macOS**——依赖 pyobjc、Carbon 热键 |

## 为什么叫 gloss

*gloss* 是中世纪手抄本页边上，抄写员为难词写的小注——词源是希腊语 *glōssa*
（舌头、语言）。把页边的注汇编起来，就是 glossary（术语表）。gloss 做的是同一
件事：替你写页边注，并把每一条都留下来。

## Roadmap

- [x] 零打字捕获：`gloss paste` + 全局热键（macOS ⌘G / Windows Ctrl+G）
- [x] 本地 Web 弹窗 + HTTP API（`gloss serve`，一切未来入口的底座）
- [x] 菜单栏应用（macOS）/ 托盘无边框卡片（Windows）
- [ ] 本地 embedding 语义搜索（按意思找卡片）
- [ ] 间隔复习（`gloss quiz`）
- [ ] MCP server，让编辑器/agent 查你的词库
- [ ] 微信/Telegram bot（基于 `gloss serve` 的 API）
- [ ] 多机词库同步

## 隐私

词库就是本地一个 SQLite 文件。只有你查询的词（加上用于关联的已有词表）会发给
你配置的 LLM 服务商，没有遥测，没有其他外发。

## 参与

欢迎 PR，见 [CONTRIBUTING.md](CONTRIBUTING.md)。开发是标准 hatchling 项目：
`uv venv && uv pip install -e ".[dev]"`，然后 `pytest`。

## 许可

[MIT](LICENSE)
