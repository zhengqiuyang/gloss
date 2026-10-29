"""gloss app: a tiny native macOS shell around the local web popup.

Menu-bar item + global hotkey (⌥⌘G) + a floating panel showing the same
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
from typing import Callable

from .models import GlossError

# Carbon bits (kVK_ANSI_G = 0x05, cmdKey|optionKey = 0x0900, 'keyb'/pressed)
_KC_G = 0x05
_MOD_CMD_OPT = 0x0900
_EVENT_CLASS_KEYBOARD = 0x6B657962  # 'keyb'
_EVENT_HOT_KEY_PRESSED = 10


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

    from .server import start_server
    from .ui import console

    server = start_server(0)  # ephemeral port: never clashes with `gloss serve`
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    console.print(
        "[green]📖 gloss 常驻已启动[/green]（菜单栏图标 · ⌥⌘G 呼出弹窗 · Ctrl+C 退出）"
    )
    _run_gui(port=port, no_hotkey=no_hotkey, show_on_start=show_on_start)


def _run_gui(port: int, no_hotkey: bool, show_on_start: bool) -> None:
    import AppKit as A

    delegate = _build_delegate_class().alloc().init()
    delegate.port = port
    delegate.buildUI()

    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)  # no Dock icon
    app.setDelegate_(delegate)

    if not no_hotkey and not register_hotkey(delegate.togglePanel):
        from .ui import console

        console.print("[yellow]全局热键 ⌥⌘G 注册失败，仍可用菜单栏图标呼出[/yellow]")

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

    class Delegate(F.NSObject):
        port = 0
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

            def add(title, action, key=""):
                it = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, action, key
                )
                it.setTarget_(self)
                menu.addItem_(it)

            add("显示弹窗", "mShow:", "g")
            add("重新载入", "mReload:", "r")
            menu.addItem_(A.NSMenuItem.separatorItem())
            menu.addItem_(
                A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "退出 gloss", "terminate:", "q"
                )
            )
            item.setMenu_(menu)
            self.status_item = item

            # floating panel (spotlight-like: no visible titlebar, rounded, shadowed)
            mask = (
                A.NSWindowStyleMaskNonactivatingPanel
                | A.NSWindowStyleMaskTitled
                | A.NSWindowStyleMaskFullSizeContentView
            )
            panel = Panel.alloc().initWithContentRect_styleMask_backing_defer_(
                F.NSMakeRect(0, 0, 620, 540), mask, A.NSBackingStoreBuffered, False
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

            conf = WebKit.WKWebViewConfiguration.alloc().init()
            webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
                F.NSMakeRect(0, 0, 620, 540), conf
            )
            webview.setUIDelegate_(self)
            webview.setAutoresizingMask_(2 | 16)  # grow with parent
            panel.contentView().addSubview_(webview)

            self.panel = panel
            self.webview = webview

            F.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self, "windowDidResignKey:", "NSWindowDidResignKeyNotification", panel
            )

        # -- panel control ---------------------------------------------

        def showPanel(self) -> None:
            url = F.NSURL.URLWithString_(f"http://127.0.0.1:{self.port}/")
            self.webview.loadRequest_(F.NSURLRequest.requestWithURL_(url))
            self._position_near_mouse()
            self.panel.makeKeyAndOrderFront_(None)

        def togglePanel(self) -> None:
            if self.panel is not None and self.panel.isVisible():
                self.panel.orderOut_(None)
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


def register_hotkey(callback: Callable[[], None]) -> bool:
    """Register ⌥⌘G system-wide via Carbon. No accessibility permission needed."""
    holder: dict = {"cb": None}

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
        except Exception:
            pass
        return 0

    holder["cb"] = CALLBACK(_cb)  # keep alive for the C side

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
            target, holder["cb"], 1, ctypes.byref(spec), None, ctypes.byref(handler)
        )
        != 0
    ):
        return False

    hotkey = ctypes.c_void_p()
    status = carbon.RegisterEventHotKey(
        _KC_G,
        _MOD_CMD_OPT,
        HotKeyID(0x676C6F73, 1),  # 'glos', id 1
        target,
        0,
        ctypes.byref(hotkey),
    )
    return status == 0
