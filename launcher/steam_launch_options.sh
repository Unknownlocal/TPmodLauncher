#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Generate a native Linux Steam line without requiring a Linux Python runtime.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/steam_linux_common.sh"
load_linux_installation

launch_line="bash $(quote_steam_path "$mod_root/launcher/launch_proton.sh") %command%"
printf 'Steam > Properties > General > Launch Options:\n\n%s\n' "$launch_line"

clipboard_command() {
    if command -v timeout >/dev/null 2>&1; then
        timeout 5 "$@"
    else
        "$@"
    fi
}

copy_linux_clipboard() {
    if [[ -n ${WAYLAND_DISPLAY:-} ]] && command -v wl-copy >/dev/null 2>&1; then
        if printf '%s' "$launch_line" | clipboard_command wl-copy; then return 0; fi
    fi
    if [[ -n ${DISPLAY:-} ]] && command -v xclip >/dev/null 2>&1; then
        if printf '%s' "$launch_line" | clipboard_command xclip -selection clipboard; then return 0; fi
    fi
    if [[ -n ${DISPLAY:-} ]] && command -v xsel >/dev/null 2>&1; then
        if printf '%s' "$launch_line" | clipboard_command xsel --clipboard --input; then return 0; fi
    fi
    return 1
}

if copy_linux_clipboard 2>/dev/null; then
    printf '\nCopied to clipboard. Paste the line into Steam\047s Launch Options field.\n'
else
    printf '\nCould not copy to the clipboard. Copy the displayed line manually.\n'
fi
printf '\nKeep %%command%% unchanged. This helper does not change Steam settings or start the game.\n'
printf 'Proton/Wine mod loading remains experimental and untested.\n'
