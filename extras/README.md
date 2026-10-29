# 零打字方案：把 gloss 接到"复制"这个动作上

日常的最佳体验不是敲 `gloss <词>`，而是：**在任何应用里选中/复制 → 一个动作出卡片**。
按你手头的工具选一种：

## 0. 先把命令打到最短（必做）

```bash
# 加进 ~/.zshrc
alias g='gloss'
alias gp='gloss paste'
```

之后就是：复制任何东西 → 终端敲 `gp` ⏎。文字变词条/摘要卡片，截图直接进视觉解析。

## 1. Raycast（装了 Raycast 的话，推荐）

把 `extras/raycast/gloss-paste.sh` 拷到 Raycast 的 Script Commands 目录
（Raycast 设置 → Extensions → Script Command → Open Directory），
然后在 Raycast 里给它设一个 Hotkey（比如 ⌥G）。

体验：任何地方 ⌘C → 按 ⌥G → 卡片内容内联弹出。

## 2. macOS 快捷指令（不装任何东西）

1. 打开「快捷指令」App → 新建快捷指令
2. 添加动作「运行 Shell 脚本」，Shell 选 zsh，脚本内容：
   ```bash
   $HOME/.local/bin/gloss paste
   ```
   传入方式选「作为自变量」改成「不传入」都可以
3. 再加一个动作「显示结果」
4. 快捷指令详情 → 「添加键盘快捷键」，比如 ⌥⌘G

体验：任何地方 ⌘C → 按 ⌥⌘G → 系统弹窗显示卡片。

## 3. 想要"选中即查"的面板应用？

这是 gloss roadmap 里的 macOS hotkey panel（选中文字按快捷键、原生小窗显示卡片）。
在它做出来之前，方案 1/2 已经能覆盖 90% 的场景——区别只是结果显示在
Raycast/通知弹窗里，而不是一个专门的输入框。

## 注意

- **不要**做"监控剪贴板自动收录"：你会复制密码、地址、临时文字，全部自动
  打给 LLM 又贵又不安全。显式触发（快捷键/命令）才是正确姿势。
- 截图解析需要配置 `llm.vision_model`（DeepSeek 不支持视觉；可用智谱
  GLM-4V、OpenAI gpt-4o-mini、本地 qwen2.5-vl 等）。文字部分不受影响。
