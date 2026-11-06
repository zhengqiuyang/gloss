"""Run the real embedded JavaScript's save handler against a small fake DOM/API."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from gloss.server import _PAGE


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is required for JavaScript behavior checks')
@pytest.mark.parametrize('scenario', ['serial', 'pending-edit', 'reopened', 'reopened-clear', 'failure', 'duplicate', 'key'])
def test_settings_save_state_machine(scenario):
    script = _PAGE.split('<script>', 1)[1].split('</script>', 1)[0]
    for key, value in {'__PREFILL__':'', '__HINT__':'', '__CLIP_KIND__':'none',
                       '__AUTO_LOOKUP__':False, '__NO_KEY__':False}.items():
        script = script.replace(key, json.dumps(value))
    result = subprocess.run(
        [shutil.which('node'), str(Path(__file__).with_name('settings_state_harness.cjs'))],
        input=json.dumps({'script':script, 'scenario':scenario}), encoding='utf-8',
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
