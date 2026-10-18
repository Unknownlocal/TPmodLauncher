# SPDX-License-Identifier: MPL-2.0
"""Read trusted CPython code and already-loaded namespaces without calling them.

The provider supplies code roots whose artifact/owner it has already verified.
Names, __module__, filenames and __wrapped__ attributes are not ownership proof.
Live code/function references stay private to the interpreter running this code.
"""

import inspect
import hashlib
import json
import marshal
from types import CodeType, FunctionType, ModuleType


class DiscoveryError(ValueError):
    """Discovery is unsupported or exceeded its finite inspection budget."""


def _identity_hash(value):
    # Use the catalogue's canonical encoding on both sides of the runtime relay.
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compiled_source_identity(root, descriptors):
    """Return fingerprint input bytes and the compiled source identity, not code to load."""
    # Format 2 ignores reference-sharing flags. Inventory paths also distinguish
    # shared constants from separate equal code entries that format 2 omits.
    content = marshal.dumps(root, 2)
    return content, _identity_hash(["code", hashlib.sha256(content).hexdigest(), descriptors])


def compiled_function_id(owner_key, artifact, module, path, source_hash, discriminator):
    """Reproduce a compiled catalogue function ID from the verified module root."""
    return _identity_hash([owner_key, artifact, module, path, source_hash, discriminator])


def code_inventory(roots, *, maximum_functions=4096, maximum_objects=16384, maximum_constants=65536):
    """Return descriptor rows and their private discriminator -> code mapping.

    Roots must be actual admitted code objects, not bytes to deserialize. This
    works with retained module code or with function code supplied by a trusted
    runtime provider. No code is executed and no target module is imported.
    """
    if type(roots) not in (tuple, list) or not roots:
        raise DiscoveryError("Expected trusted CPython code roots")
    if any(type(limit) is not int or limit <= 0 for limit in (maximum_functions, maximum_objects, maximum_constants)):
        raise DiscoveryError("Inspection limits must be positive integers")
    if len(roots) > maximum_objects:
        raise DiscoveryError("Code object limit")
    if any(type(code) is not CodeType for code in roots):
        raise DiscoveryError("Expected trusted CPython code roots")
    stack = [(code, str(index)) for index, code in reversed(tuple(enumerate(roots)))]
    seen, rows, codes, constants = set(), [], {}, 0
    while stack:
        code, discriminator = stack.pop()
        if len(discriminator) > 512:
            raise DiscoveryError("Code path limit")
        if id(code) in seen:
            continue
        seen.add(id(code))
        if len(seen) > maximum_objects:
            raise DiscoveryError("Code object limit")
        if type(code.co_consts) is not tuple:
            raise DiscoveryError("Code constants must be an ordinary tuple")
        constants += len(code.co_consts)
        if constants > maximum_constants:
            raise DiscoveryError("Code constant limit")
        # Module and class bodies contribute descendants but are not functions.
        if code.co_flags & inspect.CO_NEWLOCALS:
            kind = "coroutine" if code.co_flags & inspect.CO_COROUTINE else "function"
            if code.co_flags & (inspect.CO_GENERATOR | inspect.CO_ASYNC_GENERATOR):
                kind = "generator"
            rows.append([code.co_qualname, code.co_firstlineno, discriminator, kind])
            codes[discriminator] = code
            if len(rows) > maximum_functions:
                raise DiscoveryError("Too many discovered functions")
        # Reverse pushes keep source constant order in this LIFO traversal.
        for index, child in reversed(tuple(enumerate(code.co_consts))):
            if type(child) is CodeType:
                stack.append((child, discriminator + "." + str(index)))
                if len(stack) + len(seen) > maximum_objects:
                    raise DiscoveryError("Code object limit")
    return rows, codes


def loaded_bindings(module, codes, *, maximum_members=16384, maximum_depth=32):
    """Find visible functions whose code identity is in a verified inventory.

    Lookup uses raw namespaces, not getattr(), descriptors or an import loader.
    Aliases of the same function are deduplicated; distinct closures sharing code
    remain separate private references. Discovery alone grants no metric support.
    """
    if type(module) is not ModuleType or type(codes) is not dict:
        raise DiscoveryError("Expected an ordinary loaded module and code mapping")
    if any(type(limit) is not int or limit <= 0 for limit in (maximum_members, maximum_depth)):
        raise DiscoveryError("Inspection limits must be positive integers")
    if (len(codes) > maximum_members
            or any(type(key) is not str or not key or len(key) > 512 for key in codes)
            or any(type(code) is not CodeType for code in codes.values())):
        raise DiscoveryError("Invalid or oversized code mapping")
    by_code = {id(code): key for key, code in codes.items()}
    found = {}
    for function in namespace_functions(module, maximum_members=maximum_members, maximum_depth=maximum_depth):
        key = by_code.get(id(function.__code__))
        if key is not None:
            found.setdefault(key, []).append(function)
    return {key: tuple(functions) for key, functions in found.items()}


def namespace_functions(module, *, maximum_members=16384, maximum_depth=32):
    """Copy visible ordinary functions without claiming code/artifact ownership.

    Both identity discovery and verified-artifact matching use this bounded raw
    scan. Returning references first completes inspection before a provider starts
    changing registrations; a limit failure cannot publish a partial traversal.
    """
    if type(module) is not ModuleType:
        raise DiscoveryError("Expected an ordinary loaded module")
    if any(type(limit) is not int or limit <= 0 for limit in (maximum_members, maximum_depth)):
        raise DiscoveryError("Inspection limits must be positive integers")
    found, seen_classes, seen_functions = [], set(), set()
    stack = [(ModuleType.__getattribute__(module, "__dict__"), 0)]
    inspected = 0
    try:
        while stack:
            namespace, depth = stack.pop()
            for value in namespace.values():
                inspected += 1
                if inspected > maximum_members:
                    raise DiscoveryError("Namespace member limit")
                value_type = type(value)
                if value_type in (staticmethod, classmethod):
                    value = value.__func__
                    value_type = type(value)
                if value_type is FunctionType:
                    if id(value) not in seen_functions:
                        found.append(value)
                        seen_functions.add(id(value))
                elif issubclass(value_type, type) and id(value) not in seen_classes:
                    if depth >= maximum_depth:
                        raise DiscoveryError("Namespace depth limit")
                    seen_classes.add(id(value))
                    # Use type's own dictionary descriptor, bypassing a custom
                    # metaclass's __getattribute__ or __dict__ property.
                    namespace = type.__dict__["__dict__"].__get__(value)
                    stack.append((namespace, depth + 1))
                # Properties, instances and custom callable/descriptor types
                # are left untouched; their user-defined lookup could run code.
    except RuntimeError as error:
        raise DiscoveryError("Namespace changed during inspection") from error
    return tuple(found)
