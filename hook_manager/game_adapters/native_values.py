# SPDX-License-Identifier: MPL-2.0
"""Remember native values and container identities without serializing game data."""


def native_value(value, seen=None):
    """Describe mutable dict/list contents, preserving identity and shared paths."""
    seen = set() if seen is None else seen
    kind = type(value)
    if isinstance(value, (dict, list)):
        # Native helpers also accept dict/list subclasses. Compare their contents
        # and identities: replacing a record with an equal copy still changes the
        # object that native code will use. Stop shared paths/cycles at the identity.
        if id(value) in seen:
            return kind, id(value)
        seen.add(id(value))
        children = (tuple((key, native_value(item, seen)) for key, item in value.items())
                    if isinstance(value, dict) else tuple(native_value(item, seen) for item in value))
        return kind, id(value), children
    # Other game objects stay borrowed. Their callers choose which fields matter;
    # walking arbitrary objects here would turn a small check into a career copy.
    return (kind, value) if kind in (str, int, float, bool, type(None)) else (kind, id(value))
