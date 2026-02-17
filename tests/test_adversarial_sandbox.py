"""Adversarial stress tests for the Alethic sandbox (tools.py).

Tests the expanded allowlist (scipy, sympy, mpmath, numpy) while verifying
that dangerous modules (os, sys, subprocess, pathlib, shutil) remain blocked.
Handles the case where scientific packages may or may not be installed.
"""

from __future__ import annotations

import pytest

from alethic.tools import execute_python

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_security_block(result: str) -> bool:
    """Return True if the sandbox blocked the import on policy grounds."""
    return "not allowed" in result.lower()


def _is_import_error(result: str) -> bool:
    """Return True if the module simply isn't installed (ImportError/ModuleNotFoundError)."""
    return (
        "ModuleNotFoundError" in result
        or ("ImportError" in result and "not allowed" not in result.lower())
    )


def _succeeded(result: str) -> bool:
    """Return True if execution completed without any ERROR."""
    return "ERROR" not in result and "TIMEOUT" not in result


# ---------------------------------------------------------------------------
# 1. scipy submodule import
# ---------------------------------------------------------------------------

class TestScipySubmodule:
    def test_scipy_special_import(self):
        """scipy.special should be importable (top-level 'scipy' is in allowlist)."""
        result = execute_python(
            "import scipy.special\n"
            "print(scipy.special.gamma(5))"
        )
        # Either it works (prints 24.0) or the package isn't installed
        if _is_import_error(result):
            pytest.skip("scipy not installed")
        assert not _is_security_block(result), (
            f"scipy.special was blocked by sandbox policy: {result}"
        )
        assert "24" in result  # gamma(5) = 4! = 24


# ---------------------------------------------------------------------------
# 2. mpmath import
# ---------------------------------------------------------------------------

class TestMpmathImport:
    def test_mpmath_dps(self):
        """mpmath should be importable and report its default decimal places."""
        result = execute_python("import mpmath; print(mpmath.mp.dps)")
        if _is_import_error(result):
            pytest.skip("mpmath not installed")
        assert not _is_security_block(result), (
            f"mpmath was blocked by sandbox policy: {result}"
        )
        # Default dps is typically 15
        assert _succeeded(result)
        assert result.strip().isdigit()


# ---------------------------------------------------------------------------
# 3. scipy.constants access
# ---------------------------------------------------------------------------

class TestScipyConstants:
    def test_scipy_constants_hbar(self):
        """from scipy import constants should give access to physical constants."""
        result = execute_python(
            "from scipy import constants\n"
            "print(constants.hbar)"
        )
        if _is_import_error(result):
            pytest.skip("scipy not installed")
        assert not _is_security_block(result), (
            f"scipy.constants was blocked by sandbox policy: {result}"
        )
        assert _succeeded(result)
        # hbar ~ 1.054e-34
        assert "1.054" in result or "e-34" in result


# ---------------------------------------------------------------------------
# 4. Nested disallowed import via allowed module
# ---------------------------------------------------------------------------

class TestTransitiveDeps:
    def test_scipy_io_transitive_os(self):
        """scipy.io internally uses os — sandbox should allow it because
        _restricted_import only checks what the USER imports, not transitive deps.
        """
        result = execute_python(
            "import scipy.io\n"
            "print('scipy.io imported successfully')"
        )
        if _is_import_error(result):
            pytest.skip("scipy not installed")
        assert not _is_security_block(result), (
            f"scipy.io was blocked by sandbox policy: {result}"
        )
        assert "successfully" in result


# ---------------------------------------------------------------------------
# 5. Still blocks os, sys, subprocess
# ---------------------------------------------------------------------------

class TestBlockedDangerousModules:
    def test_import_os_blocked(self):
        result = execute_python("import os")
        assert _is_security_block(result), f"os was NOT blocked: {result}"

    def test_import_sys_blocked(self):
        result = execute_python("import sys")
        assert _is_security_block(result), f"sys was NOT blocked: {result}"

    def test_import_subprocess_blocked(self):
        result = execute_python("import subprocess")
        assert _is_security_block(result), f"subprocess was NOT blocked: {result}"


# ---------------------------------------------------------------------------
# 6. Still blocks pathlib, shutil
# ---------------------------------------------------------------------------

class TestBlockedFilesystemModules:
    def test_import_pathlib_blocked(self):
        result = execute_python("import pathlib")
        assert _is_security_block(result), f"pathlib was NOT blocked: {result}"

    def test_import_shutil_blocked(self):
        result = execute_python("import shutil")
        assert _is_security_block(result), f"shutil was NOT blocked: {result}"


