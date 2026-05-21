"""optimize.py - source-to-source optimizer for bundled bot submissions.

Operates on the bundled submission.py produced by bundle.py. Two passes:

  1. Inlining. Functions whose body is a single `return EXPR` (or a linear
     "assignments + final return" body, with optional early-return guards
     turned into ternaries) get expanded at call sites. Cross-module inlining
     is allowed when the body uses only safe names (parameters, math.*,
     builtins, and module-level literal constants).

  2. Unrolling. `for VAR in range(...)` and `for VAR in <literal-seq>` loops
     whose bounds resolve to concrete integers (via module-level int/float
     constants) are expanded. Both full unrolling (default) and partial
     unrolling via `# @unroll N` are supported. `# @nounroll` skips a loop.
     `# @noinline` skips a function definition.

Both passes are run to fixpoint so newly-inlined bodies can themselves trigger
further inlining or unrolling.

Usage:
    python optimize.py basic4/submission.py
    python optimize.py basic4/submission.py -o basic4/submission_opt.py
    python optimize.py basic4/submission.py --max-inline-body 16 --max-unroll 16
    python optimize.py basic4/submission.py --verbose
"""

import argparse
import ast
import copy
import os
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# Safe-name registry: names that resolve identically in every module so a
# body referencing them is safe to splice into a foreign module.
# ---------------------------------------------------------------------------

_PY_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "frozenset", "hasattr", "hash", "id", "int", "isinstance",
    "iter", "len", "list", "map", "max", "min", "next", "object",
    "print", "range", "repr", "reversed", "round", "set", "slice",
    "sorted", "str", "sum", "tuple", "type", "zip", "True", "False", "None",
    "Ellipsis",
}

# math.* attributes used by these bots; pure functions, identical across modules
_MATH_SAFE = {
    "pi", "e", "tau", "inf", "nan",
    "sqrt", "hypot", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "log", "log2", "log10", "exp", "pow", "ceil", "floor", "trunc",
    "fabs", "copysign", "isfinite", "isinf", "isnan", "isclose",
    "degrees", "radians",
}


# ---------------------------------------------------------------------------
# Bundle handling
# ---------------------------------------------------------------------------

_MAIN_MARKER = "# === main.py ==="


def is_bundle(src: str) -> bool:
    return src.lstrip().startswith("# Auto-generated bundle")


def split_bundle(src: str) -> tuple[str, str]:
    """Split header (with _BUNDLED_MODULES + exec loop) from the main.py tail."""
    idx = src.find(_MAIN_MARKER)
    if idx < 0:
        raise SystemExit(f"Bundle missing '{_MAIN_MARKER}' separator")
    return src[:idx], src[idx + len(_MAIN_MARKER):]


def extract_bundled_modules(header_src: str) -> tuple[ast.Module, dict[str, str], ast.Assign]:
    """
    Parse the header and return:
      - the full header AST (mutated later)
      - {bundled_module_name: raw_source_string}
      - the AST node for `_BUNDLED_MODULES = {...}` (so we can rewrite values)
    """
    tree = ast.parse(header_src)
    assign_node = None
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "_BUNDLED_MODULES"
            and isinstance(stmt.value, ast.Dict)
        ):
            assign_node = stmt
            break
    if assign_node is None:
        raise SystemExit("Bundle header missing _BUNDLED_MODULES dict assignment")

    modules: dict[str, str] = {}
    for k, v in zip(assign_node.value.keys, assign_node.value.values):
        if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
            raise SystemExit("Non-string key in _BUNDLED_MODULES")
        if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
            raise SystemExit("Non-string value in _BUNDLED_MODULES")
        modules[k.value] = v.value
    return tree, modules, assign_node


def reassemble_bundle(header_tree: ast.Module, new_modules: dict[str, str],
                       assign_node: ast.Assign, main_src: str) -> str:
    """Patch the dict values to the optimized sources, then emit."""
    for k, _v in zip(assign_node.value.keys, assign_node.value.values):
        name = k.value  # type: ignore[attr-defined]
        # Find this entry's slot in the original dict ordering.
    new_keys = list(assign_node.value.keys)
    new_values: list[ast.expr] = []
    for k in new_keys:
        name = k.value  # type: ignore[attr-defined]
        new_values.append(ast.Constant(value=new_modules[name]))
    assign_node.value.values = new_values

    header_out = ast.unparse(header_tree)
    # Emit the dict's string values in their original raw-triple-quoted form
    # for readability. ast.unparse escapes newlines as \n, which is correct but
    # makes the file unreadable; the regex below re-wraps each value back into
    # r''' ... '''. Falls back to the unparse'd form if ''' appears in source.
    header_out = _prettify_bundle_dict(header_out, new_modules)
    return header_out.rstrip() + "\n\n" + _MAIN_MARKER + "\n" + main_src.lstrip("\n")


