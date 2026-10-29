#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title gloss paste
# @raycast.mode inline
# @raycast.packageName gloss
# @raycast.icon 📖
# @raycast.description Read the clipboard and gloss it (text term / snippet, or image).

export PATH="$HOME/.local/bin:$PATH"
exec gloss paste
