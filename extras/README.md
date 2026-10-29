# 零打字方案：从"敲命令"到"热键弹窗"

日常的最佳体验不是敲 `gloss <词>`，而是：**复制 → 一个动作出卡片 → 继续阅读**。
本页按实现成本从低到高给方案，按需取用。

## L3 · 菜单栏应用（体验天花板，推荐）

```bash
uv pip install 'gloss-cli[app]'   # 一次性安装 pyobjc（几 MB）
gloss app                         # 菜单栏出现 📖，弹窗自动打开
```

之后任何时候：⌥⌘G → 卡片浮在鼠标附近（自动预填剪贴板，可编辑）→ 回车查询 →
Esc 关窗回到阅读。应用自带常驻服务，不需要再跑 `gloss serve`。
`gloss app --hidden` 启动时不弹窗；`--no-hotkey` 关闭全局热键。

## L0 · 命令打到最短（必做，1 分钟）

```bash
# 加进 ~/.zshrc
alias g='gloss'
alias gp='gloss paste'
```

## L1 · 通知模式（不打断阅读，推荐日常用）

`gloss paste --notify`：收录后不走终端面板，改发一条 **macOS 系统通知**
（标题是词，正文是一句话定义，卡片照常入库）：

```bash
gloss paste --notify
# 建议 alias：alias gn='gloss paste --notify'
```

配任何热键方案（见下），体验就是：⌘C → 热键 → 眼角扫一眼通知 → 继续读。
想看详情再 `gloss <词>`。

## L2 · 本地网页弹窗（不需要 pyobjc 的替代品）

```bash
gloss serve                # 启动后自动打开 http://127.0.0.1:7789
```

页面会**自动预填剪贴板内容**（可改可删，想查别的词直接改），回车出卡片；
卡片里的关联词和"建议收录"都是可点击的，点一下就地继续查。`Esc` 关窗。

只监听 127.0.0.1，不出本机。端口可在配置里改：`gloss config set serve.port 7790`。
注意 `gloss serve` 停止后页面就会拒绝连接——要常驻请用 `gloss app`。

## 把它挂到全局快捷键（三选一）

### 1. macOS 快捷指令（零安装）

1. 「快捷指令」App → 新建 → 添加「运行 Shell 脚本」（Shell 选 zsh）：
   - 通知版：`$HOME/.local/bin/gloss paste --notify`
   - 弹窗版：`open -a "Google Chrome" --args --app=http://127.0.0.1:7789`，
     前提是先在终端跑着 `gloss serve`
2. 快捷指令详情 → 「添加键盘快捷键」（如 ⌥⌘G）

### 2. Raycast

- `extras/raycast/gloss-paste.sh` 已备好（面板内显示结果），拷进
  Raycast 设置 → Extensions → Script Commands 目录，设 Hotkey；
- 通知版把脚本里的 `gloss paste` 改成 `gloss paste --notify` 即可。

### 3. iTerm2 Hotkey Window（终端党）

iTerm2 → Settings → Keys → Hotkey → 勾选 "Show/hide all windows with a
system-wide hotkey"。设一个热键（如 ⌥Space）呼出一个悬浮半透明终端，
里面 `gp` 或 `gn` 即可，热键再按一下收起。tmux 用户等价物：
`tmux display-popup -E gloss paste`。

## Chrome --app 无边框弹窗（L2 的正确打开方式）

```bash
gloss serve --no-browser &
open -a "Google Chrome" --args --app=http://127.0.0.1:7789 --window-size=640,720
```

没有地址栏、没有标签页，观感接近原生小窗。可以把这两行存成一个 shell
脚本再绑快捷键。

## 注意

- **不要**做"监控剪贴板自动收录"：你会复制密码、地址、临时文字，全部自动
  打给 LLM 又贵又不安全。显式触发才是正确姿势。
- 截图解析需要配置 `llm.vision_model`（如 DeepSeek 的 deepseek-flash、智谱
  GLM-4V、OpenAI gpt-4o-mini、本地 qwen2.5-vl 等）。文字部分不受影响。
- `gloss serve` 只绑定 127.0.0.1，同机任意程序都能调用它的 API（无鉴权），
  这是本地单用户工具的合理默认；不要手动改成 0.0.0.0 暴露到局域网。
