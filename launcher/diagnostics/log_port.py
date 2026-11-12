# SPDX-License-Identifier: MPL-2.0
"""Keep bound logging context without repeatedly interpreting the destination."""


class LogPort:
    """Carry one adapted writer and its explicit launch owner across module loads."""

    def __init__(self, submit_log, *, owner=None, fields=None):
        self._submit_log = submit_log
        self.owner = owner
        self.fields = {} if fields is None else fields

    def bind_log(self, **fields):
        if not fields:
            return self
        return type(self)(self._submit_log, owner=self.owner, fields={**self.fields, **fields})

    def __call__(self, value, **options):
        return self._submit_log(value, **{**self.fields, **options})
