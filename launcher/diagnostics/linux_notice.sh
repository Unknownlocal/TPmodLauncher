#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Native error UI. All error text is data; never evaluate it as shell syntax.
set -uo pipefail
notice_dir=$1
log_path=$2
port=$3
token=$4
mode=${5:-console}
script=$(realpath -e -- "${BASH_SOURCE[0]}")

write_error() {
    printf '%s\n' "$1" >&2
    # Only the completed, owned log can receive presentation details. The
    # Windows controller retains its lock throughout this notice session.
    if [[ -f "$notice_dir/log-complete" ]]; then
        printf '\nPRESENTATION ERROR %s\n' "$1" >> "$log_path" || true
    fi
}

if [[ $mode == console && ( ! -t 0 || ! -t 2 ) ]]; then
    child=
    close_terminal() {
        if [[ -n $child ]]; then
            if [[ -f "$notice_dir/connected" ]]; then signal=TERM; else signal=KILL; fi
            kill -"$signal" "$child" 2>/dev/null || true
            wait "$child" 2>/dev/null || true
        fi
    }
    trap close_terminal EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    if command -v xterm >/dev/null && [[ -n ${DISPLAY:-} ]]; then
        xterm -T 'Mod Launcher' -e bash "$script" "$notice_dir" "$log_path" "$port" "$token" session &
        child=$!
        wait "$child" || true
        child=
        # A failed terminal launch must not suppress the dialog/console path.
        # Check once after it exits; never retry a notice already dismissed.
        if [[ -f "$notice_dir/connected" ]]; then exit 0; fi
        write_error 'Could not start the Linux launcher console; continuing on inherited output.'
    else
        write_error 'A visible Linux launcher console requires xterm and an X11/XWayland display.'
    fi
fi

cat -- "$notice_dir/notice.txt" >&2
printf '%s\n' 'Press any key to close this notice and end the launcher console session.' >&2
if ! exec 3<>"/dev/tcp/127.0.0.1/$port"; then
    write_error 'Could not connect the Linux notice to its controller.'
    exit 1
fi
printf '%s\n' "$token" >&3
if ! IFS= read -r -t 5 -u 3 response || [[ $response != ready ]]; then
    write_error 'The controller did not accept this Linux notice.'
    exit 1
fi
: > "$notice_dir/connected"

# A private FIFO lets completed workers queue events even before the main read
# starts. There are no wait -n races, timed input checks or periodic file reads.
if ! mkfifo -- "$notice_dir/events"; then exit 1; fi
exec 4<>"$notice_dir/events"
key_pid= owner_pid= dialog_worker= previous_mode=
finish() {
    trap '' HUP INT TERM
    for pid in "$key_pid" "$owner_pid" "$dialog_worker"; do
        if [[ -n $pid ]]; then kill -TERM "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
    done
    if [[ -n $previous_mode ]]; then stty "$previous_mode" < /dev/tty || true; fi
    exec 3>&- 4>&-
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Each child closes the socket except its one blocking owner-loss reader. An
# editor must never inherit the lifetime connection and keep the controller alive.
(IFS= read -r -u 3 ignored || true; printf 'owner\n' >&4) &
owner_pid=$!
if [[ -t 0 ]]; then
    previous_mode=$(stty -g < /dev/tty)
    (if IFS= read -r -s -n 1 < /dev/tty; then printf 'key\n' >&4; else printf 'input-error\n' >&4; fi) 3>&- &
    key_pid=$!
else
    write_error 'No interactive Linux console input; dismiss the dialog or stop the launcher to close this notice.'
fi

# xdg-open dispatches the host-native path. Ignore HUP and detach its streams so
# the editor remains independent when the console or dialog is dismissed.
if [[ -f "$notice_dir/log-complete" ]]; then
    if command -v xdg-open >/dev/null; then
        (trap '' HUP; xdg-open "$log_path" > "$notice_dir/editor-error.txt" 2>&1
         code=$?; if (( code != 0 )); then printf 'editor-error\n' >&4; fi) 3>&- &
    else
        write_error 'Automatic log opening requires xdg-open (xdg-utils); use the printed log path.'
    fi
fi

if command -v zenity >/dev/null && [[ -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]]; then
    (
        dialog=
        close_dialog() {
            if [[ -n $dialog ]]; then kill -KILL "$dialog" 2>/dev/null || true; wait "$dialog" 2>/dev/null || true; fi
        }
        trap close_dialog EXIT
        trap 'exit 0' HUP INT TERM
        # --no-markup makes exception messages literal even when they contain
        # GTK markup, quotes, dollar signs or shell metacharacters.
        ZENITY_OK=20 ZENITY_CANCEL=21 ZENITY_ESC=21 ZENITY_ERROR=22 ZENITY_TIMEOUT=22 ZENITY_EXTRA=22 \
            zenity --error --no-markup --title='Mod Launcher could not continue' \
            --ok-label=Close --width=600 --text="$(< "$notice_dir/notice.txt")" \
            3>&- 4>&- > "$notice_dir/dialog-output.txt" 2> "$notice_dir/dialog-error.txt" &
        dialog=$!
        wait "$dialog"
        code=$?
        dialog=
        # Reserved Zenity response codes distinguish real dismissal from GTK
        # startup failures (often 1), even when a displayed dialog logs warnings.
        if (( code == 20 || code == 21 )); then
            printf 'dialog-closed\n' >&4
        else
            printf 'dialog-error\n' >&4
        fi
    ) 3>&- &
    dialog_worker=$!
else
    write_error 'Native Linux error dialogs require zenity and a desktop display; the console remains available.'
fi

while IFS= read -r -u 4 event; do
    case $event in
        key|dialog-closed|owner) break ;;
        input-error) write_error 'Linux console input closed; dismiss the dialog or close the launcher window.' ;;
        dialog-error) write_error "Linux error dialog failed: $(< "$notice_dir/dialog-error.txt")" ;;
        editor-error) write_error "Linux log-opening request failed: $(< "$notice_dir/editor-error.txt")" ;;
    esac
done
