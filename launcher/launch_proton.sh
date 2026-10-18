#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Keep Steam's Proton/runtime prefix and replace only its verified game target.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/steam_linux_common.sh"
load_linux_installation

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
exec "${steam_argv[@]:0:game_index}" "$python_exe" -B -c "$bootstrap" "${steam_argv[@]:game_index+1}"
