# Diagnostics architecture

This package owns the launcher's console and file logging, fatal failure details
and requests to close its game processes.

The console and file default to INFO. Entry's `--log-level` option selects their
shared minimum level; DEBUG includes startup and code-mod timings. Fatal errors
and the final failure details bypass the routine threshold in both destinations
while keeping their original severity.
`core.parse_log_level()` maps 1-5 onto DEBUG through CRITICAL. Names and standard
numeric thresholds remain accepted, so 4, ERROR and 40 are equivalent.
Entry validates the option before opening the log. The Linux wrapper forwards
it unchanged to the same parser.
`core.startup_step()` records the start and finish of setup operations, with
elapsed real time and a failed result if the operation raises an exception.
These records cover dependency and runner checks, source preparation and process
setup. They run at those boundaries rather than checking on a timer.
The bridge adds one-time runtime detection and readiness wait messages, followed
by source-loading and observer setup durations from inside the game. Each source
duration includes compiling its Python text and executing it, including imports.
This separates time waiting for game readiness from time spent in launcher code.
Before loading the full bootstrap bundle, `hook_manager.bootstrap_readiness`
checks the existing main-thread and import state using a small expression.
Startup waits therefore avoid repeatedly parsing all the adapter sources.
The completion record counts readiness checks and full bootstrap evaluations
separately. Bound controller logging keeps the original DEBUG level and process
context, so the selected threshold works consistently in both destinations.
`write_log()` also recognises bound logging callbacks before rendering text,
so nested forwarding preserves record levels, context and full errors. Only
plain text observers receive the final formatted text.

`core.py` claims log ownership, configures the logger and keeps the original fatal
reason through cleanup. It flushes and synchronises file output before returning
the final write result. Normal package imports use the functions exported by
`__init__.py`. Startup loads `core.py` directly by a fixed path so dependency
verification failures can be logged before other launcher packages are available.

`activity_tracker.py` contains `ActivityTracker`. It records startup, game loading,
mod loading and career activity for each owned process. Nested work keeps its
parent operation, and worker context stays separate. A failure keeps the activity
where it arose, even after that work ends or cleanup starts. Mod identities use
their manifest IDs; shared preparation names the group rather than guessing an
owner. Late activity updates cannot replace a newer career or process session.

The game's navigation, loading and UI changes arrive as journal events, including
packs with no code mods. Each status kind has ordered updates, so a delayed
fallback cannot replace newer settings or readiness. Requested bridge status
replies read cached values. Game diagnostics use a cached plain snapshot to build
log records, and describe confirmed activity rather than guessing from error text.

Whole week progression and saving arrive from the game's operation observer.
Native operation and parent identities remain separate from controller records,
so a delayed worker error keeps its saving/week context after navigation changes.
Generic unhandled main-thread failures use that same context. Worker exceptions
remain diagnostics unless the game also terminates; original exception hooks and
the game's own crash recording still run.

`activity_journal.py` contains `ActivityJournal`. Each bridge owns a new journal
file. `journal_writer.py` contains `JournalWriter`, which formats captured errors,
encodes records and performs file writes, flushes and synchronisation on its own
thread. Game callbacks only capture context and enqueue; the writer holds no
activity lock while doing storage work. It batches work already queued, without
waiting for a timer. Each batch uses one open, write and flush. An error or fatal
record ends its batch and synchronises it before later records are formatted.
The controller reads completed records
when notified and before discarding an exited process, without entering the game
or scanning the journal periodically.
It then removes only its own journal. If an interrupted write corrupts
a record, the error includes its raw contents and the journal stays available.
Already decoded records keep their place before socket fallbacks, so a later
damaged append cannot replace the first fatal reason.
The normal logger still owns
`launcher.log`; an unhandled native exception follows the same fatal outcome and
cleanup path even if no further game pulse can run.

`reporting_sender.py` contains `ReportingSender`. Its own worker sends journal
notifications over an authenticated local connection, so a blocked socket does
not delay journal appends. If a journal write fails, this connection carries the
warning and complete original record instead. `reporting_wire.py` splits long
messages into numbered frames without truncating their error details.

`reporting_queue.py` shares the backlog policy across the writer, socket sender
and receiver. Each queue holds at most 256 pending ordinary informational
records. When full, it removes the oldest ordinary record and produces a WARNING
with the exact drop count. Failures, warnings, save requests, loading attempts
and control events remain protected. Healthy readiness, menu and theme hints
keep only their latest pending value. Complete errors and protected events have
no fixed memory limit; the queue limit is not a total memory guarantee.

`reporting_receiver.py` contains `ReportingReceiver`. It waits for socket data,
checks the process/session identity and message order, and queues controller
events. Control completion messages arm entry for their matching request, even
while the controller is waiting for its reply. The receiver performs no file
logging or game/mod calls. Idle reception has no periodic timeout; startup and
incomplete transfers have their own deadlines.
Repeated journal notices share one pending controller wake. The receiver rearms
that wake before reading, so a concurrent arrival cannot be missed.
The controller reads the journal and any fallback records on those events.
Connection failure triggers recovery and retains the journal; an acknowledged
stop consumes final records before the owned session is discarded.

Stopping observation signals the writer without waiting on the game thread. The
launcher waits for final delivery on its reporting connection after native
hooks have been removed, without another game-thread request.
An unconfirmed shutdown keeps the journal. Fatal handling and final interpreter
termination share one one-second drain budget across the writer and sender.
Ordinary gameplay never waits for that drain. A stalled backend may leave queued
records unwritten at the limit.