# ---------------------------------------------------------------------------
# 7. Prefix-match attack: "scipy_fake" should be blocked
# ---------------------------------------------------------------------------

class TestPrefixMatchAttack:
    def test_scipy_fake_blocked(self):
        """Allowlist checks top-level module name exactly, not prefixes.
        'scipy_fake' != 'scipy', so it must be blocked.
        """
        result = execute_python("import scipy_fake")
        # It should be a sandbox block, not just a ModuleNotFoundError from
        # actually trying to load a nonexistent package.
        assert _is_security_block(result), (
            f"scipy_fake was not blocked by policy: {result}"
        )

    def test_numpy_evil_blocked(self):
        """Similarly 'numpy_evil' must not pass the allowlist."""
        result = execute_python("import numpy_evil")
        assert _is_security_block(result), (
            f"numpy_evil was not blocked by policy: {result}"
        )


# ---------------------------------------------------------------------------
# 8. sympy.physics submodule
# ---------------------------------------------------------------------------

class TestSympyPhysics:
    def test_sympy_physics_quantum_ket(self):
        """sympy.physics.quantum.Ket should work since 'sympy' is allowed."""
        result = execute_python(
            "from sympy.physics.quantum import Ket\n"
            "print(Ket('psi'))"
        )
        if _is_import_error(result):
            pytest.skip("sympy not installed")
        assert not _is_security_block(result), (
            f"sympy.physics.quantum was blocked by sandbox policy: {result}"
        )
        assert _succeeded(result)
        assert "psi" in result


# ---------------------------------------------------------------------------
# 9. Double import doesn't bypass restrictions
# ---------------------------------------------------------------------------

class TestDoubleImport:
    def test_sequential_calls_retain_restrictions(self):
        """Calling execute_python twice must enforce restrictions both times."""
        # First call: allowed import
        r1 = execute_python("import math; print(math.pi)")
        assert _succeeded(r1)
        assert "3.14" in r1

        # Second call: disallowed import must still be blocked
        r2 = execute_python("import os; print('escaped')")
        assert _is_security_block(r2), (
            f"os was not blocked on second call: {r2}"
        )

    def test_sequential_calls_both_blocked(self):
        """Two consecutive blocked imports should both fail."""
        r1 = execute_python("import os")
        r2 = execute_python("import subprocess")
        assert _is_security_block(r1)
        assert _is_security_block(r2)


# ---------------------------------------------------------------------------
# 10. Timeout still works with scipy
# ---------------------------------------------------------------------------

class TestTimeoutWithScipy:
    def test_long_scipy_computation_times_out(self):
        """A deliberately slow scipy computation must be killed by the alarm."""
        code = (
            "import scipy\n"
            "import scipy.linalg\n"
            "import numpy as np\n"
            "# Create progressively larger eigenvalue problems to burn CPU\n"
            "i = 100\n"
            "while True:\n"
            "    m = np.random.randn(i, i)\n"
            "    scipy.linalg.eigvals(m)\n"
            "    i += 100\n"
        )
        result = execute_python(code, timeout_seconds=2)
        if _is_import_error(result):
            pytest.skip("scipy/numpy not installed")
        assert "TIMEOUT" in result, (
            f"Expected timeout but got: {result[:200]}"
        )


# ---------------------------------------------------------------------------
# 11. Attribute traversal escape attempts
# ---------------------------------------------------------------------------

