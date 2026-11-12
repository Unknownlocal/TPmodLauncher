#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Keep Steam's Proton/runtime prefix and replace only its verified game target.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/steam_linux_common.sh"
load_linux_installation

# Launcher options precede Steam's complete command. Never parse game arguments.
launcher_argv=()
if [[ ${1:-} == --log-level ]]; then
    if (( $# < 2 )); then
        printf '%s\n' '--log-level requires a level.' >&2
        exit 1
    fi
    # The Windows entry point shares name/integer validation with normal entry.
    launcher_argv=(--log-level "$2")
    shift 2
fi
if [[ ${1:-} == -- ]]; then shift; fi

steam_argv=("$@")
game_index=-1
expected_game=$(realpath -e -- "$game_root/TeamPrincipal.exe")
for (( index=0; index<${#steam_argv[@]}; index++ )); do
    candidate=${steam_argv[index]}
    if [[ -f $candidate ]] && [[ $(realpath -e -- "$candidate") == "$expected_game" ]]; then
        game_index=$index
        break
    fi
done
if (( game_index < 2 )); then
    printf '%s\n' "Supply Steam's complete Proton command after this wrapper; the installed game target was not found." >&2
    exit 1
fi
proton_verb=${steam_argv[game_index-1]}
proton_program=${steam_argv[game_index-2]}
if [[ ${proton_program##*/} != proton || ( $proton_verb != waitforexitandrun && $proton_verb != run ) ]]; then
    printf '%s\n' "The installed game must be the target of Steam's Proton run command. Enable Proton for this game in Steam." >&2
    exit 1
fi

# Windows Python discovers its own Wine-mapped installation path. A small trusted
# adapter then supplies entry.py with the installed Windows game path; Unix paths
# are never passed to the Windows executable validator, and no Z: mapping is assumed.
bootstrap="import runpy,sys;from pathlib import Path;runpy.run_path(str(Path(sys.executable).resolve().parents[2]/'launcher'/'proton_entry.py'),run_name='__main__')"
source "$mod_root/launcher/diagnostics/proton_supervisor.sh"