def _prettify_bundle_dict(header_src: str, modules: dict[str, str]) -> str:
    """Rewrite each `'name': '<escaped source>'` into `'name': r'''<raw>''',`."""
    out = header_src
    for name, src in modules.items():
        if "'''" in src:
            continue  # leave as escaped string literal
        # Match: 'name': '...escaped...'   OR  "name": '...'   OR  'name': "..."
        pat = re.compile(
            r"(['\"]" + re.escape(name) + r"['\"]\s*:\s*)"
            r"('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")",
            re.DOTALL,
        )
        m = pat.search(out)
        if not m:
            continue
        replacement = f"{m.group(1)}r'''{src}'''"
        out = out[:m.start()] + replacement + out[m.end():]
    return out


# ---------------------------------------------------------------------------
# Module analysis
# ---------------------------------------------------------------------------

def find_constants(tree: ast.Module) -> dict[str, int | float | str | bool | None]:
    """Module-level `NAME = <literal>` assignments. Only int/float/str/bool/None."""
    consts: dict[str, object] = {}
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, (int, float, str, bool, type(None)))
        ):
            # Skip dunder/conventional-private if you want, but include all here.
            consts[stmt.targets[0].id] = stmt.value.value
    return consts


def find_imported_names(tree: ast.Module) -> dict[str, str]:
    """
    Map `name as used in this module` -> `bundled-module name` it came from.

    Only handles `from <module> import X, Y` where <module> is one of our
    bundled module names. Aliases (`as Z`) map Z to the source module.
    """
    out: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            for alias in stmt.names:
                local = alias.asname or alias.name
                out[local] = stmt.module
    return out


# ---------------------------------------------------------------------------
# Inlining
# ---------------------------------------------------------------------------

# Names allowed in an inlinable function body if it's to be cross-module-safe.
def _safe_attr(node: ast.Attribute) -> bool:
    # Allow math.X where X is in _MATH_SAFE.
    if isinstance(node.value, ast.Name) and node.value.id == "math" and node.attr in _MATH_SAFE:
        return True
    return False


class _NameUsageScanner(ast.NodeVisitor):
    """Collect free names referenced in a function body."""

    def __init__(self, params: set[str]):
        self.params = params
        self.free_names: set[str] = set()
        self.attr_accesses: list[ast.Attribute] = []
        self.calls: list[ast.Call] = []
        self.has_yield = False
        self.has_await = False
        self.has_try = False
        self.has_nested_def = False
        self.has_star_args_use = False
        self.assigned_locals: set[str] = set()

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            self.assigned_locals.add(node.id)
        else:
            if node.id not in self.params and node.id not in self.assigned_locals:
                self.free_names.add(node.id)

    def visit_Attribute(self, node):
        self.attr_accesses.append(node)
        self.generic_visit(node)

    def visit_Call(self, node):
        self.calls.append(node)
        self.generic_visit(node)

    def visit_Yield(self, node):
        self.has_yield = True

    def visit_YieldFrom(self, node):
        self.has_yield = True

    def visit_Await(self, node):
        self.has_await = True

    def visit_Try(self, node):
        self.has_try = True

    def visit_FunctionDef(self, node):
        self.has_nested_def = True

    def visit_AsyncFunctionDef(self, node):
        self.has_nested_def = True

    def visit_ClassDef(self, node):
        self.has_nested_def = True


class InlinableSpec:
    """A function deemed inlinable, plus its normalized form."""

    __slots__ = ("name", "module", "params", "defaults", "body_assigns",
                 "return_expr", "free_names", "cross_module_safe", "is_trivial")

    def __init__(self, name, module, params, defaults, body_assigns,
                 return_expr, free_names, cross_module_safe, is_trivial):
        self.name = name
        self.module = module
        self.params: list[str] = params
        self.defaults: dict[str, ast.expr] = defaults
        self.body_assigns: list[ast.Assign] = body_assigns
        self.return_expr: ast.expr = return_expr
        self.free_names: set[str] = free_names
        self.cross_module_safe: bool = cross_module_safe
        self.is_trivial: bool = is_trivial  # True iff no body assigns


def _normalize_linear_body(fn: ast.FunctionDef) -> tuple[list[ast.Assign], ast.expr] | None:
    """
    Convert the body into (list_of_simple_assignments, final_return_expr).

    Accepts two shapes:
      1. Trailing-return-only:   stmts ending in `return EXPR`, all prior stmts
         are simple Name = expr assignments (no augmented, no tuple targets).
      2. Early-return-guard:     `if cond: return X` followed by linear body
         ending in `return Y`. Fold to: (assigns, X if cond else Y).
    """
    body = list(fn.body)
    # Strip a leading docstring.
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    if not body:
        return None

    # Collect leading `if cond: return X` guards.
    guards: list[tuple[ast.expr, ast.expr]] = []
    while body and isinstance(body[0], ast.If) and not body[0].orelse:
        ifnode = body[0]
        if (len(ifnode.body) == 1
                and isinstance(ifnode.body[0], ast.Return)
                and ifnode.body[0].value is not None):
            guards.append((ifnode.test, ifnode.body[0].value))
            body = body[1:]
        else:
            break

    if not body:
        return None
    final = body[-1]
    if not (isinstance(final, ast.Return) and final.value is not None):
        return None
    middle = body[:-1]
    assigns: list[ast.Assign] = []
    for s in middle:
        if (isinstance(s, ast.Assign)
                and len(s.targets) == 1
                and isinstance(s.targets[0], ast.Name)):
            assigns.append(s)
        else:
            return None

    final_expr = final.value
    # Build ternary chain: guard1_x if guard1_cond else (guard2_x if ... else final)
    for cond, x in reversed(guards):
        final_expr = ast.IfExp(test=cond, body=x, orelse=final_expr)
    return assigns, final_expr


