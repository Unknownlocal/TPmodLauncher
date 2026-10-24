# SPDX-License-Identifier: MPL-2.0
"""Build entry for the trusted Python-in-Wasm runner.

Set up the author API and source-file guard before constructing the loader.
Componentize-py finds RunnerBindings here; its implementation lives in _loading.
"""

# Freeze this standard-library profile so author code can import it later.
import json
import math
import os
import sys

from _loading.author_world import install_author_api
from _loading.source_loader import reject_source_file_reads

install_author_api()
sys.addaudithook(reject_source_file_reads)

# Import last: the loader reserves trusted module names after the API is ready.
from _loading.runner_bindings import RunnerBindings
