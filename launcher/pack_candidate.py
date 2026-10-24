# SPDX-License-Identifier: MPL-2.0
"""Keep discovered metadata separate from source and component execution."""

from dataclasses import dataclass
from pathlib import Path

from launcher.mod_contract import Manifest
from mod_runtime.source import SourceManifest


@dataclass
class PackCandidate:
    root: Path
    manifest: Manifest | SourceManifest
    timings: dict
