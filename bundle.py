"""Bundle a multi-file bot into a single main.py suitable for Kaggle submission.

Local modules are embedded as strings and registered in sys.modules at import time,
so `from physics import fleet_speed` etc. keep working unchanged inside the bundle.

Usage:
    python bundle.py basic1                       # writes basic1/submission.py
    python bundle.py basic1 -o out.py
    python bundle.py basic1 --submit -m "v3"     # bundle + kaggle submit
"""

import argparse
import ast
import os
import subprocess
import sys


COMPETITION = "orbit-wars"


def find_local_modules(folder, exclude=()):
    """Return {module_name: filepath} for every .py file in folder except main.py and excludes."""
    mods = {}
    exclude_set = {os.path.abspath(p) for p in exclude}
    for name in os.listdir(folder):
        if not name.endswith(".py") or name == "main.py":
            continue
        full = os.path.join(folder, name)
        if os.path.abspath(full) in exclude_set:
            continue
        mods[name[:-3]] = full
    return mods


def imported_locals(src, local_names):
    """Parse `src` and return the set of local module names it imports."""
    used = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return used
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in local_names:
                    used.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                root = node.module.split(".")[0]
                if root in local_names:
                    used.add(root)
    return used


def topo_sort(deps):
    """deps: {mod: set(mod)}. Returns a list in load order."""
    order = []
    visited = set()
    visiting = set()

    def visit(m):
        if m in visited:
            return
        if m in visiting:
            raise ValueError(f"Circular import involving '{m}'")
        visiting.add(m)
        for d in deps.get(m, ()):
            visit(d)
        visiting.remove(m)
        visited.add(m)
        order.append(m)

    for m in deps:
        visit(m)
    return order


def bundle(folder, out_path):
    folder = os.path.abspath(folder)
    main_path = os.path.join(folder, "main.py")
    if not os.path.isfile(main_path):
        raise SystemExit(f"No main.py in {folder}")

    local_mods = find_local_modules(folder, exclude=[out_path])
    sources = {name: open(p, "r", encoding="utf-8").read() for name, p in local_mods.items()}
    deps = {name: imported_locals(src, local_mods) for name, src in sources.items()}
    order = topo_sort(deps)
    main_src = open(main_path, "r", encoding="utf-8").read()

    # Namespace each module's name so two bundled bots in the same process don't
    # collide in sys.modules. E.g., basic3's `trajectory` becomes `basic3__trajectory`.
    bundle_id = os.path.basename(folder)
    mangled = {name: f"{bundle_id}__{name}" for name in local_mods}

    def rewrite_imports(src):
        # Rewrite `from <mod> import X` and `import <mod>` to the mangled name.
        new_src = src
        for original, ns in mangled.items():
            # Match whole-word occurrences in import positions.
            new_src = new_src.replace(f"from {original} import", f"from {ns} import")
            new_src = new_src.replace(f"import {original}", f"import {ns}")
        return new_src

    rewritten_sources = {name: rewrite_imports(src) for name, src in sources.items()}
    rewritten_main = rewrite_imports(main_src)

    parts = [
        "# Auto-generated bundle. Do not edit by hand.",
        f"# Source: {os.path.basename(folder)}/",
        "import sys as _sys",
        "import types as _types",
        "",
        "_BUNDLED_MODULES = {",
    ]
    for name in order:
        parts.append(f"    {mangled[name]!r}: r'''{rewritten_sources[name]}''',")
    mangled_order = [mangled[n] for n in order]
    parts += [
        "}",
        "",
        "for _name in " + repr(mangled_order) + ":",
        "    _mod = _types.ModuleType(_name)",
        "    _mod.__file__ = _name + '.py'",
        "    _sys.modules[_name] = _mod",
        "    exec(compile(_BUNDLED_MODULES[_name], _name + '.py', 'exec'), _mod.__dict__)",
        "",
        "# === main.py ===",
        rewritten_main,
    ]
    out = "\n".join(parts)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)

    # Sanity check: no triple-quoted string in any source would break the raw r''' wrapper.
    for name, src in sources.items():
        if "'''" in src:
            print(f"WARNING: {name}.py contains ''' which may break the bundle.", file=sys.stderr)

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Bundle a bot into a single Kaggle-ready file.")
    parser.add_argument("folder", help="Path to bot folder (containing main.py)")
    parser.add_argument("-o", "--out", default=None, help="Output path (default: <folder>/submission.py)")
    parser.add_argument("--submit", action="store_true", help="Submit to Kaggle after bundling")
    parser.add_argument("-m", "--message", default=None, help="Kaggle submission message (prompts if --submit given without one)")
    parser.add_argument("--competition", default=COMPETITION, help="Kaggle competition slug")
    args = parser.parse_args()

    out_path = args.out or os.path.join(args.folder, "submission.py")
    out_path = bundle(args.folder, out_path)
    print(f"Bundled -> {out_path} ({os.path.getsize(out_path)} bytes)")

    # Verify it imports cleanly in a subprocess
    proc = subprocess.run(
        [sys.executable, "-c", f"import importlib.util,sys; "
         f"spec=importlib.util.spec_from_file_location('m', r'{out_path}'); "
         f"m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
         f"assert callable(getattr(m,'agent', None)), 'no agent() function'; print('OK')"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print("Sanity check FAILED:", file=sys.stderr)
        print(proc.stdout, proc.stderr, file=sys.stderr)
        sys.exit(1)
    print(f"Sanity check: {proc.stdout.strip()}")

    if args.submit:
        message = args.message
        while not message:
            try:
                message = input("Submission message: ").strip()
            except EOFError:
                sys.exit("No submission message provided.")
        cmd = ["kaggle", "competitions", "submit", args.competition,
               "-f", out_path, "-m", message]
        print("$ " + " ".join(cmd))
        r = subprocess.run(cmd)
        sys.exit(r.returncode)


if __name__ == "__main__":
    main()
