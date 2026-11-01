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

The game's navigation and loading-job observations arrive through the existing
bridge status messages, including packs with no code mods. Game diagnostics use
a cached plain snapshot, so workers do not inspect live screens to build a log
record. Logs describe confirmed activity rather than inferring it from error text.

`log_file_handler.py` contains `LogFileHandler`. It retains file-write failures
that standard Python logging could otherwise suppress. The console can still
show the original error when the log cannot be written.

`console_shutdown.py` contains `ConsoleShutdown`. On Windows it requests normal
window closure for verified, owned game processes. After four seconds it closes
any survivors forcibly. Its process handles protect unrelated game instances.

The shared plain-text record format comes from `hook_manager.diagnostics`, which
also runs inside the game. No dialog participates in writing or completing logs.