class TestAttributeTraversalBlocked:
    """Verify that attribute-chain escapes via allowed modules are contained.

    The subprocess sandbox blocks *direct* imports of dangerous modules (os,
    sys, subprocess) via the import gate.  However, allowed modules like
    ``random`` and ``fractions`` hold internal references to ``os`` and ``sys``
    as module attributes (e.g. ``random._os``, ``fractions.sys``).

    Because execute_python() now runs in a child subprocess, attribute
    traversal still works *inside the child*, but the parent process is
    unaffected.  These tests document that behavior: the child CAN access
    ``random._os`` and ``fractions.sys`` (subprocess isolation does not
    restrict attribute access), but direct ``import os`` is still blocked
    by the import gate.
    """

    @pytest.mark.xfail(
        reason="Known limitation: attribute traversal bypasses import gate. "
               "Subprocess isolation prevents parent impact but child can run shell commands.",
        strict=False,
    )
    def test_random_os_system_blocked(self):
        """Attribute traversal via random._os.system() should ideally be blocked.

        Currently the import gate only intercepts ``import`` statements, not
        attribute access on pre-imported modules.  The child process can call
        random._os.system() — subprocess isolation limits blast radius but
        does not prevent execution.  This test is marked xfail to document
        the gap: it passes (xfail) when the escape works, and will xpass
        (unexpected pass) if future hardening blocks it.
        """
        result = execute_python(
            "import random\nprint(random._os.system('echo ESCAPED'))"
        )
        assert "ESCAPED" not in result, (
            f"Attribute traversal escape succeeded (known limitation): {result}"
        )

    @pytest.mark.xfail(
        reason="Known limitation: attribute traversal exposes os.environ in child.",
        strict=False,
    )
    def test_random_os_environ_blocked(self):
        """Attribute traversal via random._os.environ should ideally be blocked.

        The child subprocess inherits the parent environment.  Attribute
        traversal lets code reach os.environ, which may contain secrets
        like ANTHROPIC_API_KEY.  Marked xfail to document the gap.
        """
        result = execute_python(
            "import random\nprint(random._os.environ.get('HOME', 'NOPE'))"
        )
        assert _is_security_block(result), (
            f"Attribute traversal env access succeeded (known limitation): {result}"
        )

    def test_os_system_direct_import_still_blocked(self):
        """Direct 'import os' is blocked by the import gate.

        While random._os works via attribute traversal, a direct
        ``import os`` statement is caught by the restricted __import__
        hook.  This confirms the import gate is still enforced even
        though attribute traversal bypasses it.
        """
        result = execute_python("import os; os.system('echo ESCAPED')")
        assert _is_security_block(result), (
            f"Direct 'import os' was NOT blocked: {result}"
        )
        assert "ESCAPED" not in result, (
            f"Shell command ran despite import block: {result}"
        )

    @pytest.mark.xfail(
        reason="Known limitation: fractions.sys accessible via attribute traversal "
               "(CPython implementation detail, may vary across versions).",
        strict=False,
    )
    def test_fractions_sys_blocked(self):
        """Attribute traversal via fractions.sys should ideally be blocked.

        CPython's fractions module holds a reference to sys.  This is an
        implementation detail that may change across versions.  Marked
        xfail to document without asserting success.
        """
        result = execute_python(
            "import fractions\nprint(fractions.sys.executable)"
        )
        assert _is_security_block(result), (
            f"Attribute traversal sys access succeeded (known limitation): {result}"
        )


# ---------------------------------------------------------------------------
# 12. Thread safety — execute_python() from worker threads
# ---------------------------------------------------------------------------

class TestSubprocessThreadSafety:
    """Verify that execute_python() works from ThreadPoolExecutor workers.

    The old in-process sandbox used signal.signal(SIGALRM) for timeouts,
    which raises ValueError when called from a non-main thread.  The new
    subprocess-based sandbox avoids this: signal.signal() is called inside
    the child process (which IS the main thread of that process), not in
    the parent's worker thread.  subprocess.run(timeout=...) handles the
    parent-side timeout without signals.
    """

    def test_execute_from_worker_thread(self):
        """Basic execute_python() call from a ThreadPoolExecutor worker."""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execute_python, "print(2 + 2)")
            result = future.result(timeout=15)

        assert "4" in result, f"Expected '4' in output: {result}"
        assert _succeeded(result)

    def test_execute_timeout_from_worker_thread(self):
        """Timeout is enforced when execute_python() runs in a worker thread.

        Uses an infinite loop (not time.sleep, which requires 'time'
        import) to trigger the child's SIGALRM timeout.
        """
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                execute_python, "while True: pass", 2
            )
            result = future.result(timeout=15)

        assert "TIMEOUT" in result, (
            f"Expected TIMEOUT from worker thread: {result}"
        )

    def test_multiple_parallel_executions(self):
        """Multiple concurrent execute_python() calls via ThreadPoolExecutor.

        Submits 3 independent computations in parallel and verifies all
        return correct results.  This exercises the subprocess-per-call
        model under concurrent load.
        """
        from concurrent.futures import ThreadPoolExecutor

        codes = [
            ("print(1 + 1)", "2"),
            ("print(2 * 3)", "6"),
            ("print(7 ** 2)", "49"),
        ]

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(execute_python, code)
                for code, _ in codes
            ]
            results = [f.result(timeout=15) for f in futures]

        for (code, expected), result in zip(codes, results):
            assert expected in result, (
                f"Code {code!r}: expected {expected!r} in {result!r}"
            )
            assert _succeeded(result)
