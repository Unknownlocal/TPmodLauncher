# SPDX-License-Identifier: MPL-2.0
"""Trusted Python-in-Wasm runner artifacts, independent of launcher/game lifetimes.

Importing this package never loads a compiler, Wasmtime, a runner or mod source.
Player startup verifies the installed bundle before selected-pack source loading.
"""

from .bundle import ABI_VERSION, BundleError, RunnerBundle

__all__ = ["ABI_VERSION", "BundleError", "RunnerBundle"]
