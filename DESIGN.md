---
name: gloss
description: A local glossary card beside the user's reading.
colors:
  accent: "#0a84ff"
typography:
  sans:
    fontFamily: '-apple-system, "PingFang SC", "Helvetica Neue", sans-serif'
omitted:
  - section: spacing
    reason: Existing embedded CSS remains canonical.
  - section: rounded
    reason: Existing embedded CSS remains canonical.
---

# Gloss interface context

The product surface is a compact glossary card and service settings form for
desktop readers. Preserve the current frosted panel, system typography and
Chinese interface labels. README files provide Chinese and English guidance;
language settings control generated explanations, not interface localization.

Runtime tokens and form rendering remain owned by `src/gloss/server.py`.
Its `:root --accent` and `body` font declaration map to the values above.
Native window geometry, dragging and focus behavior belong to `src/gloss/app.py`.
Terminal onboarding belongs to `src/gloss/cli.py`; both configuration surfaces
write through `src/gloss/config.py`. No new visual token system is introduced.

Settings are an editing task: switching apps keeps the native panel visible,
and showing it again preserves its document and draft. Closing settings returns
to lookup-mode auto-hide. Explicit Reload resets the document. Native settings
state is conveyed by `setSettingsOpen` through the `glossSettings` WebKit handler.
Save feedback, duplicate protection and response baselines stay owned by the
existing settings save handler. Verification lives in `tests/test_app.py`,
`tests/test_settings_state.py` and `tests/test_setup.py`.