def _is_noinline_marked(fn: ast.FunctionDef) -> bool:
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Name) and dec.id in ("noinline", "nooptimize"):
            return True
    return False


def _has_noinline_comment(src_lines: list[str], fn: ast.FunctionDef) -> bool:
    # Comment on the line immediately above `def`. ast.unparse drops comments
    # and shifts line numbers, so be tolerant of out-of-range indices.
    line = fn.lineno - 2  # zero-indexed line above
    if line >= len(src_lines):
        return False
    while line >= 0 and not src_lines[line].strip():
        line -= 1
    if line < 0:
        return False
    stripped = src_lines[line].strip()
    return stripped.startswith("# @noinline") or stripped.startswith("# @nooptimize")


def _params_of(fn: ast.FunctionDef) -> tuple[list[str], dict[str, ast.expr]] | None:
    args = fn.args
    if args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs:
        return None
    params = [a.arg for a in args.args]
    defaults_list = args.defaults
    defaults: dict[str, ast.expr] = {}
    n_defaults = len(defaults_list)
    for name, dflt in zip(params[len(params) - n_defaults:], defaults_list):
        defaults[name] = dflt
    return params, defaults


def find_inlinable(
    module_name: str,
    tree: ast.Module,
    src: str,
    max_body: int,
) -> dict[str, InlinableSpec]:
    """Scan top-level defs and return a name->spec map of inlinable functions."""
    src_lines = src.splitlines()
    out: dict[str, InlinableSpec] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        if stmt.decorator_list and not all(
            isinstance(d, ast.Name) and d.id in ("inline",) for d in stmt.decorator_list
        ):
            continue
        if _is_noinline_marked(stmt) or _has_noinline_comment(src_lines, stmt):
            continue
        params_info = _params_of(stmt)
        if params_info is None:
            continue
        params, defaults = params_info
        normalized = _normalize_linear_body(stmt)
        if normalized is None:
            continue
        body_assigns, return_expr = normalized
        if len(body_assigns) > max_body:
            continue

        # Scan free-name usage and forbid bad constructs.
        scanner = _NameUsageScanner(set(params))
        for a in body_assigns:
            scanner.visit(a)
        scanner.visit(return_expr)
        if scanner.has_yield or scanner.has_await or scanner.has_try or scanner.has_nested_def:
            continue
        # Recursion guard.
        if stmt.name in scanner.free_names:
            continue
        out[stmt.name] = InlinableSpec(
            name=stmt.name, module=module_name, params=params, defaults=defaults,
            body_assigns=body_assigns, return_expr=return_expr,
            free_names=scanner.free_names, cross_module_safe=True,  # filled below
            is_trivial=(len(body_assigns) == 0),
        )
    return out


def _classify_safety(
    spec: InlinableSpec,
    home_consts: dict[str, object],
    all_bundled_module_names: set[str],
) -> None:
    """Set spec.cross_module_safe based on whether free names are universally safe.

    Mutates the spec in place. A name is universally safe if it's a builtin,
    a math.X reference (checked separately via attribute scan), or a module-
    level literal in the home module. References to non-literal home-module
    names (functions, mutable state) mean only same-module inlining is OK.
    """
    safe = True
    for name in spec.free_names:
        if name in _PY_BUILTINS:
            continue
        if name == "math":
            continue
        if name in home_consts and isinstance(home_consts[name], (int, float, str, bool, type(None))):
            continue
        # Anything else (e.g., another helper function, module state) - not portable.
        safe = False
        break
    spec.cross_module_safe = safe


# ---------------------- Inlining transform ---------------------------------

class _ParamSubstituter(ast.NodeTransformer):
    """Replace parameter Names in a copied function body with provided exprs."""

    def __init__(self, mapping: dict[str, ast.expr], local_rename: dict[str, str]):
        self.mapping = mapping
        self.local_rename = local_rename

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in self.mapping:
            return copy.deepcopy(self.mapping[node.id])
        if node.id in self.local_rename:
            return ast.copy_location(
                ast.Name(id=self.local_rename[node.id], ctx=node.ctx), node)
        return node


