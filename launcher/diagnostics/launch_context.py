# SPDX-License-Identifier: MPL-2.0
"""Keep one launch's state beside its logger rather than on the global logger."""

import logging


class LaunchContext(logging.LoggerAdapter):
    """Own activity, output and the lease while retaining standard logging calls."""

    def __init__(self, logger, outcome, activity, ownership=None):
        super().__init__(logger, {})
        self.outcome, self.activity, self.ownership = outcome, activity, ownership
        self.output = self.reporter = None
        self.closed = False

    def bind_log(self, **fields):
        return self.reporter.bind_log(**fields)

    def log(self, level, message, *args, **options):
        if not self.closed:
            super().log(level, message, *args, **options)

    def __getattr__(self, name):
        # Standard logger inspection remains available to trusted callers. All
        # launch resources above belong to this explicit context, not the logger.
        return getattr(self.logger, name)
