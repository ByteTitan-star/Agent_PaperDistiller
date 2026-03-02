from typing import Any
import contextlib
import io
import math
import re


BLOCKED_PATTERN = re.compile(
    r"\b(import|open|exec|eval|compile|globals|locals|__|os|sys|subprocess|socket|pathlib|shutil|input)\b",
    flags=re.IGNORECASE,
)


def run(code: str, _context: dict[str, Any] | None = None) -> dict[str, Any]:
    snippet = (code or "").strip()
    if not snippet:
        return {"error": "code is empty"}
    if len(snippet) > 900:
        return {"error": "code exceeds 900 characters"}
    if BLOCKED_PATTERN.search(snippet):
        return {"error": "code contains blocked keywords"}

    safe_builtins = {
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "range": range,
        "round": round,
        "float": float,
        "int": int,
        "print": print,
    }
    safe_globals: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "math": math,
    }
    safe_locals: dict[str, Any] = {}
    stdout = io.StringIO()

    try:
        with contextlib.redirect_stdout(stdout):
            exec(snippet, safe_globals, safe_locals)
    except Exception as exc:
        return {"error": str(exc)}

    visible_locals = {
        key: repr(value)[:200]
        for key, value in safe_locals.items()
        if not key.startswith("_")
    }
    return {
        "stdout": stdout.getvalue()[:2000],
        "locals": visible_locals,
    }