def _is_simple_arg(node: ast.expr) -> bool:
    """Safe to substitute multiple times without re-evaluating side effects."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Attribute):
        return _is_simple_arg(node.value)
    if isinstance(node, ast.Subscript):
        return _is_simple_arg(node.value) and _is_simple_arg_slice(node.slice)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_simple_arg(node.operand)
    return False


def _is_simple_arg_slice(node: ast.expr) -> bool:
    if isinstance(node, ast.Slice):
        return all(p is None or _is_simple_arg(p) for p in (node.lower, node.upper, node.step))
    return _is_simple_arg(node)


def _name_appears_count(body: list[ast.Assign], expr: ast.expr, name: str) -> int:
    """Count load-context uses of `name` across the body and return expression."""
    count = 0

    class V(ast.NodeVisitor):
        def visit_Name(self, n):
            nonlocal count
            if n.id == name and isinstance(n.ctx, ast.Load):
                count += 1

    v = V()
    for a in body:
        v.visit(a.value)
    v.visit(expr)
    return count


class Inliner(ast.NodeTransformer):
    """Inline calls to known-safe functions, expanding to body+return.

    Same-module calls can always inline (if the spec is inlinable). Cross-module
    inlining only fires when spec.cross_module_safe is True AND the destination
    module has the imports needed (we check by looking at imported_names).
    """

    def __init__(
        self,
        specs: dict[tuple[str, str], InlinableSpec],
        home_module: str,
        imported_names: dict[str, str],
        consts: dict[str, object],
        verbose: bool,
    ):
        # specs: keyed by (module, func_name).
        self.specs = specs
        self.home = home_module
        self.imports = imported_names
        self.consts = consts
        self.verbose = verbose
        self.fresh_counter = 0
        self.inline_count = 0
        # Statements to inject before the current statement.
        self._prelude_stack: list[list[ast.stmt]] = []

    def _fresh(self, base: str) -> str:
        self.fresh_counter += 1
        return f"_il_{base}_{self.fresh_counter}"

    def _lookup_call_target(self, call: ast.Call) -> InlinableSpec | None:
        func = call.func
        if isinstance(func, ast.Name):
            home_key = (self.home, func.id)
            if home_key in self.specs:
                return self.specs[home_key]
            src_mod = self.imports.get(func.id)
            if src_mod is not None:
                ext_key = (src_mod, func.id)
                spec = self.specs.get(ext_key)
                if spec is not None and spec.cross_module_safe:
                    return spec
        return None

    def _bind_args(self, spec: InlinableSpec, call: ast.Call,
                    prelude: list[ast.stmt]) -> dict[str, ast.expr] | None:
        """
        Build the param->expression substitution map. If an argument is unsafe
        to substitute multiple times AND the parameter appears more than once
        in the body, hoist it to a fresh local in `prelude`.
        """
        if call.keywords and any(kw.arg is None for kw in call.keywords):
            return None  # **kwargs at call site - skip
        positional = call.args
        if len(positional) > len(spec.params):
            return None  # extra positionals - bail

        provided: dict[str, ast.expr] = {}
        for p, arg in zip(spec.params, positional):
            provided[p] = arg
        for kw in call.keywords:
            if kw.arg in provided:
                return None  # duplicate
            if kw.arg not in spec.params:
                return None  # unknown keyword - bail
            provided[kw.arg] = kw.value

        # Fill defaults.
        for p in spec.params:
            if p not in provided:
                dflt = spec.defaults.get(p)
                if dflt is None:
                    return None
                provided[p] = dflt

        mapping: dict[str, ast.expr] = {}
        for p, expr in provided.items():
            if _is_simple_arg(expr):
                mapping[p] = expr
                continue
            uses = _name_appears_count(spec.body_assigns, spec.return_expr, p)
            if uses <= 1:
                mapping[p] = expr
                continue
            tmp = self._fresh(p)
            prelude.append(ast.Assign(
                targets=[ast.Name(id=tmp, ctx=ast.Store())],
                value=expr,
            ))
            mapping[p] = ast.Name(id=tmp, ctx=ast.Load())
        return mapping

    def _inline(self, call: ast.Call) -> ast.expr | None:
        spec = self._lookup_call_target(call)
        if spec is None:
            return None
        prelude = self._prelude_stack[-1] if self._prelude_stack else None
        if prelude is None and spec.body_assigns:
            return None  # not at a statement context - can't hoist body assigns
        local_prelude: list[ast.stmt] = []
        mapping = self._bind_args(spec, call, local_prelude)
        if mapping is None:
            return None

        # Rename body-local names (assigned vars) to avoid collision in caller scope.
        local_rename: dict[str, str] = {}
        for a in spec.body_assigns:
            tgt = a.targets[0]
            if isinstance(tgt, ast.Name):
                local_rename[tgt.id] = self._fresh(tgt.id)

        substituter = _ParamSubstituter(mapping, local_rename)
        new_assigns = []
        for a in spec.body_assigns:
            new_a = copy.deepcopy(a)
            new_a = substituter.visit(new_a)
            ast.fix_missing_locations(new_a)
            new_assigns.append(new_a)
        new_return = substituter.visit(copy.deepcopy(spec.return_expr))
        ast.fix_missing_locations(new_return)

        if prelude is not None:
            prelude.extend(local_prelude)
            prelude.extend(new_assigns)
        elif local_prelude or new_assigns:
            # We were unable to hoist - bail. (Should be unreachable given earlier check.)
            return None
        self.inline_count += 1
        return new_return

    # ---- Statement-level dispatch (gives us a prelude slot) -----------------

    def _visit_stmt_list(self, stmts: list[ast.stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for s in stmts:
            prelude: list[ast.stmt] = []
            self._prelude_stack.append(prelude)
            try:
                new_s = self.visit(s)
            finally:
                self._prelude_stack.pop()
            if prelude:
                out.extend(prelude)
            if isinstance(new_s, list):
                out.extend(new_s)
            elif new_s is not None:
                out.append(new_s)
        return out

    def visit_Module(self, node):
        node.body = self._visit_stmt_list(node.body)
        return node

    def visit_FunctionDef(self, node):
        node.body = self._visit_stmt_list(node.body)
        node.args = self.generic_visit(node.args) if False else node.args  # leave args alone
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node):
        node.test = self.visit(node.test)
        node.body = self._visit_stmt_list(node.body)
        node.orelse = self._visit_stmt_list(node.orelse)
        return node

    def visit_For(self, node):
        node.iter = self.visit(node.iter)
        node.body = self._visit_stmt_list(node.body)
        node.orelse = self._visit_stmt_list(node.orelse)
        return node

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        node.test = self.visit(node.test)
        node.body = self._visit_stmt_list(node.body)
        node.orelse = self._visit_stmt_list(node.orelse)
        return node

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
        node.body = self._visit_stmt_list(node.body)
        return node

    visit_AsyncWith = visit_With

    def visit_Try(self, node):
        node.body = self._visit_stmt_list(node.body)
        for h in node.handlers:
            h.body = self._visit_stmt_list(h.body)
        node.orelse = self._visit_stmt_list(node.orelse)
        node.finalbody = self._visit_stmt_list(node.finalbody)
        return node

    # ---- Expression: try to inline if it's a Call ---------------------------

    def visit_Call(self, node):
        self.generic_visit(node)
        replacement = self._inline(node)
        if replacement is None:
            return node
        return replacement


# ---------------------------------------------------------------------------
# Unrolling
# ---------------------------------------------------------------------------

def _evaluate_const_expr(node: ast.expr, consts: dict[str, object]) -> object | None:
    """Try to fold an expression to a Python literal using `consts`. Returns None on failure."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _evaluate_const_expr(node.operand, consts)
        if v is None:
            return None
        if isinstance(node.op, ast.USub):
            return -v  # type: ignore[operator]
        if isinstance(node.op, ast.UAdd):
            return +v  # type: ignore[operator]
        return None
    if isinstance(node, ast.BinOp):
        l = _evaluate_const_expr(node.left, consts)
        r = _evaluate_const_expr(node.right, consts)
        if l is None or r is None:
            return None
        try:
            if isinstance(node.op, ast.Add): return l + r  # type: ignore[operator]
            if isinstance(node.op, ast.Sub): return l - r  # type: ignore[operator]
            if isinstance(node.op, ast.Mult): return l * r  # type: ignore[operator]
            if isinstance(node.op, ast.FloorDiv): return l // r  # type: ignore[operator]
            if isinstance(node.op, ast.Mod): return l % r  # type: ignore[operator]
            if isinstance(node.op, ast.Pow): return l ** r  # type: ignore[operator]
        except Exception:
            return None
    return None


