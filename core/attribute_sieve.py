"""Attribute-phantom sieve: attribute access is verified like call kwargs."""
from __future__ import annotations

import ast
import typing
from typing import Any


def _resolve_imports(tree: ast.AST) -> dict[str, Any]:
    """Imported name -> live object; failures are silence."""
    import importlib

    out: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                try:
                    mod = importlib.import_module(node.module)
                    obj = getattr(mod, alias.name, None)
                except Exception:  # noqa: BLE001, S112 — сомнение = молчание
                    continue
                if obj is not None:
                    out[alias.asname or alias.name] = obj
    return out


def _return_type(func: Any) -> Any:
    try:
        return typing.get_type_hints(func).get("return")
    except Exception:  # noqa: BLE001 — сомнение = молчание
        return None


def _element_type(tp: Any) -> Any:
    """Element type of tuple[X, ...] / list[X] / Iterable[X], else None."""
    args = typing.get_args(tp)
    if not args:
        return None
    if typing.get_origin(tp) is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return args[0]
        return None
    return args[0] if len(args) == 1 else None


def _call_type(node: ast.expr, imports: dict[str, Any]) -> Any:
    """Static type of a call/name expression, or None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        target = imports.get(node.func.id)
        if isinstance(target, type):
            return target
        if callable(target):
            return _return_type(target)
    return None


def _bind_types(tree: ast.AST, imports: dict[str, Any]) -> dict[str, Any]:
    """Local name -> class, from constructor calls, annotated returns and
    tuple-unpacking loops. Anything unresolved stays unbound (silence)."""
    bound: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
            node.targets[0], ast.Name
        ):
            tp = _call_type(node.value, imports)
            if tp is not None:
                bound[node.targets[0].id] = tp
        if isinstance(node, (ast.For, ast.comprehension)):
            iter_node = node.iter
            src = None
            if isinstance(iter_node, ast.Name):
                src = bound.get(iter_node.id)
            else:
                src = _call_type(iter_node, imports)
            elem = _element_type(src) if src is not None else None
            target = node.target
            if elem is None or target is None:
                continue
            if isinstance(target, ast.Name) and isinstance(elem, type):
                bound[target.id] = elem
            elif isinstance(target, ast.Tuple) and typing.get_origin(elem) is tuple:
                parts = typing.get_args(elem)
                names = target.elts
                if len(parts) == len(names):
                    for name_node, part in zip(names, parts, strict=False):
                        if isinstance(name_node, ast.Name) and isinstance(part, type):
                            bound[name_node.id] = part
    return bound


def _class_lacks(cls: type, attr: str) -> bool:
    """True only when the class PROVABLY has no such attribute."""
    if not isinstance(cls, type):
        return False
    if "__getattr__" in dir(cls) and getattr(cls, "__getattr__", None) is not getattr(
        object, "__getattr__", None
    ):
        return False  # dynamic attributes: doubt = silence
    if attr in getattr(cls, "__dataclass_fields__", {}):
        return False
    return not hasattr(cls, attr)


def phantom_attribute_reason(code: str) -> str | None:
    """One named reason, or None. Judges structure, never strings."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    imports = _resolve_imports(tree)
    if not imports:
        return None
    bound = _bind_types(tree, imports)
    for name, obj in imports.items():
        if isinstance(obj, type):
            bound.setdefault(name, obj)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            cls = bound.get(node.value.id)
            if isinstance(cls, type) and _class_lacks(cls, node.attr):
                return (
                    f"attribute {node.attr!r} does not exist on "
                    f"{cls.__name__} (the real class carries no such field)"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            cls = bound.get(node.args[0].id)
            attr = node.args[1].value
            if isinstance(cls, type) and _class_lacks(cls, attr):
                return (
                    f"getattr asks for {attr!r} which does not exist on "
                    f"{cls.__name__} (the real class carries no such field)"
                )
    return None
