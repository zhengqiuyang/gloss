"""gloss app: a tiny native macOS shell around the local web popup.

Menu-bar item + global hotkey (⌘G) + a floating panel showing the same
card UI as `gloss serve` (loaded from an in-process server, so the service
is alive for exactly as long as the app is).

Optional dependency: pip install 'gloss-cli[app]' (pyobjc Cocoa + WebKit).
The global hotkey is registered through Carbon (ctypes, stdlib), which —
unlike global event monitors — needs no Accessibility permission.
"""

from __future__ import annotations

import ctypes
import signal
import threading
from typing import Callable, Optional

from .config import gloss_home
from .models import GlossError


def _log(message: str) -> None:
    """Append to GLOSS_HOME/app.log — the app's black-box flight recorder."""
    try:
        from .models import now_iso

        with open(gloss_home() / "app.log", "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {message}\n")
    except Exception:
        pass

# Carbon modifiers
_MOD_MAP = {
    "cmd": 0x0100, "command": 0x0100,
    "alt": 0x0800, "option": 0x0800, "opt": 0x0800,
    "ctrl": 0x1000, "control": 0x1000,
    "shift": 0x0200,
}
_MOD_SYMBOL = {"cmd": "⌘", "command": "⌘", "alt": "⌥", "option": "⌥",
               "opt": "⌥", "ctrl": "⌃", "control": "⌃", "shift": "⇧"}
# Carbon virtual keycodes for a-z / 0-9 (kVK_ANSI_*)
_VK_MAP = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "1": 0x12,
    "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "=": 0x18,
    "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D, "]": 0x1E,
    "o": 0x1F, "u": 0x20, "[": 0x21, "i": 0x22, "p": 0x23, "l": 0x25,
    "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29, "\\": 0x2A, ",": 0x2B,
    "/": 0x2C, "n": 0x2D, "m": 0x2E,
}
_EVENT_CLASS_KEYBOARD = 0x6B657962  # kEventClassKeyboard = 'keyb'
_EVENT_HOT_KEY_PRESSED = 5          # Carbon Events.h: kEventHotKeyPressed = 5


