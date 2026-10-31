# SPDX-License-Identifier: MPL-2.0
"""Owned policy/validation workloads; business conditions live only in mod source."""

from copy import deepcopy
import json


CASES = {
    "nationality": dict(owners=1, calls=4, rule="nationality"),
    "salary": dict(owners=1, calls=4, rule="salary"),
    "validators": dict(owners=4, calls=8, bindings=8),
    "shared32": dict(owners=32, calls=1),
    "private4": dict(owners=4, calls=4, private=True),
    "context16": dict(owners=1, calls=16, context="nodes"),
    "context32": dict(owners=1, calls=32, context="nodes"),
    "bytes32": dict(owners=1, calls=32, context="bytes"),
    "integer32": dict(owners=1, calls=32, context="integer"),
    "rule32": dict(owners=1, calls=32, payload="nodes"),
    "catalog": dict(owners=1, calls=32, rules=32, options=32, payload="catalog", full_source=True),
    "dense_catalog": dict(owners=1, calls=32, rules=32, options=10, payload="nodes", full_source=True),
    "maximum_calls": dict(owners=32, calls=8, bindings=8),
}

ENTRY = '''import sys,json
class WitWorld:
 def activate(self): pass
 def on_event(self,n,p):
  if n!='benchmark.memory': raise ValueError('unknown benchmark operation')
  state=getattr(sys.modules['runner_guest'],'_resident',None)
  seen=set()
  if state is None: return json.dumps({'resident_bytes':0})
  pending=[{name:getattr(state,name).__dict__ if getattr(state,name) is not None else None
            for name in ('active','staged','action')}]
  total=0
  while pending:
   value=pending.pop()
   kind=type(value)
   frozen=kind.__module__=='_regulations.frozen'
   model=kind.__module__.startswith('_regulations.') and hasattr(value,'__dict__')
   if not frozen and not model and kind not in (dict,list,tuple,set,frozenset,str,int,float,bool,type(None)): continue
   identity=id(value)
   if identity in seen: continue
   seen.add(identity)
   total+=sys.getsizeof(value)
   if frozen:
    pending.append(value.tokens)
   elif model:
    pending.append(vars(value))
   elif kind is dict:
    pending.extend(value.keys())
    pending.extend(value.values())
   elif kind in (list,tuple,set,frozenset):
    pending.extend(value)
  return json.dumps({'resident_bytes':total})
'''

HANDLERS = {
    "nationality": """def validate(c,r):
 if c['proposal']['nationality'] not in r['current']['parameters']['allowed_nationalities']:
  return {'decision':'deny','message':'Nationality not permitted'}
 return {'decision':'allow'}
""",
    "salary": """def validate(c,r):
 if c['proposal']['salary']>r['current']['parameters']['maximum_salary']:
  return {'decision':'deny','message':'Salary exceeds the permitted amount'}
 return {'decision':'allow'}
""",
    "trivial": "def validate(c,r): return {'decision':'allow'}\n",
}


def context():
    return {"schema": 1, "scope": "personnel.contract", "operation": "commit", "initiator": "player",
            "clock": {"season": 2026, "absolute_week": 4},
            "effective": {"season": 2026, "start_week": 4, "end_week": None}, "proposal": {"context_token": True}}


def inputs(name):
    case = CASES[name]
    owners = [f"owned.rules{index:02}" for index in range(case["owners"])]
    parameters = {"stable_token": "policy", "value": 1}
    if case.get("rule") == "nationality":
        parameters["allowed_nationalities"] = ["GB", "IE"]
    if case.get("rule") == "salary":
        parameters["maximum_salary"] = 1000
    if case.get("payload") in ("nodes", "catalog"):
        parameters["values"] = [{} for _ in range(950 if case["payload"] == "nodes" else 270)]
    count = case.get("rules", 1)
    native = {f"native{index:02}": [{"key": f"opt{option:02}", "parameters": deepcopy(parameters)}
                                   for option in range(case.get("options", 1))] for index in range(count)}
    sections = {}
    for owner in owners:
        rules = [owner + ".private"] if case.get("private") else list(native)
        validators = [{"id": f"v{index:02}", "rule": rules[index % len(rules)], "scope": "personnel.contract",
                       "handler": "rules.validate"} for index in range(case.get("bindings", case["calls"]))]
        sections[owner] = {"schema": 1, "validators": validators}
        if case.get("private"):
            sections[owner]["categories"] = [{"key": rules[0], "label": "Owned", "default": "opt00",
                                               "options": [{"id": 1, "key": "opt00", "name": "Owned",
                                                            "parameters": {**parameters, "private_token": owner}}]}]
        else:
            sections[owner]["option_data"] = {rule: {option["key"]: {"private_token": owner} for option in options}
                                               for rule, options in native.items()}
    document = {"categories": [{"key": rule, "options": options} for rule, options in native.items()], "mod_extensions": sections}
    data = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    if case.get("full_source"):
        document["benchmark_padding"] = ""
        size = len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode())
        if size > 1024 * 1024:
            raise ValueError("Large benchmark fixture needs fewer definition entries")
        document["benchmark_padding"] = "x" * (1024 * 1024 - size)
        data = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > 1024 * 1024:
        raise ValueError("Benchmark fixture exceeds regulations.json admission")
    return case, owners, native, sections, data


def files(case, *, owner=None):
    entry = ENTRY
    if owner is not None:
        # Inspect this owner's retained view inside the shared physical runner.
        # The legacy comparison keeps its separate, explicitly selected source.
        entry = entry.replace("state=getattr(sys.modules['runner_guest'],'_resident',None)",
                              "from _loading.runner_bindings import _shared_regulations\n"
                              "  state=_shared_regulations")
    result = (("mod.py", entry), ("rules.py", HANDLERS[case.get("rule", "trivial")]))
    if case.get("full_source"):
        remaining = 1024 * 1024 - sum(len(text.encode()) for _, text in result)
        result += (("padding.py", "#" + "x" * (remaining - 1)),)
    return result


def tables(case, owners, native, sections, revisions):
    expanded, bindings = {}, {}
    for owner in owners:
        expanded[owner], bindings[owner] = [], []
        declared = sections[owner]["validators"]
        for binding in range(case["calls"]):
            rule = declared[binding]["rule"]
            private = case.get("private")
            option = sections[owner]["categories"][0]["options"][0] if private else native[rule][0]
            overlay = {} if private else sections[owner]["option_data"][rule][option["key"]]
            regulation = {"schema": 1, "key": rule, "revision": revisions[owner] + ":1:1", "evaluation_season": 2026,
                          "selected": "current", "current": {"key": option["key"], "effective_season": 2026,
                          "parameters": option["parameters"], "option_data": overlay}, "upcoming": None}
            expanded[owner].append(regulation)
            bindings[owner].append(binding)
    # The caller computes exact node/byte boundary padding using the implementation's
    # own metric helper; this fixture supplies the common unpadded context.
    ctx = context()
    ctx["proposal"].update(nationality="GB", salary=1100)
    return ctx, expanded, bindings
