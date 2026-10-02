from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any


def import_callable(path: str) -> Callable[..., Any]:
    if ":" not in path:
        raise ValueError("Handler must use module:function syntax")
    module_name, func_name = path.split(":", 1)
    module = importlib.import_module(module_name)
    fn = getattr(module, func_name)
    if not callable(fn):
        raise TypeError(f"{path} is not callable")
    return fn
