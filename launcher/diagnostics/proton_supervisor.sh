#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Sourced after launch_proton.sh validates the installation and original argv.
# Steam's Windows helper may replace all standard streams with console handles.
# Control and owner lifetime therefore use explicitly opened, private endpoints.
umask 077
mkdir -p -- "$mod_root/.state"
notice_dir=$(mktemp -d -- "$mod_root/.state/linux-notice.XXXXXXXXXX")
session=${notice_dir##*/}
controller_pid= relay_pid=
owner_open=0
control_open=0
events_open=0
outer_code= done_code=
trace() {
    # Error payloads stay in the console/log, outside this lifecycle trace.
    printf '%(%FT%T%z)T %s\n' -1 "$1" >> "$notice_dir/transport.txt"
}
finish() {
    trap '' HUP INT TERM
    if (( owner_open )); then exec 9>&- 7>&-; owner_open=0; fi
    if (( control_open )); then exec 5>&-; control_open=0; fi
    if [[ -n $controller_pid ]]; then
        # Owner EOF wakes the Windows shutdown path. Allow its four-second
        # owned-game cleanup before cancelling this wrapper's remaining job.
        deadline=$((SECONDS + 5))
        while [[ -z $outer_code ]] && (( events_open && SECONDS < deadline )); do
            IFS= read -r -t "$((deadline - SECONDS))" -u 8 line || break
            if [[ $line =~ ^OUTER\ ([0-9]{1,3})$ ]]; then outer_code=${BASH_REMATCH[1]}; fi
        done
        if [[ -z $outer_code ]]; then kill -TERM "$controller_pid" 2>/dev/null || true; fi
        wait "$controller_pid" 2>/dev/null || true
    fi
    if [[ -n $relay_pid ]]; then kill -TERM "$relay_pid" 2>/dev/null || true; wait "$relay_pid" 2>/dev/null || true; fi
    if (( events_open )); then exec 8>&-; fi
    rm -f -- "$notice_dir/owner" "$notice_dir/control" "$notice_dir/transport-events"
    if [[ ${code:-1} == 0 ]]; then rm -f -- "$notice_dir/transport.txt"; fi
    rmdir -- "$notice_dir" 2>/dev/null || true
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
mkfifo -m 600 -- "$notice_dir/owner" "$notice_dir/control" "$notice_dir/transport-events"
# Temporary owner keeper permits Windows' first read-only open. Only the parent
# retains it; after READY it is replaced by the sole directional writer (7).
exec 9<>"$notice_dir/owner" 8<>"$notice_dir/transport-events"
owner_open=1
events_open=1
# Open the relay reader before forking. A temporary writer prevents premature
# EOF; the parent alone keeps it until HELLO. Losing the parent before Windows
# opens control therefore also wakes the relay instead of orphaning an open.
exec 5<>"$notice_dir/control" 4<"$notice_dir/control"
control_open=1
trace STARTING

# One reader forwards control frames and genuine writer EOF to the event queue.
# The separate prefix waiter cannot mask Windows control EOF with its own writer.
(
    export LC_ALL=C
    while IFS= read -r -n 257 line <&4; do
        if (( ${#line} > 256 )); then printf 'INVALID\n' >&3; exit 1; fi
        printf 'CONTROL %s\n' "$line" >&3
    done
    # An unterminated final frame is a protocol failure, never a valid DONE.
    if [[ -n $line ]]; then printf 'INVALID\n' >&3; fi
    printf 'EOF\n' >&3
) 5>&- 8>&- 9>&- 3<>"$notice_dir/transport-events" &
relay_pid=$!
exec 4<&-
(
    # Keep prefix completion observable even when Proton spawns Windows and
    # returns early. No copy of the owner writer reaches this job or its child.
    child=
    cancel_prefix() {
        trap '' HUP INT TERM
        for pid in $(jobs -pr); do
            if [[ $pid == "$child" ]]; then kill -KILL "$pid" 2>/dev/null || true; fi
        done
        wait "$child" 2>/dev/null || true
        exit 143
    }
    trap cancel_prefix HUP INT TERM
    "${steam_argv[@]:0:game_index}" "$python_exe" -B -c "$bootstrap" \
        --host-session "$session" --host-log-path "$mod_root/logs/launcher.log" \
        "${launcher_argv[@]}" -- "${steam_argv[@]:game_index+1}" \
        </dev/null 3>&- &
    child=$!
    result=0
    wait "$child" || result=$?
    # Open only for this final write, rather than carrying a descriptor across
    # runtime execution. RDWR avoids an open waiting forever if the supervisor
    # was killed; this event queue uses records, never EOF, for completion.
    printf 'OUTER %s\n' "$result" 3<>"$notice_dir/transport-events" >&3
) 5>&- 8>&- 9>&- &
controller_pid=$!

ready=0
hello=0
closed=0
code=1
deadline=$((SECONDS + 45))
while :; do
    timeout=()
    if (( ready == 0 )); then
        remaining=$((deadline - SECONDS))
        if (( remaining <= 0 )); then trace STARTUP_TIMEOUT; break; fi
        timeout=(-t "$remaining")
    fi
    if ! IFS= read -r "${timeout[@]}" -u 8 line; then trace READ_FAILED; break; fi
    # Only the relay adds CONTROL. Windows cannot forge native waiter/EOF
    # events, and the quoted prefix compares the session literally.
    if [[ $line == "CONTROL ML1 $session "* ]]; then
        native_event=0
        control=${line#"CONTROL ML1 $session "}
    elif [[ $line == OUTER\ * || $line == EOF ]]; then
        native_event=1
        control="NATIVE $line"
    else
        trace INVALID_FRAME
        break
    fi
    if [[ $control == HELLO ]] && (( hello == 0 && ready == 0 )); then
        hello=1
        exec 5>&-
        control_open=0
        trace HELLO
        printf 'ML1 %s START\n' "$session" >&9
    elif [[ $control == READY ]] && (( hello && ready == 0 )); then
        exec 7>"$notice_dir/owner"
        exec 9>&-
        ready=1
        trace READY
    elif [[ $control =~ ^DONE\ ([0-9]{1,3})$ ]] \
        && (( ready )) && [[ -z $done_code ]] && (( 10#${BASH_REMATCH[1]} <= 255 )); then
        done_code=$((10#${BASH_REMATCH[1]}))
        trace "DONE $done_code"
        exec 7>&-
        owner_open=0
    elif (( native_event )) && [[ $control =~ ^NATIVE\ OUTER\ ([0-9]{1,3})$ ]] && [[ -z $outer_code ]]; then
        outer_code=$((10#${BASH_REMATCH[1]}))
        trace "OUTER $outer_code"
        if (( ready == 0 && outer_code != 0 )); then code=$outer_code; break; fi
    elif (( native_event )) && [[ $control == "NATIVE EOF" ]] && (( closed == 0 )); then
        closed=1
        trace EOF
        if [[ -z $done_code ]]; then trace MISSING_DONE; break; fi
    else
        trace INVALID_FRAME
        break
    fi
    if [[ -n $done_code && -n $outer_code ]] && (( closed )); then
        # Windows failure wins over an outer success; a failing outer command
        # must also prevent success when Windows declared a clean completion.
        code=$done_code
        if (( code == 0 )); then code=$outer_code; fi
        break
    fi
done
if (( code != 0 )); then
    printf 'Mod Launcher stopped (status %s). Transport details: %s/transport.txt\n' "$code" "$notice_dir" >&2
fi
exit "$code"
