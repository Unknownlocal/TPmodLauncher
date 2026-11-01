# Diagnostics architecture

This package owns the launcher's console and file logging, fatal failure details
and requests to close its game processes.

`core.py` claims log ownership, configures the logger and keeps the original fatal
reason through cleanup. It flushes and synchronises file output before returning
the final write result. Normal package imports use the functions exported by
`__init__.py`. Startup loads `core.py` directly by a fixed path so dependency
verification failures can be logged before other launcher packages are available.

`log_file_handler.py` contains `LogFileHandler`. It retains file-write failures
that standard Python logging could otherwise suppress. The console can still
show the original error when the log cannot be written.

`console_shutdown.py` contains `ConsoleShutdown`. On Windows it requests normal
window closure for verified, owned game processes. After four seconds it closes
any survivors forcibly. Its process handles protect unrelated game instances.

The shared plain-text record format comes from `hook_manager.diagnostics`, which
also runs inside the game. No dialog participates in writing or completing logs.
