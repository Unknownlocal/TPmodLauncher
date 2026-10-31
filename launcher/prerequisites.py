# SPDX-License-Identifier: MPL-2.0
"""Apply extra requirements to individual registrations, never to mod activation."""


class Prerequisites:
    """Keep checked declarations and reuse decisions until readiness changes."""

    def __init__(self, candidates=()):
        self.declarations = {(candidate.manifest.id, registration): requirements
            for candidate in candidates for registration, requirements in candidate.manifest.prerequisites}
        names = {name for requirements in self.declarations.values() for name in requirements}
        self.request = {"career": "career.loaded" in names,
                        "modules": sorted(name[7:] for name in names if name.startswith("module:"))}
        self.native = {"career": None, "modules": [], "watching": False, "detail": ""}
        self._state, self._allowed = None, set()

    def receive(self, record):
        """Accept only the requested native availability, with no game references."""
        if (type(record) is not dict or set(record) != {"career", "modules", "watching", "detail"}
                or record["career"] is not None and (type(record["career"]) is not int or record["career"] < 1)
                or type(record["modules"]) is not list or len(record["modules"]) > 256
                or any(type(name) is not str or name not in self.request["modules"] for name in record["modules"])
                or type(record["watching"]) is not bool or type(record["detail"]) is not str
                or len(record["detail"]) > 4096):
            raise ValueError("Invalid native prerequisite availability")
        self.native = {**record, "modules": sorted(set(record["modules"]))}

    def update(self, active, regulations):
        """Evaluate only after a mod, career, regulation or module changes state."""
        active = frozenset(active)
        state = active, regulations, self.native["career"], tuple(self.native["modules"])
        if state == self._state:
            return
        self._state = state
        available = {"mod:" + owner for owner in active}
        available.update("module:" + name for name in self.native["modules"])
        if self.native["career"] is not None:
            available.add("career.loaded")
        if regulations:
            available.add("regulations.active")
        self._allowed = {key for key, names in self.declarations.items() if available.issuperset(names)}

    def allows(self, owner, registration):
        return (owner, registration) not in self.declarations or (owner, registration) in self._allowed
