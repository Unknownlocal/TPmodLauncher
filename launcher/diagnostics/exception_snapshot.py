# SPDX-License-Identifier: MPL-2.0
"""Capture failures from memory without traceback's implicit source-file checks."""

from itertools import islice
import linecache
import traceback


class ExceptionSnapshot:
    """Keep exception messages, relationships and frame locations, never live frames."""

    def __init__(self, error):
        self.root, self.nodes = id(error), {}
        pending = [error]
        while pending:
            value = pending.pop()
            key = id(value)
            if key in self.nodes:
                continue
            kind, stack, tb = type(value), [], value.__traceback__
            while tb is not None:
                code = tb.tb_frame.f_code
                position = next(islice(code.co_positions(), tb.tb_lasti // 2, None), (None,) * 4) if tb.tb_lasti >= 0 else (None,) * 4
                stack.append((code.co_filename, tb.tb_lineno, code.co_name, *position[1:]))
                tb = tb.tb_next
            cause, context = value.__cause__, value.__context__
            children = list(value.exceptions) if isinstance(value, BaseExceptionGroup) else None
            syntax = ({name: getattr(value, name, None) for name in
                       ("filename", "lineno", "end_lineno", "text", "offset", "end_offset", "msg")}
                      if isinstance(value, SyntaxError) else None)
            self.nodes[key] = dict(name=kind.__qualname__, module=kind.__module__, message=str(value),
                                   stack=stack, notes=list(getattr(value, "__notes__", ())), syntax=syntax,
                                   cause=id(cause) if cause is not None else None,
                                   context=id(context) if context is not None else None,
                                   suppress=value.__suppress_context__,
                                   children=[id(child) for child in children] if children is not None else None)
            pending.extend(item for item in (cause, context) if item is not None)
            pending.extend(children or ())

    def format(self):
        """Resolve source lines and use standard traceback presentation on the writer."""
        rendered = {}
        for key, node in self.nodes.items():
            # Harmless stand-ins avoid invoking a game's exception constructors,
            # property readers or retained instances on this background thread.
            base = SyntaxError if node["syntax"] is not None else Exception
            kind = type(node["name"], (base,), {"__module__": node["module"]})
            value = base(node["message"])
            for name, field in (node["syntax"] or {}).items():
                setattr(value, name, field)
            value.__notes__ = node["notes"]
            summary = traceback.TracebackException(kind, value, None, lookup_lines=False,
                                                   max_group_width=float("inf"), max_group_depth=float("inf"))
            summary.stack = traceback.StackSummary.from_list([
                traceback.FrameSummary(filename, lineno, name, lookup_line=False,
                                       end_lineno=end, colno=column, end_colno=end_column)
                for filename, lineno, name, end, column, end_column in node["stack"]])
            for filename, *_ in node["stack"]:
                linecache.checkcache(filename)
            summary.__suppress_context__ = node["suppress"]
            rendered[key] = summary
        # Follow each causal edge once to avoid cyclic exception chains. Group
        # members may refer to the same failure more than once and remain visible.
        seen, pending = {self.root}, [self.root]
        while pending:
            key = pending.pop()
            node, summary = self.nodes[key], rendered[key]
            for field in ("cause", "context"):
                child = node[field]
                linked = child is not None and child not in seen
                setattr(summary, "__" + field + "__", rendered[child] if linked else None)
                if linked:
                    seen.add(child)
                    pending.append(child)
            children = node["children"]
            summary.exceptions = [rendered[child] for child in children] if children is not None else None
            for child in children or ():
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
        return rendered[self.root].format()
