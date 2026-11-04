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
# Keep the native host alive for fatal presentation. Unique private state prevents
# another launch's outcome from opening a stale notice or claiming its log.
mkdir -p -- "$mod_root/.state"
notice_dir=$(mktemp -d -- "$mod_root/.state/linux-notice.XXXXXXXXXX")
session=${notice_dir##*/}
notice_pid= controller_pid=
seen_notice=0
input_open=0
output_open=0
finish() {
    trap '' HUP INT TERM
    if (( input_open )); then exec 7>&-; input_open=0; fi
    if [[ -n $notice_pid ]]; then kill -TERM "$notice_pid" 2>/dev/null || true; wait "$notice_pid" 2>/dev/null || true; fi
    if [[ -n $controller_pid ]]; then
        # EOF reaches the Windows controller even through a spawning Proton
        # shim. Give its existing four-second owned-game shutdown time to finish.
        deadline=$((SECONDS + 5))
        while (( output_open && SECONDS < deadline )); do
            IFS= read -r -t "$((deadline - SECONDS))" -u 5 line || break
            printf '%s\n' "$line"
        done
        for pid in $(jobs -pr); do
            if [[ $pid == "$controller_pid" ]]; then kill -KILL "$pid" 2>/dev/null || true; fi
        done
        wait "$controller_pid" 2>/dev/null || true
    fi
    # Successful launches have no evidence to retain. Failed-session files stay
    # available for inspection; this never removes another session's directory.
    rm -f -- "$notice_dir/controller-input" "$notice_dir/controller-output"
    rmdir -- "$notice_dir" 2>/dev/null || true
}
trap finish EXIT
trap 'exit 129' HUP
interrupt() {
    if (( seen_notice )) && [[ -n $notice_pid ]]; then
        # A fatal notice already owns the exit code. Ctrl+C dismisses it without
        # converting the original failure into an ordinary interruption.
        kill -TERM "$notice_pid" 2>/dev/null || true
    else
        exit 130
    fi
}
trap interrupt INT
trap 'exit 143' TERM
exec 6<&0
mkfifo -- "$notice_dir/controller-input" "$notice_dir/controller-output"
exec 7<>"$notice_dir/controller-input"
input_open=1
"${steam_argv[@]:0:game_index}" "$python_exe" -B -c "$bootstrap" \
    --host-session "$session" --host-log-path "$mod_root/logs/launcher.log" \
    "${launcher_argv[@]}" -- "${steam_argv[@]:game_index+1}" \
    < "$notice_dir/controller-input" > "$notice_dir/controller-output" 7>&- 6>&- &
controller_pid=$!
exec 5<"$notice_dir/controller-output"
output_open=1
while IFS= read -r line <&5 || [[ -n $line ]]; do
    # Windows stdout uses CRLF even though the host's control parser uses LF.
    control=${line%$'\r'}
    if [[ $control == "MOD_LAUNCHER_DONE $session" ]]; then
        if (( input_open )); then exec 7>&-; input_open=0; fi
    elif [[ $control =~ ^MOD_LAUNCHER_NOTICE\ (linux-notice\.[A-Za-z0-9]{10})\ ([0-9]{1,5})\ ([a-f0-9]{64})$ ]] \
        && [[ ${BASH_REMATCH[1]} == "$session" ]] && (( seen_notice == 0 )); then
        seen_notice=1
        bash "$mod_root/launcher/diagnostics/linux_notice.sh" "$notice_dir" "$mod_root/logs/launcher.log" \
            "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" <&6 5>&- 6>&- 7>&- &
        notice_pid=$!
    else
        printf '%s\n' "$line"
    fi
done
exec 5<&- 6<&-
output_open=0
code=0
wait "$controller_pid" || code=$?
controller_pid=
if [[ -n $notice_pid ]]; then
    # Includes terminal startup that stalled before establishing its socket.
    kill -TERM "$notice_pid" 2>/dev/null || true
    wait "$notice_pid" || true
    notice_pid=
fi
exit "$code"
