# SPDX-License-Identifier: MPL-2.0
"""Owned native CPython client; endpoint credentials travel through private stdin."""

import json
import sys
import time

sys.path.insert(0, sys.argv[1])
from hook_manager.regulations import RegulationChannel

channel = RegulationChannel(json.loads(sys.stdin.readline()))
try:
    print(json.dumps({"connected": True}), flush=True)
    tables = json.loads(sys.stdin.readline())
    channel.publish(native={"native": [{"key": "open"}]})
    print(json.dumps(channel(tables["contexts"], tables["selections"], tables["calls"], time.monotonic() + .1)), flush=True)
finally:
    channel.close()
