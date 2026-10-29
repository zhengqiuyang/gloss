# gloss

**查一次，永久拥有。**

`gloss` 是一个本地优先、AI 驱动的个人术语表。看文档时遇到不认识的名词，不用再开新标签页去搜——丢给它：

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

卡片存进本地 SQLite。下次再遇到 `RAG`，查询是一次瞬时的 **缓存命中——0 token、0 延迟**。
日积月累，gloss 会变成你的「名词解释缓存」和一张只有你拥有的概念图谱——这是任何聊天窗口都给不了你的东西。

> **还没有 API key？** 运行 `gloss demo` 导入一套示例词库（9 张互相关联的卡片），
> 离线体验所有浏览功能——`gloss ls`、`gloss RAG`、`gloss related RAG`、
> `gloss search 向量`、`gloss review`。不想要了随时 `gloss demo --clear`。

## 为什么做这个

问三次聊天机器人"RAG 是什么"，你付三次钱，答案蒸发三次。真正贵重的步骤不是"问"，而是"留住"。所以 gloss 把工作流反过来：

1. **查询即缓存** — 每个名词先查本地库；只有未命中时才调一次模型，终身一次。
2. **卡片，而不是聊天气泡** — 每条解释都是结构化的：别名、一句话定义、简短详情、领域标签、相关概念、遇到它的语境。
3. **概念连着概念** — 新卡片提到的词如果你已经收录过，自动建立关联；还没收录的先挂起，等哪天你 gloss 了它，自动回填成双向链接。你的术语表会长成一张图。

## 功能

- 📖 **术语查询**：别名、一句话定义、简明详情、领域标签
- ⚡ **本地缓存**：重复查询零成本、可离线
- 🔗 **自动关联**：相关概念互相链接，未收录的名字自动挂起、后续回填
- 📝 **管道输入**：`pbpaste | gloss` 把一段话消化成摘要卡片，并提示值得单独收录的术语
- 📋 **`gloss paste`**：免打字——直接读剪贴板，配合 `alias gp='gloss paste'` + [全局快捷键方案](extras/README.md)，复制即收录
- 🖼 **`gloss img screenshot.png`**：用视觉模型解析截图/图表
- 🔍 **全文搜索**（SQLite FTS5），覆盖词条、详情、标签
- 📅 **`gloss review`**：本周新学复盘，按领域分组
- 📤 **导出 Obsidian**：每张卡片一个 markdown 文件、带 `[[双链]]`；也支持完整 JSON 备份
- 🤝 **任意 OpenAI 兼容服务**：OpenAI、DeepSeek、GLM、Ollama、硅基流动……改两个配置就能用

## 安装

```bash
uv tool install git+https://github.com/YOUR_USERNAME/gloss
# 或
pipx install git+https://github.com/YOUR_USERNAME/gloss
```

## 配置

指向任意 OpenAI 兼容接口：

```bash
export GLOSS_API_KEY=sk-...                 # 或 OPENAI_API_KEY
gloss config set llm.base_url https://api.deepseek.com/v1   # 以 DeepSeek 为例
gloss config set llm.model deepseek-chat
```

配置在 `~/.gloss/config.toml`（`gloss config show` 查看），数据库在 `~/.gloss/gloss.db`。用环境变量 `GLOSS_HOME` 可以换数据目录。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `llm.base_url` | `https://api.openai.com/v1` | 任意 OpenAI 兼容接口 |
| `llm.model` | `gpt-4o-mini` | 生成卡片用的对话模型 |
| `llm.vision_model` | 回退到 `llm.model` | `gloss img` 用的视觉模型 |
| `glossary.language` | `中文` | 解释语言 |
| `glossary.detail_max_chars` | `160` | 详情字段长度预算 |

## 用法

```bash
gloss demo                        # 导入示例词库（无需 API key）
gloss paste                       # 直接读剪贴板：文字 → 词条/摘要，截图 → 视觉解析
gloss QUIC                        # 查询/收录一个术语
gloss RAG -n "读 agent 文档时" -s "https://…"   # 记录语境和来源
gloss RAG --refresh               # 忽略缓存重新生成，保留历史
gloss show RAG --json             # 只读本地，绝不调模型
gloss ls                          # 浏览词库
gloss search 向量                  # 全文搜索
gloss related RAG                 # 查看概念关联
gloss review                      # 本周复盘
gloss stats                       # 卡片数、缓存命中、token 消耗
echo "一段长文字" | gloss         # 消化成摘要卡片
pbpaste | gloss                   # 剪贴板内容直接消化
gloss img diagram.png             # 解析一张图片
gloss export --format md --out ~/vault/Glossary   # 导出 Obsidian
gloss export --format json --out ~/backups/gloss.json
```

每次查询都会告诉你是缓存命中（`⚡ 0 tokens`）还是新收录（`✨ 已收录`），`gloss stats` 里能看到复利的省。

## 关联机制

gloss 在生成卡片时会把**你已有的词条名**作为 `known_terms` 一起给模型，并要求它在 `related` 里精确复用这些拼写，于是：

- related 中能匹配到已有卡片的 → 直接存为**关联**（无向边）
- 匹配不到的 → 挂起在该卡片上；将来你 gloss 了这个词，**自动回填**建链

`gloss related RAG` 能同时看到两种。

## 隐私

词库就是本地一个 SQLite 文件。只有你查询的词（加上用于关联的已有词表）会发给你配置的 LLM 服务商，没有其他任何外发，也没有遥测。

## Roadmap

- [ ] 本地 embedding 语义搜索（按意思找卡片，而不是关键词）
- [ ] 间隔复习模式（`gloss quiz`）
- [ ] macOS 全局快捷键面板、浏览器插件捕获
- [ ] MCP server，让编辑器/agent 能查你的词库
- [ ] 微信/Telegram bot 入口

## 参与

欢迎 PR，见 [CONTRIBUTING.md](CONTRIBUTING.md)。开发是标准 hatchling 项目：`uv venv && uv pip install -e ".[dev]"`，然后 `pytest`。

## 许可

[MIT](LICENSE)