def _try_eval_range(call: ast.Call, consts: dict[str, object]) -> list[int] | None:
    if not (isinstance(call.func, ast.Name) and call.func.id == "range"):
        return None
    args = call.args
    if not (1 <= len(args) <= 3):
        return None
    vals: list[int] = []
    for a in args:
        v = _evaluate_const_expr(a, consts)
        if not isinstance(v, int) or isinstance(v, bool):
            return None
        vals.append(v)
    try:
        return list(range(*vals))
    except Exception:
        return None


def _try_eval_literal_seq(node: ast.expr, consts: dict[str, object]) -> list[ast.expr] | None:
    """Return per-element AST exprs if `node` is a literal list/tuple of safe exprs."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    items = []
    for el in node.elts:
        # Either a literal, or a safe expression (no side effects).
        if isinstance(el, ast.Constant):
            items.append(el)
        elif _is_simple_arg(el):
            items.append(el)
        elif _evaluate_const_expr(el, consts) is not None:
            v = _evaluate_const_expr(el, consts)
            items.append(ast.Constant(value=v))
        else:
            return None
    return items


def _unroll_directive(src_lines: list[str], for_node: ast.For) -> tuple[bool, int | None]:
    """Return (skip_unroll, partial_factor) based on the comment above this for."""
    line = for_node.lineno - 2
    if line >= len(src_lines):
        return False, None
    while line >= 0 and not src_lines[line].strip():
        line -= 1
    if line < 0:
        return False, None
    stripped = src_lines[line].strip()
    if stripped.startswith("# @nounroll") or stripped.startswith("# @nooptimize"):
        return True, None
    m = re.match(r"#\s*@unroll\s+(\d+)\b", stripped)
    if m:
        return False, int(m.group(1))
    return False, None


def _body_has_unwind(stmts: list[ast.stmt]) -> bool:
    """break/continue/return inside the body breaks unrolling. Nested loops are fine."""
    for stmt in stmts:
        for sub in ast.walk(stmt):
            if isinstance(sub, (ast.Break, ast.Continue, ast.Return)):
                # Allow if inside a nested for/while/comprehension.
                # ast.walk doesn't tell us; do a parent-tracking pass.
                return _unwind_at_top_level(stmts)
    return False


def _unwind_at_top_level(stmts: list[ast.stmt]) -> bool:
    """True iff any break/continue/return targets the for-loop being unrolled."""
    class V(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0
            self.found = False

        def visit_For(self, n):
            self.depth += 1
            self.generic_visit(n)
            self.depth -= 1

        visit_AsyncFor = visit_For
        visit_While = visit_For

        def visit_FunctionDef(self, n):
            self.depth += 1
            self.generic_visit(n)
            self.depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef

        def visit_Break(self, n):
            if self.depth == 0:
                self.found = True

        def visit_Continue(self, n):
            if self.depth == 0:
                self.found = True

        def visit_Return(self, n):
            # Returns leave the loop entirely - safe to unroll past, the first
            # iteration's return wins. But unrolling more than once after a
            # return is dead code; bail.
            self.found = True

    v = V()
    for s in stmts:
        v.visit(s)
    return v.found


class Unroller(ast.NodeTransformer):
    def __init__(self, consts: dict[str, object], src: str, max_unroll: int, verbose: bool):
        self.consts = consts
        self.src_lines = src.splitlines()
        self.max_unroll = max_unroll
        self.verbose = verbose
        self.unroll_count = 0
        self.fresh = 0

    def _fresh_name(self, base: str) -> str:
        self.fresh += 1
        return f"_ur_{base}_{self.fresh}"

    def _build_unroll(self, target: ast.Name, values: list[ast.expr],
                       body: list[ast.stmt]) -> list[ast.stmt]:
        """Emit body N times, each with target=value prepended."""
        out: list[ast.stmt] = []
        for v in values:
            iter_assign = ast.Assign(
                targets=[ast.Name(id=target.id, ctx=ast.Store())],
                value=copy.deepcopy(v),
            )
            ast.fix_missing_locations(iter_assign)
            out.append(iter_assign)
            for s in body:
                out.append(copy.deepcopy(s))
        return out

    def visit_For(self, node: ast.For):
        # Recurse first - nested loops may unroll independently.
        node.body = [self.visit(s) for s in node.body]
        node.orelse = [self.visit(s) for s in node.orelse]

        if not isinstance(node.target, ast.Name):
            return node
        if node.orelse:
            return node  # for/else semantics don't survive unrolling
        skip, partial = _unroll_directive(self.src_lines, node)
        if skip:
            return node
        if _unwind_at_top_level(node.body):
            return node

        # Try range(...) first, then literal seq.
        values_raw: list[ast.expr] | None = None
        if isinstance(node.iter, ast.Call):
            r = _try_eval_range(node.iter, self.consts)
            if r is not None:
                values_raw = [ast.Constant(value=v) for v in r]
        if values_raw is None:
            values_raw = _try_eval_literal_seq(node.iter, self.consts)
        if values_raw is None:
            return node

        n = len(values_raw)
        limit = partial or self.max_unroll
        if n > limit:
            # Partial unroll: keep the for-loop but unroll N copies per iteration.
            if partial is None:
                return node
            return self._partial_unroll(node, partial)

        unrolled = self._build_unroll(node.target, values_raw, node.body)
        self.unroll_count += 1
        return unrolled

    def _partial_unroll(self, node: ast.For, factor: int) -> ast.For:
        """Replace body with `factor` copies, advancing iter_var manually.

        This is a textbook unroll-by-K transform; we don't try to compute
        leftover handling, so the caller must supply a range whose length is
        a multiple of K, OR be OK with the partial unroll being applied to a
        chunked iteration of the original sequence.

        For now, only handle range(N) where N % factor == 0; otherwise return
        unchanged.
        """
        if not (isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "range"):
            return node
        r = _try_eval_range(node.iter, self.consts)
        if r is None or len(r) % factor != 0:
            return node
        # Build: for _idx in range(0, N, factor): body[i=base+0]; body[i=base+1]; ...
        target = node.target
        chunk_idx = self._fresh_name("base")
        new_target = ast.Name(id=chunk_idx, ctx=ast.Store())
        # Stride args.
        start, stop, step = r[0], r[-1] + 1, factor  # iterate base 0..N step factor
        new_iter = ast.Call(
            func=ast.Name(id="range", ctx=ast.Load()),
            args=[ast.Constant(value=start), ast.Constant(value=stop), ast.Constant(value=step)],
            keywords=[],
        )
        new_body: list[ast.stmt] = []
        for k in range(factor):
            iter_assign = ast.Assign(
                targets=[ast.Name(id=target.id, ctx=ast.Store())],
                value=ast.BinOp(
                    left=ast.Name(id=chunk_idx, ctx=ast.Load()),
                    op=ast.Add(),
                    right=ast.Constant(value=k),
                ),
            )
            new_body.append(iter_assign)
            for s in node.body:
                new_body.append(copy.deepcopy(s))
        new_node = ast.For(
            target=new_target,
            iter=new_iter,
            body=new_body,
            orelse=[],
        )
        ast.copy_location(new_node, node)
        ast.fix_missing_locations(new_node)
        self.unroll_count += 1
        return new_node


# ---------------------------------------------------------------------------
# Dead-def removal
# ---------------------------------------------------------------------------

def _name_uses(tree: ast.Module, name: str) -> int:
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == name:
            count += 1
        elif (isinstance(node, ast.Attribute) and node.attr == name
              and isinstance(node.ctx, ast.Load)):
            count += 1
    return count


def remove_unused_defs(tree: ast.Module, inlined: set[str]) -> int:
    """Drop top-level defs that we successfully inlined everywhere, if unused."""
    new_body: list[ast.stmt] = []
    removed = 0
    for stmt in tree.body:
        if (isinstance(stmt, ast.FunctionDef) and stmt.name in inlined):
            uses = _name_uses(tree, stmt.name)
            # The def itself produces 0 Load-context references (it's a Store).
            # Cross-module: we can't see other modules from here; leave def in place
            # if there are imports of this name we can detect from same-bundle scan.
            if uses == 0:
                removed += 1
                continue
        new_body.append(stmt)
    tree.body = new_body
    return removed


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class _Stats:
    def __init__(self):
        self.inlined = 0
        self.unrolled = 0
        self.defs_removed = 0
        self.passes = 0

    def __str__(self):
        return (f"passes={self.passes} inlined_calls={self.inlined} "
                f"unrolled_loops={self.unrolled} defs_removed={self.defs_removed}")


def optimize_source(src: str, *, max_inline_body: int, max_unroll: int,
                    verbose: bool) -> tuple[str, _Stats]:
    stats = _Stats()
    if is_bundle(src):
        header_src, main_src = split_bundle(src)
        header_tree, modules, assign_node = extract_bundled_modules(header_src)
        # Re-parse modules into ast trees we'll mutate, keyed by name.
        module_trees: dict[str, ast.Module] = {n: ast.parse(s) for n, s in modules.items()}
        module_srcs: dict[str, str] = dict(modules)
        main_tree = ast.parse(main_src)
        main_src_text = main_src
        bundle_module_names = set(modules.keys())

        # Run inline + unroll to fixpoint.
        for it in range(8):
            stats.passes += 1
            changes_this_pass = 0

            # Rebuild spec registry each pass: bodies may have been simplified.
            all_specs: dict[tuple[str, str], InlinableSpec] = {}
            module_consts: dict[str, dict[str, object]] = {}
            for mod_name, tree in module_trees.items():
                consts = find_constants(tree)
                module_consts[mod_name] = consts
                specs = find_inlinable(mod_name, tree, ast.unparse(tree), max_inline_body)
                for sname, sp in specs.items():
                    _classify_safety(sp, consts, bundle_module_names)
                    all_specs[(mod_name, sname)] = sp
            # main module: synthesize entry under name "__main__"
            main_consts = find_constants(main_tree)
            main_specs = find_inlinable("__main__", main_tree, main_src_text, max_inline_body)
            for sname, sp in main_specs.items():
                _classify_safety(sp, main_consts, bundle_module_names)
                all_specs[("__main__", sname)] = sp
            module_consts["__main__"] = main_consts

            # Inline + unroll each module.
            for mod_name, tree in list(module_trees.items()):
                imports = find_imported_names(tree)
                inliner = Inliner(all_specs, mod_name, imports, module_consts[mod_name], verbose)
                tree = inliner.visit(tree)
                ast.fix_missing_locations(tree)
                stats.inlined += inliner.inline_count
                changes_this_pass += inliner.inline_count

                unroller = Unroller(module_consts[mod_name], ast.unparse(tree), max_unroll, verbose)
                tree = unroller.visit(tree)
                ast.fix_missing_locations(tree)
                stats.unrolled += unroller.unroll_count
                changes_this_pass += unroller.unroll_count

                module_trees[mod_name] = tree
                module_srcs[mod_name] = ast.unparse(tree)

            # main module
            imports = find_imported_names(main_tree)
            inliner = Inliner(all_specs, "__main__", imports, main_consts, verbose)
            main_tree = inliner.visit(main_tree)
            ast.fix_missing_locations(main_tree)
            stats.inlined += inliner.inline_count
            changes_this_pass += inliner.inline_count

            unroller = Unroller(main_consts, ast.unparse(main_tree), max_unroll, verbose)
            main_tree = unroller.visit(main_tree)
            ast.fix_missing_locations(main_tree)
            stats.unrolled += unroller.unroll_count
            changes_this_pass += unroller.unroll_count
            main_src_text = ast.unparse(main_tree)

            if verbose:
                print(f"  pass {it+1}: inlined={inliner.inline_count} "
                      f"unrolled={unroller.unroll_count} (cumulative {stats})",
                      file=sys.stderr)
            if changes_this_pass == 0:
                break

        # Dead-def pass: remove top-level defs whose name is no longer referenced
        # in their home module. Cross-module imports keep the def alive.
        all_module_srcs_concat = "\n".join(module_srcs.values()) + "\n" + main_src_text
        for mod_name, tree in module_trees.items():
            removed_here: list[str] = []
            new_body: list[ast.stmt] = []
            for stmt in tree.body:
                if isinstance(stmt, ast.FunctionDef):
                    # Local references (Load context in this module).
                    locals_uses = sum(
                        1 for node in ast.walk(tree)
                        if isinstance(node, ast.Name)
                        and isinstance(node.ctx, ast.Load)
                        and node.id == stmt.name
                    )
                    # Any other module imports this name?
                    external = re.search(
                        rf"import\b[^#\n]*\b{re.escape(stmt.name)}\b",
                        all_module_srcs_concat,
                    )
                    if locals_uses == 0 and not external:
                        removed_here.append(stmt.name)
                        stats.defs_removed += 1
                        continue
                new_body.append(stmt)
            tree.body = new_body
            module_srcs[mod_name] = ast.unparse(tree)

        # Rebuild bundle.
        return reassemble_bundle(header_tree, module_srcs, assign_node, main_src_text), stats

    # Non-bundle path.
    tree = ast.parse(src)
    consts = find_constants(tree)
    specs = find_inlinable("__main__", tree, src, max_inline_body)
    for sp in specs.values():
        _classify_safety(sp, consts, set())
    keyed = {("__main__", n): sp for n, sp in specs.items()}
    for it in range(8):
        stats.passes += 1
        changes = 0
        inliner = Inliner(keyed, "__main__", find_imported_names(tree), consts, verbose)
        tree = inliner.visit(tree)
        ast.fix_missing_locations(tree)
        stats.inlined += inliner.inline_count
        changes += inliner.inline_count
        unroller = Unroller(consts, ast.unparse(tree), max_unroll, verbose)
        tree = unroller.visit(tree)
        ast.fix_missing_locations(tree)
        stats.unrolled += unroller.unroll_count
        changes += unroller.unroll_count
        if changes == 0:
            break
    return ast.unparse(tree), stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Inline + unroll a bundled bot submission.")
    parser.add_argument("input", help="Path to bundled submission.py")
    parser.add_argument("-o", "--out", default=None, help="Output path (default: <input>_opt.py)")
    parser.add_argument("--max-inline-body", type=int, default=12,
                        help="Max body assignments for an inlinable function (default 12)")
    parser.add_argument("--max-unroll", type=int, default=8,
                        help="Max iterations for automatic full unrolling (default 8)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-sanity", action="store_true",
                        help="Skip the post-write import sanity check")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        src = f.read()

    if args.out is None:
        base, ext = os.path.splitext(args.input)
        args.out = base + "_opt" + (ext or ".py")

    optimized, stats = optimize_source(
        src,
        max_inline_body=args.max_inline_body,
        max_unroll=args.max_unroll,
        verbose=args.verbose,
    )

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(optimized)

    print(f"Wrote {args.out} ({os.path.getsize(args.out)} bytes)")
    print(f"Stats: {stats}")

    if args.no_sanity:
        return
    cmd = [sys.executable, "-c",
           f"import importlib.util,sys; "
           f"spec=importlib.util.spec_from_file_location('m', r'{args.out}'); "
           f"m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
           f"assert callable(getattr(m,'agent', None)), 'no agent() function'; print('OK')"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("Sanity check FAILED:", file=sys.stderr)
        print(proc.stdout, proc.stderr, file=sys.stderr)
        sys.exit(1)
    print(f"Sanity check: {proc.stdout.strip()}")


if __name__ == "__main__":
    main()
