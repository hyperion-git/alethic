"""Tool definitions for Alethic subagents.

Provides Python code execution via Anthropic's tool_use API, mirroring
Alethic's use of computational verification alongside natural-language reasoning.
"""

from __future__ import annotations

import contextlib
import io
import re
import traceback

# Anthropic tool schema for Python code execution
PYTHON_TOOL = {
    "name": "execute_python",
    "description": (
        "Execute Python code for computational verification. "
        "Use this to check calculations, test conjectures with examples, "
        "verify formulas numerically, or perform symbolic computation. "
        "Available libraries: math, fractions, decimal, itertools, functools, "
        "collections, operator, random, statistics. "
        "NumPy, SymPy, SciPy, and mpmath are also available if installed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute. Print results to stdout.",
            }
        },
        "required": ["code"],
    },
}


# Safe subset of builtins for the sandbox
_SAFE_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "chr", "complex", "dict",
    "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "hash", "hex", "int", "isinstance", "issubclass", "iter", "len",
    "list", "map", "max", "min", "next", "oct", "ord", "pow", "print",
    "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "str", "sum", "tuple", "type", "zip",
}

# Allowed imports
_ALLOWED_MODULES = {
    "math", "cmath", "fractions", "decimal", "itertools", "functools",
    "collections", "operator", "random", "statistics", "re", "string",
    "textwrap", "numbers",
    # Scientific (available if installed)
    "numpy", "sympy", "scipy", "mpmath",
}


def execute_python(code: str, timeout_seconds: int = 30) -> str:
    """Execute Python code in a restricted sandbox and return stdout + result.

    Args:
        code: Python source code to execute.
        timeout_seconds: Max execution time (enforced via signal on Unix).

    Returns:
        String containing stdout output and/or error messages.
    """
    import signal

    stdout_capture = io.StringIO()

    # Build restricted globals
    import builtins

    safe_builtins = {k: getattr(builtins, k) for k in _SAFE_BUILTINS if hasattr(builtins, k)}
    safe_builtins["__import__"] = _restricted_import

    restricted_globals = {"__builtins__": safe_builtins}

    # Pre-import commonly needed modules
    for mod_name in ("math", "fractions", "decimal", "itertools", "functools"):
        try:
            restricted_globals[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    # Try importing numpy and sympy
    for mod_name in ("numpy", "sympy"):
        try:
            restricted_globals[mod_name] = __import__(mod_name)
            if mod_name == "numpy":
                restricted_globals["np"] = restricted_globals[mod_name]
            if mod_name == "sympy":
                restricted_globals["sp"] = restricted_globals[mod_name]
        except ImportError:
            pass

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Code execution exceeded {timeout_seconds}s limit")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_seconds)

    try:
        with contextlib.redirect_stdout(stdout_capture):
            exec(compile(code, "<alethic-sandbox>", "exec"), restricted_globals)

        output = stdout_capture.getvalue()
        if not output:
            output = "[Code executed successfully with no output]"
        return output.strip()

    except TimeoutError as e:
        return f"TIMEOUT ERROR: {e}"
    except Exception:
        tb = traceback.format_exc()
        # Strip internal frames
        return f"EXECUTION ERROR:\n{tb}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _restricted_import(name, *args, **kwargs):
    """Only allow importing from the approved module list."""
    top_level = name.split(".")[0]
    if top_level not in _ALLOWED_MODULES:
        raise ImportError(
            f"Import of '{name}' is not allowed. "
            f"Allowed modules: {', '.join(sorted(_ALLOWED_MODULES))}"
        )
    return __import__(name, *args, **kwargs)


def extract_code_blocks(text: str) -> list[str]:
    """Extract Python code blocks from model output.

    Looks for <code>...</code> tags or ```python...``` fenced blocks.
    """
    blocks = []

    # <code> tags
    for match in re.finditer(r"<code>(.*?)</code>", text, re.DOTALL):
        blocks.append(match.group(1).strip())

    # ```python fenced blocks
    for match in re.finditer(r"```python\s*\n(.*?)```", text, re.DOTALL):
        blocks.append(match.group(1).strip())

    return blocks


def process_tool_calls(response) -> list[dict]:
    """Process tool_use blocks from an Anthropic API response.

    Args:
        response: Anthropic message response object.

    Returns:
        List of dicts with keys: tool_use_id, name, result.
    """
    results = []
    for block in response.content:
        if block.type == "tool_use" and block.name == "execute_python":
            code = block.input.get("code", "")
            output = execute_python(code)
            results.append({
                "tool_use_id": block.id,
                "name": block.name,
                "result": output,
            })
    return results
