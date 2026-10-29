# 零打字方案：从"敲命令"到"热键弹窗"

日常的最佳体验不是敲 `gloss <词>`，而是：**复制 → 一个动作出卡片 → 继续阅读**。
本页按实现成本从低到高给方案，按需取用。

> 平台备注：`gloss paste` 剪贴板捕获（文字 + 截图）与 **L3 全局热键弹窗**
> （macOS `gloss app` ⌥⌘G / Windows `gloss hotkey` Ctrl+G）双平台可用；
> L1 系统通知目前仅 **macOS**；`gloss serve`（L2）全平台可用。

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

## Windows 方案

剪贴板捕获在 Windows 上开箱即用（Win32 剪贴板读文字，截图走 PowerShell）：

1. `Win+Shift+S` 截图（进剪贴板）或照常 `Ctrl+C` 复制文字；
2. 终端里敲 `gloss paste`。建议在 PowerShell `$PROFILE` 里加（**`gp` 在 PowerShell 里用不了**——它是内置 AllScope 别名，任何方式都覆盖不掉；Git Bash 无此限制）：

   ```powershell
   Set-Alias g gloss
   function gpp { gloss paste @args }
   function gn { gloss paste --notify @args }
   ```

   Git Bash 用户照旧：`alias g='gloss'`、`alias gp='gloss paste'`。

### 全局热键（gloss hotkey，零依赖，推荐）

macOS 有 `gloss app`（⌥⌘G），Windows 的对称能力是 `gloss hotkey`——
用 Win32 `RegisterHotKey` 注册系统热键（不需要装 AutoHotkey）：

```powershell
gloss hotkey              # 前台运行：Ctrl+G 呼出弹窗（对齐 macOS 的 ⌘G）
gloss hotkey --startup    # 写入开机自启（登录后隐藏常驻）
gloss hotkey --no-startup # 移除开机自启
```

按下热键后：自动确保 `gloss serve` 在跑（没跑就静默拉起），弹出**无边框
置顶卡片**——WebView2 原生窗口，不占任务栏和 Alt-Tab，顶栏可拖动；和
macOS `gloss app` 的浮窗同源；剪贴板里的文字或 `Win+Shift+S` 截图**立即
出卡**，`Esc` 或右上角 ✕ 关窗回阅读。没装 `.[app]` extra（pywebview）时
自动回退 Chrome `--app` 窗口：功能相同，外观带浏览器边框。

常驻形态是**系统托盘的 📖 图标**（时钟附近，即 macOS 菜单栏图标的对应物）：
双击呼出卡片，右键「退出」。没装 `.[app]` extra 时无图标，进程仍驻留，
退出用热键 Ctrl+Alt+Shift+G。
想换组合（与 macOS 共用同一个配置键）：

```bash
gloss config set app.hotkey ctrl+alt+g
```

> 注意 `app.hotkey` 里的 `cmd`（macOS 的 ⌘）在 Windows 上读作 Win 键，
> 而 `Win+G` 被 Xbox Game Bar 占用——配置保持默认 `cmd+g` 时，注册失败会
> 自动回退到 `Ctrl+G`（对齐 ⌘G）并提示；若 Ctrl+G 也被其他软件占用，
> 换一个组合即可。

### 无边框弹窗（Chrome --app 的 Windows 版，热键方案的手动版）

```powershell
gloss serve --no-browser
Start-Process chrome '-app=http://127.0.0.1:7789' '-window-size=640,720'
```

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
