# SPDX-License-Identifier: MPL-2.0
"""Store cached rule data as flat tuples to reduce garbage-collection work.

Python can stop checking tuples of scalar values for reference cycles. The
catalogue arranges this during installation, so later calls avoid rescanning
unchanged rule containers. Each validator gets fresh dictionaries and lists
rebuilt from these tuples without parsing JSON.

Short-lived voting snapshots can instead keep scalar-only container templates.
Their copies use Python's built-in container copying and visit nested containers
only. Persistent rule caches retain the flat storage used to avoid GC scans.
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


def _templates(value):
    kind = type(value)
    if kind is not dict and kind is not list:
        return value, None
    template, children = value.copy(), []
    for key, child in (value.items() if kind is dict else enumerate(value)):
        if type(child) is dict or type(child) is list:
            children.append((key, _templates(child)))
            template[key] = None
    # Cached containers hold scalars only. Nested mutable values are separate
    # templates, so no caller can receive a reference into the cached tree.
    return template, tuple(children)


def _copy_templates(node):
    template, children = node
    if children is None:
        return template
    result = template.copy()
    for key, child in children:
        result[key] = _copy_templates(child)
    return result


class FrozenValue:
    __slots__ = ("tokens", "_template")

    def __init__(self, value, *, templates=False):
        self._template = _templates(value) if templates else None
        if templates:
            self.tokens = ()
            return
        # The caller has already checked types, size and nesting depth. The
        # finished tuple holds only strings, numbers, booleans and None, so
        # Python can remove it from cyclic-GC tracking at its first collection.
        tokens = []
        _pack(value, tokens)
        self.tokens = tuple(tokens)

    def copy(self):
        # A mod may keep and later change its arguments, so each call needs its
        # own dicts/lists. Cached storage never leaves this helper.
        # Flat reads share one position counter to avoid creating
        # another (value, next position) tuple for every child.
        return _copy_templates(self._template) if self._template is not None else _unpack(self.tokens, [0])
