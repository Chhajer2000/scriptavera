# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Find names used in the app's modules that are defined nowhere (a NameError or AttributeError waiting in a
rarely run path): bare names, and attributes looked up on the app's own modules (pipeline.build_outputs)."""
import ast, builtins, importlib, os, pathlib, symtable, sys, types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SCRIPTAVERA_MOCK", "1")


def walk(table, module, missing, where=""):
    for sym in table.get_symbols():
        if sym.is_referenced() and sym.is_global() and not sym.is_assigned():
            name = sym.get_name()
            if not hasattr(module, name) and not hasattr(builtins, name):
                missing.append(f"{module.__name__}: {where or '<module>'} uses undefined name {name!r}")
    for child in table.get_children():
        walk(child, module, missing, f"{where}.{child.get_name()}" if where else child.get_name())


def attributes(tree, module, missing):
    """module.attr where `module` is one of ours and has no such attribute."""
    local = set()
    for node in ast.walk(tree):                       # names rebound inside functions are not the module
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for a in node.args.args + node.args.kwonlyargs:
                local.add(a.arg)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            target = getattr(module, node.value.id, None)
            if node.value.id in local or not isinstance(target, types.ModuleType):
                continue
            if (target.__name__.startswith("scriptavera") or target.__name__.startswith("tests")) and not hasattr(target, node.attr):
                missing.append(f"{module.__name__}: line {node.lineno} uses {node.value.id}.{node.attr}, which does not exist")


def main() -> int:
    missing: list[str] = []
    for path in sorted((ROOT / "scriptavera").glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = importlib.import_module(f"scriptavera.{path.stem}")
        src = path.read_text(encoding="utf-8")
        walk(symtable.symtable(src, str(path), "exec"), module, missing)
        attributes(ast.parse(src), module, missing)
    for m in missing:
        print(m)
    print("names ok" if not missing else f"{len(missing)} undefined name(s)")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
