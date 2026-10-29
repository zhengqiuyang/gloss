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
    assert "gloss-cli[app]" in str(exc.value)


def test_register_hotkey_returns_bool():
    from gloss import app

    # On macOS this registers a real hotkey and returns True; on CI/Linux
    # Carbon isn't loadable and it must return False — either way, no crash.
    result = app.register_hotkey(lambda: None)
    assert isinstance(result, bool)


@pytest.fixture
def runner_env(home):
    yield home
