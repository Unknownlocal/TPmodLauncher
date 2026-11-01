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
fallback cannot replace newer settings or readiness. Ordinary bridge status
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
activity lock while doing storage work. The controller reads completed records
when notified and before discarding an exited process, without entering the game
or scanning the journal periodically.
It then removes only its own journal. If an interrupted write corrupts
a record, the error includes its raw contents and the journal stays available.
The normal logger still owns
`launcher.log`; an unhandled native exception follows the same fatal outcome and
cleanup path even if no further game pulse can run.

`reporting_sender.py` contains `ReportingSender`. Its own worker sends journal
notifications over an authenticated local connection, so a blocked socket does
not delay journal appends. If a journal write fails, this connection carries the
warning and complete original record instead. `reporting_wire.py` splits long
messages into numbered frames without truncating their error details.

`reporting_receiver.py` contains `ReportingReceiver`. It waits for socket data,
checks the process/session identity and message order, and queues controller
events. It performs no file logging or game/mod calls. Idle reception has no
periodic timeout; startup and incomplete transfers have their own deadlines.
The controller reads the journal and any fallback records on those events.
Connection failure triggers recovery and retains the journal; an acknowledged
stop consumes final records before the owned session is discarded.

Stopping observation signals the writer without waiting on the game thread. The
launcher waits for its acknowledgement through repeated short bridge requests.
An unconfirmed shutdown keeps the journal. Fatal handling and final interpreter
termination share one one-second drain budget across the writer and sender.
Ordinary gameplay never waits for that drain. A stalled backend may leave queued
records unwritten at the limit.

`exception_snapshot.py` contains `ExceptionSnapshot`. Game threads copy only
messages, notes, exception relationships and frame locations from memory. This
avoids Python's usual traceback source-cache checks, which can read the filesystem
even when line lookup is disabled. The writer resolves source lines and applies
standard traceback formatting later, without keeping live frames or local values.

`log_file_handler.py` contains `LogFileHandler`. It retains file-write failures
that standard Python logging could otherwise suppress. The console can still
show the original error when the log cannot be written.

`console_shutdown.py` contains `ConsoleShutdown`. On Windows it requests normal
window closure for verified, owned game processes. After four seconds it closes
any survivors forcibly. Its process handles protect unrelated game instances.

The shared plain-text record format comes from `hook_manager.diagnostics`, which
also runs inside the game. No dialog participates in writing or completing logs.
