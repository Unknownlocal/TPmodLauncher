# Diagnostics architecture

This package owns the launcher's console and file logging, fatal failure details
and requests to close its game processes.

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

`console_input.py` contains `ConsoleInput`. It waits for a Windows console key or
a Linux terminal byte without requiring Enter. Cancellation wakes the wait and
restores terminal settings. Missing input and EOF leave the notice open without
repeated input checks.

`presentation_child.py` contains `PresentationChild`. It starts an isolated
helper asynchronously, with a deadline for dialog startup. That deadline cannot
end the user's console-reading time. Dismissal closes only the owned helper,
including one that starts late. `presentation_helper.py` watches the owner's
input pipe before starting UI; losing the owner ends the helper and its windows.
Dialog and log-opening functions run independently. The current default helper
reports unavailable presentation functions, leaving the printed console notice
in place. An independently opened editor does not belong to the dismissal session.