def parse_hotkey(spec: str) -> Optional[tuple]:
    """'cmd+g' -> (keycode, carbon_modifiers, display) ; None if invalid."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    key = parts[-1]
    mods, display = 0, ""
    for m in parts[:-1]:
        if m not in _MOD_MAP:
            return None
        mods |= _MOD_MAP[m]
        display += _MOD_SYMBOL[m]
    if len(key) != 1 or key not in _VK_MAP:
        return None
    return _VK_MAP[key], mods, display + key.upper()


# Carbon modifier -> NSEvent modifier mask (for menu key-equivalent display)
_CARBON_TO_NS = {0x0100: 1 << 20, 0x0200: 1 << 17, 0x0800: 1 << 19, 0x1000: 1 << 18}


def _carbon_to_ns_mods(carbon_mods: int) -> int:
    ns = 0
    for bit, ns_bit in _CARBON_TO_NS.items():
        if carbon_mods & bit:
            ns |= ns_bit
    return ns


def run_app(no_hotkey: bool = False, show_on_start: bool = True) -> None:
    """Block forever running the menu-bar app; Ctrl+C or menu -> 退出 stops it."""
    try:
        import AppKit  # noqa: F401
        import Foundation  # noqa: F401
        import WebKit  # noqa: F401
    except ImportError as exc:
        raise GlossError(
            "gloss app 需要 macOS 原生绑定（pyobjc）：\n"
            "  uv tool install --reinstall -e '.[app]'   # 从源码目录\n"
            "  或 uv pip install 'gloss-cli\\[app]'      # 注意 \\[，别让 shell/rich 吃掉方括号\n"
            "  或 pip install pyobjc-framework-Cocoa pyobjc-framework-WebKit\n"
            "不装也没关系：gloss serve + 系统快捷键（见 extras/README.md）是等价替代。"
        ) from exc

    from .config import load_config
    from .server import start_server
    from .ui import console

    server = start_server(0)  # ephemeral port: never clashes with `gloss serve`
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    spec = load_config()["app"]["hotkey"]
    parsed = parse_hotkey(spec)
    display = parsed[2] if parsed else "菜单栏"
    console.print(
        f"[green]📖 gloss 常驻已启动[/green]（菜单栏图标 · {display} 呼出弹窗 · Ctrl+C 退出）"
    )
    _run_gui(port=port, no_hotkey=no_hotkey, show_on_start=show_on_start)


def _run_gui(port: int, no_hotkey: bool, show_on_start: bool) -> None:
    import AppKit as A

    from .config import load_config

    spec = load_config()["app"]["hotkey"]
    parsed = parse_hotkey(spec)

    delegate = _build_delegate_class().alloc().init()
    delegate.port = port
    delegate.hotkey = parsed  # (keycode, carbon_mods, display) or None
    delegate.buildUI()

    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)  # no Dock icon
    app.setDelegate_(delegate)

    from .ui import console

    _log(f"app start: port={port} spec={spec!r} parsed={parsed}")
    if not no_hotkey:
        if parsed is None:
            _log(f"hotkey INVALID: {spec!r}")
            console.print(
                f"[yellow]热键配置 {spec!r} 无法识别（示例: cmd+g / cmd+alt+g / ctrl+1），"
                "先用菜单栏图标呼出。可用 gloss config set app.hotkey 修改。[/yellow]"
            )
        elif not register_hotkey(delegate.togglePanel, spec):
            _log(f"hotkey REGISTER FAILED: {spec!r} (可能被其他应用占用)")
            from .notify import send

            send("gloss · 热键注册失败", f"{parsed[2]} 可能被其他应用占用，请看终端提示")
            console.print(
                f"[yellow]全局热键 {parsed[2]} 注册失败（可能被其他应用占用），"
                "仍可用菜单栏图标呼出[/yellow]"
            )
        else:
            _log(f"hotkey registered: {spec!r} -> {parsed[2]}")
            if parsed[1] == _MOD_MAP["cmd"] and len(spec.split("+")) == 2:
                console.print(
                    "[yellow]提示: ⌘+单键 是全局热键，gloss 运行期间会接管其他应用里的同款快捷键"
                    "（如 ⌘G 的“查找下一个”）。想换回来: gloss config set app.hotkey cmd+alt+g[/yellow]"
                )

    if show_on_start:
        delegate.showPanel()

    # NSApp.run() blocks inside native code; make Ctrl+C the hard kill it looks like
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app.run()


def _build_delegate_class():
    """Build the NSApplication delegate lazily so Linux/CI never imports AppKit."""
    import AppKit as A
    import Foundation as F
    import WebKit

    class Panel(A.NSPanel):
        def canBecomeKey(self):
            return True  # floating panels need this for webview text input

    class DragStrip(A.NSView):
        """Slim native strip along the top edge: drag it to move the window."""

        def isFlipped(self):
            return False

        def mouseDownCanMoveWindow(self):
            return True

        def mouseDown_(self, event):
            self.window().performDrag_(event)

    class ResizeGrip(A.NSView):
        """Bottom-right grip: drag to resize the panel."""

        def mouseDown_(self, event):
            w = self.window()
            start_frame = w.frame()
            start_mouse = A.NSEvent.mouseLocation()
            mask = A.NSEventMaskLeftMouseDragged | A.NSEventMaskLeftMouseUp
            while True:
                ev = w.nextEventMatchingMask_untilDate_inMode_dequeue_(
                    mask, F.NSDate.distantFuture(), A.NSEventTrackingRunLoopMode, True
                )
                if ev is None or ev.type() == A.NSEventTypeLeftMouseUp:
                    break
                m = A.NSEvent.mouseLocation()
                new_w = max(380, start_frame.size.width + (m.x - start_mouse.x))
                new_h = max(300, start_frame.size.height - (m.y - start_mouse.y))
                new_y = start_frame.origin.y - (new_h - start_frame.size.height)
                w.setFrame_(
                    F.NSMakeRect(start_frame.origin.x, new_y, new_w, new_h)
                )

        def drawRect_(self, rect):
            color = A.NSColor.systemGrayColor().colorWithAlphaComponent_(0.55)
            color.setStroke()
            path = A.NSBezierPath.bezierPath()
            path.setLineWidth_(1.5)
            w, h = self.bounds().size.width, self.bounds().size.height
            for i in range(3):
                o = 4 + i * 5
                path.moveToPoint_((w - o, 2.5))
                path.lineToPoint_((w - 2.5, o))
            path.stroke()

    class Delegate(F.NSObject):
        port = 0
        hotkey = None  # (keycode, carbon_mods, display) set by _run_gui
        panel = None
        webview = None
        status_item = None

        def buildUI(self) -> None:
            # menu bar item
            item = A.NSStatusBar.systemStatusBar().statusItemWithLength_(
                A.NSVariableStatusItemLength
            )
            item.button().setTitle_("📖")
            menu = A.NSMenu.alloc().init()

            def add(title, action, key="", mods=None):
                it = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, action, key
                )
                if mods is not None:
                    it.setKeyEquivalentModifierMask_(mods)
                it.setTarget_(self)
                menu.addItem_(it)

            # 菜单里显示真实生效的全局热键（Carbon 注册的那个）
            if self.hotkey:
                _, carbon_mods, display = self.hotkey
                add("显示弹窗 " + display, "mShow:",
                    display[-1].lower(), _carbon_to_ns_mods(carbon_mods))
            else:
                add("显示弹窗", "mShow:")
            add("重新载入", "mReload:", "r")
            menu.addItem_(A.NSMenuItem.separatorItem())
            menu.addItem_(
                A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "退出 gloss", "terminate:", "q"
                )
            )
            item.setMenu_(menu)
            self.status_item = item

            # floating panel (spotlight-like: no visible titlebar, rounded, frosted)
            mask = (
                A.NSWindowStyleMaskNonactivatingPanel
                | A.NSWindowStyleMaskTitled
                | A.NSWindowStyleMaskFullSizeContentView
                | A.NSWindowStyleMaskResizable
            )
            panel = Panel.alloc().initWithContentRect_styleMask_backing_defer_(
                F.NSMakeRect(0, 0, 500, 380), mask, A.NSBackingStoreBuffered, False
            )
            panel.setTitlebarAppearsTransparent_(True)
            panel.setTitleVisibility_(A.NSWindowTitleHidden)
            panel.setLevel_(A.NSFloatingWindowLevel)
            panel.setHidesOnDeactivate_(False)
            panel.setReleasedWhenClosed_(False)
            panel.setMovableByWindowBackground_(True)
            for kind in (
                A.NSWindowCloseButton,
                A.NSWindowMiniaturizeButton,
                A.NSWindowZoomButton,
            ):
                btn = panel.standardWindowButton_(kind)
                if btn:
                    btn.setHidden_(True)

            # frosted-glass backdrop under a transparent webview
            effect = A.NSVisualEffectView.alloc().initWithFrame_(
                F.NSMakeRect(0, 0, 500, 380)
            )
            effect.setMaterial_(A.NSVisualEffectMaterialPopover)
            effect.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
            effect.setState_(A.NSVisualEffectStateActive)
            effect.setAutoresizingMask_(2 | 16)
            panel.contentView().addSubview_(effect)

            conf = WebKit.WKWebViewConfiguration.alloc().init()
            try:
                # 新系统上可能失效（页面自身有玻璃拟态兜底），生效则能透出桌面模糊
                conf.setValue_forKey_(False, "drawsBackground")
            except Exception:
                pass
            webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
                F.NSMakeRect(0, 0, 500, 380), conf
            )
            webview.setUIDelegate_(self)
            webview.setValue_forKey_(False, "drawsBackground")  # let the blur show
            webview.setAutoresizingMask_(2 | 16)  # grow with parent
            panel.contentView().addSubview_(webview)

            # native move/resize affordances on top of the webview
            panel.setMinSize_(F.NSSize(380, 300))
            strip = DragStrip.alloc().initWithFrame_(
                F.NSMakeRect(0, 380 - 16, 500, 16)
            )
            strip.setAutoresizingMask_(2 | 16)  # flexible width, pinned to top
            panel.contentView().addSubview_(strip)
            grip = ResizeGrip.alloc().initWithFrame_(
                F.NSMakeRect(500 - 18, 0, 18, 18)
            )
            grip.setAutoresizingMask_(4 | 16)  # pinned to bottom-right
            panel.contentView().addSubview_(grip)

            self.panel = panel
            self.webview = webview

            F.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self, "windowDidResignKey:", "NSWindowDidResignKeyNotification", panel
            )

        # -- panel control ---------------------------------------------

        def showPanel(self) -> None:
            _log("panel show")
            url = F.NSURL.URLWithString_(f"http://127.0.0.1:{self.port}/?glass=1")
            self.webview.loadRequest_(F.NSURLRequest.requestWithURL_(url))
            self._position_near_mouse()
            self.panel.makeKeyAndOrderFront_(None)

        def togglePanel(self) -> None:
            _log("hotkey triggered -> toggle")
            if self.panel is not None and self.panel.isVisible():
                self.panel.orderOut_(None)
                _log("panel hide")
            else:
                self.showPanel()

        def _position_near_mouse(self) -> None:
            mouse = A.NSEvent.mouseLocation()
            frame = self.panel.frame()
            screen = A.NSScreen.mainScreen().visibleFrame()
            x = mouse.x - frame.size.width / 2
            x = max(
                screen.origin.x,
                min(x, screen.origin.x + screen.size.width - frame.size.width),
            )
            y = mouse.y - frame.size.height + 18  # keep the pointer over the panel
            y = max(
                screen.origin.y,
                min(y, screen.origin.y + screen.size.height - frame.size.height),
            )
            self.panel.setFrameOrigin_(F.NSMakePoint(x, y))

        # -- menu actions -----------------------------------------------

        def mShow_(self, sender) -> None:
            self.showPanel()

        def mReload_(self, sender) -> None:
            self.showPanel()

        # -- window / webview events --------------------------------------

        def windowDidResignKey_(self, notification) -> None:
            if self.panel is not None:
                self.panel.orderOut_(None)

        def webViewDidClose_(self, webview) -> None:
            # the page calls window.close() on Esc
            if self.panel is not None:
                self.panel.orderOut_(None)

    return Delegate


# Module-level: the CFUNCTYPE object MUST outlive register_hotkey(), otherwise
# ctypes garbage-collects the trampoline and Carbon calls a dangling pointer.
_HOTKEY_REFS: list = []


def register_hotkey(callback: Callable[[], None], spec: str = "cmd+g") -> bool:
    """Register a system-wide hotkey via Carbon. No accessibility permission needed."""
    parsed = parse_hotkey(spec)
    if parsed is None:
        return False
    key_code, modifiers, _display = parsed

    try:
        carbon = ctypes.CDLL(
            "/System/Library/Frameworks/Carbon.framework/Versions/Current/Carbon"
        )
    except OSError:
        return False

    class HotKeyID(ctypes.Structure):
        _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

    class EventTypeSpec(ctypes.Structure):
        _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

    CALLBACK = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )

    def _cb(_call_ref, _event, _user_data) -> int:
        try:
            callback()
        except Exception as exc:
            _log(f"hotkey callback ERROR: {exc!r}")
        return 0

    _HOTKEY_REFS.clear()
    _HOTKEY_REFS.append(CALLBACK(_cb))  # keep alive for as long as C uses it

    carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    carbon.InstallEventHandler.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(EventTypeSpec),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.InstallEventHandler.restype = ctypes.c_int
    carbon.RegisterEventHotKey.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        HotKeyID,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.RegisterEventHotKey.restype = ctypes.c_int

    target = carbon.GetApplicationEventTarget()
    if not target:
        return False

    spec = EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOT_KEY_PRESSED)
    handler = ctypes.c_void_p()
    if (
        carbon.InstallEventHandler(
            target, _HOTKEY_REFS[0], 1, ctypes.byref(spec), None, ctypes.byref(handler)
        )
        != 0
    ):
        return False

    hotkey = ctypes.c_void_p()
    status = carbon.RegisterEventHotKey(
        key_code,
        modifiers,
        HotKeyID(0x676C6F73, 1),  # 'glos', id 1
        target,
        0,
        ctypes.byref(hotkey),
    )
    return status == 0
