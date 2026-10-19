# SPDX-License-Identifier: MPL-2.0
"""Keep cached rule data from slowing later validation calls.

Large nested dicts/lists made Python's garbage collector scan unchanged rule
values during later calls, sometimes exhausting their execution allowance. Store
those values as a flat tuple instead; Python can stop tracking its scalar contents
for cyclic garbage collection. The catalog arranges this during installation.
Each validator still gets fresh ordinary dicts/lists, rebuilt without JSON parsing.
"""


def _pack(value, output):
    # A dict starts with (0, item count), a list with (1, item count), and a
    # scalar with (2, value). Dict keys precede their values. For example,
    # {"limit": 3} becomes (0, 1, "limit", 2, 3). No nested containers remain.
    kind = type(value)
    if kind is dict:
        output.extend((0, len(value)))
        for key, child in value.items():
            output.append(key)
            _pack(child, output)
    elif kind is list:
        output.extend((1, len(value)))
        for child in value:
            _pack(child, output)
    else:
        output.extend((2, value))


def _unpack(tokens, cursor):
    position = cursor[0]
    kind, value = tokens[position], tokens[position + 1]
    cursor[0] = position + 2
    if kind == 2:
        return value
    if not value:
        return {} if kind == 0 else []
    if kind == 0:
        result = {}
        for _ in range(value):
            key = tokens[cursor[0]]
            cursor[0] += 1
            result[key] = _unpack(tokens, cursor)
        return result
    return [_unpack(tokens, cursor) for _ in range(value)]


class FrozenValue:
    __slots__ = ("tokens",)

    def __init__(self, value):
        # The caller has already checked types, size and nesting depth. The
        # finished tuple holds only strings, numbers, booleans and None, so
        # Python can remove it from cyclic-GC tracking at its first collection.
        tokens = []
        _pack(value, tokens)
        self.tokens = tuple(tokens)

    def copy(self):
        # A mod may keep and later change its arguments, so each call needs its
        # own dicts/lists. Only this copy is passed to the mod; the tuple stays
        # cached. Recursive reads share one position counter to avoid creating
        # another (value, next position) tuple for every child.
        return _unpack(self.tokens, [0])
