# SPDX-License-Identifier: MPL-2.0
"""Connect a combined UI snapshot to native adapters or collectors in scenarios."""


def ui_sink(*, menu=None, settings=None, themes=None, calls=None):
    callbacks = {"menu": menu, "settings": settings, "themes": themes}
    def send(snapshot):
        if calls is not None:
            calls.append(snapshot)
        response = {}
        for area, value in snapshot.items():
            callback = callbacks[area]
            result = None
            if callback is not None:
                result = callback(value["records"], value["result"]) if area == "settings" else callback(value)
            response[area] = {"generation": 0, "state": "active"} if result is None else result[area]
        return response
    return send
