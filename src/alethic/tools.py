"""Tool definitions for Alethic subagents.

Provides Python code execution via Anthropic's tool_use API, mirroring
Alethic's use of computational verification alongside natural-language reasoning.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap

# Anthropic tool schema for Python code execution
PYTHON_TOOL = {
    "name": "execute_python",
    "description": (
        "Execute Python code for computational verification. "
        "Use this to check calculations, test conjectures with examples, "
        "verify formulas numerically, or perform symbolic computation. "
        "SymPy is pre-imported as `sp` — use it for symbolic simplification, "
        "integration, series expansion, equation solving, and matrix algebra. "
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


# Safe subset of builtins for the sandbox.
# NOTE: These module-level constants are used by PYTHON_TOOL description and tests.
# The ACTUAL sandbox enforcement is in _WORKER_SCRIPT below — if you change the
# allowlist here, you MUST update _WORKER_SCRIPT as well.
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


# Self-contained worker script executed in a child subprocess.
# Reads {"code": ..., "timeout": ...} from stdin, sets up the same restricted
# builtins / import gate / pre-imports used by the old in-process sandbox,
# enforces a SIGALRM timeout (safe because the child is always main-thread),
# and prints stdout on success or error/timeout messages on failure.
_WORKER_SCRIPT = textwrap.dedent(r'''
    import builtins as _builtins_mod
    import json
    import signal
    import sys
    import traceback

    # ── Read payload from stdin ──────────────────────────────────────────
    _payload = json.loads(sys.stdin.read())
    _code = _payload["code"]
    _timeout = _payload["timeout"]

    # ── Safe builtins ────────────────────────────────────────────────────
    _SAFE_BUILTINS = {
        "abs", "all", "any", "bin", "bool", "chr", "complex", "dict",
        "divmod", "enumerate", "filter", "float", "format", "frozenset",
        "hash", "hex", "int", "isinstance", "issubclass", "iter", "len",
        "list", "map", "max", "min", "next", "oct", "ord", "pow", "print",
        "range", "repr", "reversed", "round", "set", "slice", "sorted",
        "str", "sum", "tuple", "type", "zip",
    }

    _ALLOWED_MODULES = {
        "math", "cmath", "fractions", "decimal", "itertools", "functools",
        "collections", "operator", "random", "statistics", "re", "string",
        "textwrap", "numbers",
        "numpy", "sympy", "scipy", "mpmath",
    }

    # ── Restricted import gate ───────────────────────────────────────────
    _real_import = __import__

    def _restricted_import(name, *args, **kwargs):
        top_level = name.split(".")[0]
        if top_level not in _ALLOWED_MODULES:
            raise ImportError(
                f"Import of '{name}' is not allowed. "
                f"Allowed modules: {', '.join(sorted(_ALLOWED_MODULES))}"
            )
        return _real_import(name, *args, **kwargs)

    # ── Build restricted globals ─────────────────────────────────────────
    _safe_builtins = {
        k: getattr(_builtins_mod, k)
        for k in _SAFE_BUILTINS
        if hasattr(_builtins_mod, k)
    }
    _safe_builtins["__import__"] = _restricted_import

    _restricted_globals = {"__builtins__": _safe_builtins}

    _pre_imports = {
        "math": None, "fractions": None, "decimal": None,
        "itertools": None, "functools": None,
        "numpy": "np", "sympy": "sp",
    }
    for _mod_name, _alias in _pre_imports.items():
        try:
            _mod = _real_import(_mod_name)
            _restricted_globals[_mod_name] = _mod
            if _alias:
                _restricted_globals[_alias] = _mod
        except ImportError:
            pass

    # ── Timeout handler (safe: child process is always main thread) ──────
    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Code execution exceeded {_timeout}s limit")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(_timeout)

    # ── Execute user code ────────────────────────────────────────────────
    try:
        exec(compile(_code, "<alethic-sandbox>", "exec"), _restricted_globals)
    except TimeoutError as e:
        signal.alarm(0)
        print(f"TIMEOUT ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        signal.alarm(0)
        tb = traceback.format_exc()
        print(f"EXECUTION ERROR:\n{tb}", file=sys.stderr)
        sys.exit(1)
    finally:
        signal.alarm(0)
''').lstrip()


def execute_python(code: str, timeout_seconds: int = 30) -> str:
    """Execute Python code in a restricted child-process sandbox.

    The user code runs in a separate subprocess with restricted builtins,
    an import allowlist, and a SIGALRM timeout.  The parent enforces a
    backup timeout via ``subprocess.run(timeout=...)``.

    Args:
        code: Python source code to execute.
        timeout_seconds: Max execution time in seconds.

    Returns:
        String containing stdout output and/or error messages.
    """
    payload = json.dumps({"code": code, "timeout": timeout_seconds})

    try:
        result = subprocess.run(
            [sys.executable, "-c", _WORKER_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
        )
    except subprocess.TimeoutExpired:
        return f"TIMEOUT ERROR: Code execution exceeded {timeout_seconds}s limit"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            return stderr
        return f"EXECUTION ERROR:\nProcess exited with code {result.returncode}"

    output = result.stdout.strip()
    if not output:
        return "[Code executed successfully with no output]"
    return output


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
            if not code.strip():
                output = "ERROR: Empty code provided to execute_python"
            else:
                output = execute_python(code)
            results.append({
                "tool_use_id": block.id,
                "name": block.name,
                "result": output,
            })
    return results
