# SPDX-License-Identifier: MPL-2.0
"""Loading allowances and UTF-8 chunks used by the trusted source adapters."""

from launcher.sandbox import Limits
from ._source_policy import MAX_SOURCE_BYTES, MAX_SOURCE_FILES

# Shared source loading gives each owner's transfer and import separate allowances.
# Activation/events keep their ordinary five-million fuel/one-second limit.
SOURCE_FUEL = 256_000_000
SOURCE_SECONDS = 5.0
RUNNER_LIMITS = Limits(initialization_fuel=20_000_000, initialization_seconds=5.0, instances=64)
CHUNK_BYTES = 4096
# Each full UTF-8 chunk consumes at least 4093 bytes; each file has one tail.
MAX_TRANSFER_CALLS = MAX_SOURCE_FILES + MAX_SOURCE_BYTES // (CHUNK_BYTES - 3)


def _chunks(data):
    """Split bytes without cutting a UTF-8 character at a chunk boundary."""
    if not data:
        yield ""
    start = 0
    while start < len(data):
        end = min(start + CHUNK_BYTES, len(data))
        while end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        yield data[start:end].decode("utf-8")
        start = end
