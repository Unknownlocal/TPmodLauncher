# SPDX-License-Identifier: MPL-2.0
"""Shared JSON decoding and selective materialization for trusted adapters."""

from .decoder import decode
from .json_object import JsonObject, materialize

__all__ = ["decode", "JsonObject", "materialize"]
