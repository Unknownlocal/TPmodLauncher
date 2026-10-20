# SPDX-License-Identifier: MPL-2.0
"""Give native planning helpers private inputs, leaving career data untouched.

Some staff helpers initialize project dictionaries. Copy only the headquarters,
staff assignments and package choice that planning reads, rather than an entire
team or career. The checked methods run on temporary manager objects. Existing
budget/package definitions are borrowed read-only; those methods never edit them.
"""

from types import MethodType, SimpleNamespace

from ...manager import HookError, data_copy


def _mapping(value):
    if type(value) is not dict:
        raise HookError("Expected native project planning data")
    return value


def _facilities(value):
    source = _mapping(value)
    return data_copy({key: source[key] for key in ("wind_tunnel", "cfd_center") if key in source})


def _assignments(projects, *, settings):
    result = {}
    for key, project in _mapping(projects).items():
        if type(project) is not dict:
            if project is not None:
                raise HookError("Expected a native project object")
            result[key] = None
        elif settings:
            row = _mapping(project.get("settings") or {})
            # Native capacity helpers skip empty records. Preserve their emptiness
            # instead of turning them into a truthy settings-only project.
            result[key] = {"settings": data_copy({k: row[k] for k in ("staff", "technical_staff") if k in row})} if project else {}
        else:
            result[key] = {"staff": data_copy(project.get("staff", 0))}
    return result


def _manager(original, state, methods):
    # Bypass initialization: it may load data, register callbacks or change state.
    # A shallow attribute copy preserves native configuration/class constants;
    # checked helpers below only write to the separately copied team inputs.
    result = object.__new__(type(original))
    vars(result).update(vars(original))
    result.state = state
    for name, (function, static) in methods.items():
        vars(result)[name] = function if static else MethodType(function, result)
    return result


class ProjectSnapshot:
    def __init__(self, source, team_name, methods):
        original = _mapping(source.teams[team_name])
        self.reference = "team:" + str(id(original))
        aero = _mapping(original.get("aero", {}))
        chassis = _mapping(original.get("development", {}))
        self.team = {
            "headquarters": _facilities(original.get("headquarters", {})),
            "hq_upkeep_pct": _facilities(original["hq_upkeep_pct"]) if type(original.get("hq_upkeep_pct")) is dict else None,
            "aero": {"projects": _assignments(aero.get("projects", {}), settings=True)},
            "development": {"projects": _assignments(chassis.get("projects", {}), settings=True)},
            "driver_aids": _assignments(original.get("driver_aids") or {}, settings=False),
            "ers_development": {"projects": _assignments(_mapping(original.get("ers_development") or {}).get("projects") or {}, settings=False)},
        }
        if "current_package_key" in chassis:
            self.team["development"]["current_package_key"] = data_copy(chassis["current_package_key"])
        # Native upkeep calculations recognize the player's team by identity.
        # Keep that relationship in this temporary lookup; another team's record
        # is never needed by the checked helpers and is represented by None.
        player = getattr(source, "player_team", None)
        teams = {player: self.team if source.teams.get(player) is original else None, team_name: self.team}
        self.state = SimpleNamespace(
            teams=teams, player_team=player, season_no=source.season_no, week_index=source.week_index,
            HQ_DEGRADABLE_BUILDINGS=tuple(source.HQ_DEGRADABLE_BUILDINGS),
            regulation_ers_mode=getattr(source, "regulation_ers_mode", ""),
            regulation_ers_enabled=getattr(source, "regulation_ers_enabled", True),
        )
        getter, _ = methods["state"]["get_hq_upkeep_pct"]
        self.state.get_hq_upkeep_pct = MethodType(getter, self.state)
        self.state.aero_manager = _manager(source.aero_manager, self.state, methods["aero"])
        self.state.development_manager = _manager(source.development_manager, self.state, methods["chassis"])
        self.state.ers_development_manager = (_manager(source.ers_development_manager, self.state, methods["ers"])
                                              if "ers" in methods else None)
