"""gloss app shell: importable without pyobjc, friendly error when missing."""

from __future__ import annotations

import sys

import pytest

from gloss.models import GlossError


def test_run_app_without_pyobjc_gives_install_hint(runner_env, monkeypatch):
    from gloss import app

    for mod in ("AppKit", "Foundation", "WebKit"):
        monkeypatch.setitem(sys.modules, mod, None)  # import raises ImportError

    with pytest.raises(GlossError) as exc:
        app.run_app()
    assert "pyobjc" in str(exc.value)
    assert "gloss-cli\\[app]" in str(exc.value)


def test_register_hotkey_returns_bool():
    from gloss import app

    # On macOS this registers a real hotkey and returns True; on CI/Linux
    # Carbon isn't loadable and it must return False — either way, no crash.
    result = app.register_hotkey(lambda: None, "cmd+g")
    assert isinstance(result, bool)


def test_parse_hotkey_variants():
    from gloss import app

    assert app.parse_hotkey("cmd+g") == (0x05, 0x0100, "⌘G")
    assert app.parse_hotkey("ctrl+shift+r") == (0x0F, 0x1200, "⌃⇧R")
    assert app.parse_hotkey("ctrl+1")[0] == 0x12
    assert app.parse_hotkey("shift+r") == (0x0F, 0x0200, "⇧R")
    # 无修饰键 / 非法键 / 未知修饰 → None
    assert app.parse_hotkey("g") is None
    assert app.parse_hotkey("cmd+!") is None
    assert app.parse_hotkey("hyper+g") is None
    assert app.parse_hotkey("") is None


def test_config_default_hotkey_matches_platform(home):
    """新装默认 Mac ⌘G，Windows Ctrl+G。"""
    from gloss.config import load_config

    assert load_config()["app"]["hotkey"] == ("cmd+g" if sys.platform == "darwin" else "ctrl+g")


def test_config_auto_lookup_defaults_on_and_parses_bool(home):
    from gloss.config import load_config, set_config_value

    assert load_config()["app"]["auto_lookup"] is True
    set_config_value("app.auto_lookup", "off")
    assert load_config()["app"]["auto_lookup"] is False
    set_config_value("app.auto_lookup", "on")
    assert load_config()["app"]["auto_lookup"] is True


@pytest.fixture
def runner_env(home):
    yield home


@pytest.mark.skipif(sys.platform != "darwin", reason="native NSPanel geometry requires macOS")
def test_native_drag_and_resize_regions_stay_at_window_edges(home):
    import time
    from types import SimpleNamespace
    A = pytest.importorskip("AppKit")
    F = pytest.importorskip("Foundation")
    pytest.importorskip("WebKit")
    from gloss import app

    A.NSApplication.sharedApplication()
    delegate = app._build_delegate_class().alloc().init()
    delegate.buildUI()
    panel = delegate.panel
    try:
        content = panel.contentView()
        strip = next(v for v in content.subviews() if type(v).__name__ == "DragStrip")
        grip = next(v for v in content.subviews() if type(v).__name__ == "ResizeGrip")
        assert strip.toolTip()
        # Exercise the real Cocoa drawing API without displaying a user window.
        image = A.NSImage.alloc().initWithSize_(strip.bounds().size)
        image.lockFocus()
        try:
            strip.drawRect_(strip.bounds())
        finally:
            image.unlockFocus()
        for width, height in ((500, 380), (720, 650), (420, 320)):
            panel.setContentSize_(F.NSSize(width, height))
            bounds = content.bounds()
            move = strip.frame()
            resize = grip.frame()
            assert move.size.height >= 28, f"invisible/narrow drag region: {move}"
            assert move.size.height <= 40, f"drag region stretched over form: {move}"
            assert abs(move.origin.y + move.size.height - bounds.size.height) < 1
            assert abs(move.size.width - bounds.size.width) < 1
            assert abs(resize.origin.x + resize.size.width - bounds.size.width) < 1
            assert resize.origin.y == 0 and resize.size.height == 18
            # Cocoa routes a press on the header to the native drag handler,
            # while the body remains available to the WebKit settings form.
            assert content.hitTest_((width / 2, height - 10)) == strip
            assert content.hitTest_((width / 2, height - 60)) not in (strip, grip)
            assert content.hitTest_((width - 8, 8)) == grip

        # Execute the actual mouse-down callback. Geometry alone missed an
        # invalid NSWindow selector; intercept only the native drag loop.
        events = []

        class DragWindowProbe(A.NSPanel):
            def performWindowDragWithEvent_(self, event):
                events.append(event)

        probe = DragWindowProbe.alloc().initWithContentRect_styleMask_backing_defer_(
            F.NSMakeRect(0, 0, 500, 380), A.NSWindowStyleMaskTitled,
            A.NSBackingStoreBuffered, False,
        )
        probe.setReleasedWhenClosed_(False)
        try:
            probe.contentView().addSubview_(strip)
            event = A.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                A.NSEventTypeLeftMouseDown, (20, 15), 0, 0,
                probe.windowNumber(), None, 1, 1, 1,
            )
            strip.mouseDown_(event)
            assert events == [event]
        finally:
            content.addSubview_(strip)
            probe.orderOut_(None)

        # Exercise WebKit -> native settings messages, not just JS text.
        delegate.webview.loadHTMLString_baseURL_(
            "<script>window.webkit.messageHandlers.glossSettings.postMessage(true)</script>", None
        )
        deadline = time.monotonic() + 8
        while not delegate.settings_open and time.monotonic() < deadline:
            F.NSRunLoop.currentRunLoop().runUntilDate_(F.NSDate.dateWithTimeIntervalSinceNow_(0.02))
        assert delegate.settings_open, "WebKit did not deliver the settings-open message"

        class WindowProbe:
            visible = True
            key = False

            def isVisible(self):
                return self.visible

            def isKeyWindow(self):
                return self.key

            def orderOut_(self, sender):
                self.visible = False

            def makeKeyAndOrderFront_(self, sender):
                self.visible = True
                self.key = True

            def frame(self):
                return F.NSMakeRect(0, 0, 500, 380)

            def setFrameOrigin_(self, point):
                pass

        real_webview = delegate.webview
        loads = []
        state = WindowProbe()
        delegate.panel = state
        delegate.webview = SimpleNamespace(loadRequest_=loads.append)
        try:
            delegate.windowDidResignKey_(None)
            assert state.visible  # switching to the browser keeps settings open
            delegate.togglePanel()
            assert state.visible and state.key  # hotkey returns focus, not hide
            delegate.showPanel()
            assert not loads  # no reload that would discard unsaved API-key text
            message = SimpleNamespace(name=lambda: "glossSettings", body=lambda: False)
            delegate.userContentController_didReceiveScriptMessage_(None, message)
            delegate.windowDidResignKey_(None)
            assert not state.visible  # lookup mode retains auto-hide
            delegate.showPanel()
            assert len(loads) == 1  # lookup mode still refreshes the clipboard
        finally:
            delegate.panel = panel
            delegate.webview = real_webview
    finally:
        panel.orderOut_(None)
        A.NSStatusBar.systemStatusBar().removeStatusItem_(delegate.status_item)
        F.NSNotificationCenter.defaultCenter().removeObserver_(delegate)
