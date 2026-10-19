# SPDX-License-Identifier: MPL-2.0
"""Check project entry-point descriptors against the installed game's bytecode.

No archived function or class body is executed. Body hashes do not include a
method's defaults and annotations, so read those from its class declaration too.
The deliberately small declaration reader rejects operations it does not know.
"""

import argparse
import dis
import inspect
import json
from pathlib import Path
import sys
from types import FunctionType
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archive import GameArchive, code_objects


def declaration(body, name):
    instructions = list(dis.get_instructions(body))
    end = next(i for i, op in enumerate(instructions) if op.opname == "STORE_NAME" and op.argval == name)
    # Read just this method's declaration, between class attribute assignments.
    # The preceding methods and the rest of the class body are never evaluated.
    begin = max(i for i, op in enumerate(instructions[:end]) if op.opname == "STORE_NAME") + 1
    stack = []
    names = {"str": str, "int": int, "float": float, "bool": bool, "Optional": Optional}
    for op in instructions[begin:end]:
        if op.opname in ("NOP", "EXTENDED_ARG"):
            continue
        if op.opname == "LOAD_CONST":
            stack.append(op.argval)
        elif op.opname == "LOAD_NAME":
            stack.append(names[op.argval])
        elif op.opname == "BINARY_SUBSCR":
            key = stack.pop()
            stack.append(stack.pop()[key])
        elif op.opname == "BUILD_TUPLE":
            if op.arg:
                values = stack[-op.arg:]
                del stack[-op.arg:]
                stack.append(tuple(values))
            else:
                stack.append(())
        elif op.opname == "MAKE_FUNCTION":
            # Construct a function object for inspect.signature; never call it.
            stack.append(FunctionType(stack.pop(), {}))
        elif op.opname == "SET_FUNCTION_ATTRIBUTE":
            function, value = stack.pop(), stack.pop()
            if op.argrepr == "annotations":
                function.__annotations__ = dict(zip(value[::2], value[1::2]))
            elif op.argrepr == "defaults":
                function.__defaults__ = value
            else:
                raise ValueError("Unsupported declaration attribute: " + op.argrepr)
            stack.append(function)
        else:
            raise ValueError("Unsupported declaration operation: " + op.opname)
    if len(stack) != 1 or type(stack[0]) is not FunctionType:
        raise ValueError("Incomplete native function declaration")
    return stack[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from hook_manager.regulations.projects import PROJECT_GAME_SHA256, project_targets
    game = GameArchive(args.executable, PROJECT_GAME_SHA256)
    rows, modules = [], {}
    for target in project_targets(game.sha256):
        # Reuse parsed module objects; constructing a function does not call it
        # or install it into sys.modules. _check uses the real runtime resolver's
        # body/decorator checks, not a separate developer fingerprint algorithm.
        if target.module not in modules:
            modules[target.module] = game.module(target.module)
        body = next(code for code in code_objects(modules[target.module]) if code.co_qualname == target.owners[0])
        function = declaration(body, target.attribute)
        target._check(function)
        signature = str(inspect.signature(function))
        if signature != target.signature:
            raise ValueError("Native project signature changed: " + target.name)
        rows.append({"target": target.name, "module": target.module, "function": function.__qualname__,
                     "line": function.__code__.co_firstlineno, "signature": signature,
                     "fingerprints": target.fingerprints})
    print(json.dumps({"game_sha256": game.sha256, "python": sys.version, "targets": rows}, indent=2))


if __name__ == "__main__":
    main()