`exception_snapshot.py` contains `ExceptionSnapshot`. Game threads copy only
messages, notes, exception relationships and frame locations from memory. This
avoids Python's usual traceback source-cache checks, which can read the filesystem
even when line lookup is disabled. The writer resolves source lines and applies
standard traceback formatting later, without keeping live frames or local values.
If source lookup or formatting fails, the log retains the complete captured
messages, notes, relationships and frame locations as plain snapshot data.

`log_file_handler.py` contains `LogFileHandler`. It retains file-write failures
that standard Python logging could otherwise suppress. The console can still
show the original error when the log cannot be written.

`console_shutdown.py` contains `ConsoleShutdown`. On Windows it requests normal
window closure for verified, owned game processes. After four seconds it closes
any survivors forcibly. Its process handles protect unrelated game instances.

The shared plain-text record format comes from `hook_manager.diagnostics`, which
also runs inside the game. No dialog participates in writing or completing logs.

For normal console entry, `entry.py` presents fatal outcomes after game cleanup
and after closing the log. It retains the log lock while the notice is open, so a
second launch cannot replace the file being displayed. Code calling `main`
directly can handle the returned outcome itself. Dependency-check commands
print the summary without waiting for input.

`failure_session.py` contains `FailureSession`. It always prints the failed
activity, short reason and log path before starting presentation work. It waits
for a real key or dialog dismissal. Failure to start or display UI leaves the
console available. Presentation errors are appended to the completed log without
replacing the original failure. A launch that never owned the log cannot append
to it or describe another launch's file as its completed log.

`windows_console.py` restores the attached Windows console, or creates one if
needed, and opens its input and output without replacing global Python streams.

`console_input.py` contains `ConsoleInput`. It waits for a Windows console key or
a Linux terminal byte without requiring Enter. Cancellation wakes the wait and
restores terminal settings. Missing input and EOF leave the notice open without
repeated input checks.

`presentation_child.py` contains `PresentationChild`. It starts an isolated
helper asynchronously, with a deadline for dialog startup. That deadline cannot
end the user's console-reading time. Dismissal closes only the owned helper,
including one that starts late. `presentation_helper.py` watches the owner's
input pipe before starting UI; losing the owner ends the helper and its windows.
Dialog and log-opening functions run independently. Windows notices select the
Windows functions by a fixed source path, without loading game or mod packages.
Other hosts retain the console notice when no dialog function is supplied.

The Proton entry route selects `linux_failure_channel.py`. `LinuxFailureChannel`
keeps entry's completed log locked and records this launch's outcome, then runs
`FailureSession` in console-only mode. The notice shows the failed activity,
stage, reason and host-native log path, rather than a guessed Wine drive mapping.
Early import failures include their full traceback. Failure to save the separate
outcome file cannot suppress the console notice or claim another launch's log.

The user's host selects desktop features; the running interpreter selects console
APIs. Windows Python under Proton opens its own console input/output even though
the host is Linux. Game stdin stays null. A keypress, console closure or native
supervisor loss ends the notice. Missing input does not silently dismiss it.
There is no user-reading deadline or periodic input check.

Linux desktop features have early-exit guards in both `FailureSession` and
`presentation_helper.py`. `linux_dialog.py` and `linux_log.py` contain unimplemented
adapter interfaces. `linux_notice.sh` is a compatibility guard that exits without
starting a terminal, dialog or editor. The user opens the printed log manually;
no Linux desktop packages are required.

`host_channel.py` opens private owner and control pipes through Windows Python's
own mapped installation path. A bounded exchange checks the session before
dependency imports and game startup. Console input and output carry no control
messages. Wine's working FIFO handles can be character devices; an actual
console handle is refused instead of assuming a particular device type.

`proton_supervisor.sh` creates the private session and joins control messages,
Windows pipe closure and the outer Proton process result through blocking pipe
reads. The controller sends its final result explicitly; neither a missing result
nor a failing outer command can be treated as success. Startup/transport failures
retain their own session details without claiming an earlier launch's log.

`host_lifetime.py` contains `HostLifetime`. It blocks on the explicitly opened
owner pipe whose only writer belongs to the native supervisor. Losing that
writer requests the existing owned-game shutdown, including the shared four-second
fallback, even when Proton spawned the controller instead of replacing itself.
The watcher owns a private, non-inheritable pipe handle; controller and game stdin are then
set to null input. This keeps the lifetime read separate from game startup.
If cleanup remains stuck after forced game closure and a short final grace,
the controller exits too, preserving failure or interruption status.
Owner loss also queues console dismissal directly, so a retained failure does
not wait for keyboard input after its native supervisor ends.

`windows_dialog.py` contains `WindowsDialog`. It uses a native Windows window
with the failed activity, short reason and a scrollable, selectable log path.
It announces readiness after the window is visible. Closing the window, pressing
Escape or choosing Close dismisses the session. Closing the console ends the
helper and its window; it never searches for other windows by title.

`windows_log.py` opens the completed native log path in system Notepad through
the Windows shell API. The path is passed as one argument, without a command
shell or file-association prompt. Opening the editor runs on its own worker. The
editor does not belong to the dismissal session and remains available after
the console and dialog close. An editor failure is recorded in the original log
and leaves the console and dialog available.
