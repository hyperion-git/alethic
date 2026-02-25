# Verify & Check Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `alethic verify` and `alethic check` commands (CLI + Python library + skills) with multi-verifier consensus, domain auto-detection, and hybrid synthesis.

**Architecture:** Two new commands share a consensus pipeline (K parallel verifiers → mechanical aggregation → LLM critique cleanup). `verify` takes problem+solution, `check` takes solution only. Both produce a `ConsensusResult`. A static JSON dictionary enables domain auto-detection. New skill orchestrator (`verify-orchestrator.md`) is shared by `/alethic-verify` and `/alethic-check` thin configurators.

**Tech Stack:** Python 3.10+, anthropic SDK, ThreadPoolExecutor (parallel verification), JSON (domain dictionary), existing sandbox + matplotlib expansion.

**Design doc:** `docs/plans/2026-02-25-verify-check-design.md`

---

### Task 1: Domain Auto-Detection Dictionary

Create the classification dictionary and detection module.

**Files:**
- Create: `src/alethic/data/domain-keywords.json`
- Create: `src/alethic/domain.py`
- Test: `tests/test_domain.py`

**Step 1: Write the failing tests**

```python
# tests/test_domain.py
"""Tests for domain auto-detection."""
from __future__ import annotations

import pytest

from alethic.domain import detect_domain


class TestDetectDomain:
    def test_physics_strong_signal(self):
        text = "We begin with the Hamiltonian H = p²/2m + V(x) and solve the Schrödinger equation."
        assert detect_domain(text) == "physics"

    def test_math_strong_signal(self):
        text = "Theorem: For all primes p, Fermat's little theorem states a^p ≡ a (mod p). Proof by induction."
        assert detect_domain(text) == "math"

    def test_ambiguous_defaults_to_math(self):
        text = "Consider the function f(x) = x² + 1."
        assert detect_domain(text) == "math"

    def test_empty_defaults_to_math(self):
        assert detect_domain("") == "math"

    def test_physics_moderate_signals(self):
        text = "The energy of the system is conserved. The momentum transfer during the collision is calculated using force and impulse."
        assert detect_domain(text) == "physics"

    def test_math_moderate_signals(self):
        text = "The polynomial has degree 5. By the fundamental theorem of algebra, it has 5 roots counting multiplicity. We check convergence of the series."
        assert detect_domain(text) == "math"

    def test_override_respected(self):
        """detect_domain with explicit override should return that override."""
        text = "The Hamiltonian is H = T + V"  # physics signal
        assert detect_domain(text, override="math") == "math"
        assert detect_domain(text, override="physics") == "physics"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_domain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alethic.domain'`

**Step 3: Create the domain keywords dictionary**

Create `src/alethic/data/domain-keywords.json` — a JSON file with ~500 terms per domain across three weighted tiers (strong=3, moderate=2, weak=1). Strong tier: ~50 highly discriminative terms. Moderate tier: ~150 terms. Weak tier: ~300 terms. Include both domains.

Use an LLM to generate the initial dictionary, then review for:
- Cross-domain terms (e.g., "eigenvalue") — place in both at appropriate tiers
- False positives — remove overly generic terms from strong tier
- Coverage — ensure subfields are represented (topology, algebra, analysis, QFT, stat mech, etc.)

Structure:
```json
{
  "physics": {
    "strong": ["Hamiltonian", "Lagrangian", "Schrödinger", ...],
    "moderate": ["energy", "momentum", "potential", ...],
    "weak": ["conservation", "symmetry", ...]
  },
  "math": {
    "strong": ["theorem", "proof", "lemma", ...],
    "moderate": ["polynomial", "convergence", ...],
    "weak": ["function", "set", ...]
  }
}
```

**Step 4: Implement detect_domain()**

```python
# src/alethic/domain.py
"""Domain auto-detection for verify/check commands."""
from __future__ import annotations

import json
import re
from importlib import resources


_TIER_WEIGHTS = {"strong": 3, "moderate": 2, "weak": 1}
_KEYWORDS: dict | None = None


def _load_keywords() -> dict:
    global _KEYWORDS
    if _KEYWORDS is None:
        ref = resources.files("alethic.data").joinpath("domain-keywords.json")
        _KEYWORDS = json.loads(ref.read_text(encoding="utf-8"))
    return _KEYWORDS


def detect_domain(text: str, *, override: str | None = None) -> str:
    """Detect whether text is math or physics.

    Args:
        text: The solution/derivation text to classify.
        override: If set, skip detection and return this value.

    Returns:
        "math" or "physics".
    """
    if override is not None:
        return override

    if not text.strip():
        return "math"

    keywords = _load_keywords()
    text_lower = text.lower()

    scores: dict[str, float] = {}
    for domain, tiers in keywords.items():
        score = 0.0
        for tier_name, terms in tiers.items():
            weight = _TIER_WEIGHTS.get(tier_name, 1)
            for term in terms:
                # Word-boundary match, case-insensitive
                if re.search(r'\b' + re.escape(term.lower()) + r'\b', text_lower):
                    score += weight
        scores[domain] = score

    if scores.get("physics", 0) > scores.get("math", 0):
        return "physics"
    return "math"  # tie or math wins → default to math
```

Also create `src/alethic/data/__init__.py` (empty file for package marker).

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_domain.py -v`
Expected: PASS (all 7 tests)

**Step 6: Lint and type check**

Run: `ruff check src/alethic/domain.py tests/test_domain.py && mypy src/alethic/domain.py`

**Step 7: Commit**

```bash
git add src/alethic/data/ src/alethic/domain.py tests/test_domain.py
git commit -m "feat: add domain auto-detection with keyword dictionary"
```

---

### Task 2: New Data Models

Add `VerifierConfig`, `ConsensusResult`, and `ConsensusIssue` to `models.py`.

**Files:**
- Modify: `src/alethic/models.py` (after line 222, before `Solution`)
- Modify: `src/alethic/__init__.py` (add exports)
- Test: `tests/test_alethic.py` (extend `TestModels`)

**Step 1: Write the failing tests**

Add to `tests/test_alethic.py`:

```python
from alethic.models import ConsensusIssue, ConsensusResult, VerifierConfig

class TestVerifierModels:
    def test_verifier_config_defaults(self):
        config = VerifierConfig()
        assert config.model == "claude-opus-4-6"
        assert config.num_verifiers == 3
        assert config.tool_guidance == frozenset({"sympy", "numpy", "scipy", "matplotlib"})
        assert config.domain is None

    def test_verifier_config_presets(self):
        quick = VerifierConfig.from_preset("quick")
        assert quick.num_verifiers == 2
        thorough = VerifierConfig.from_preset("thorough")
        assert thorough.num_verifiers == 5
        assert thorough.extended_thinking is True
        extreme = VerifierConfig.from_preset("extreme")
        assert extreme.num_verifiers == 7

    def test_verifier_config_preset_override(self):
        config = VerifierConfig.from_preset("quick", num_verifiers=4)
        assert config.num_verifiers == 4

    def test_verifier_config_unknown_preset(self):
        with pytest.raises(ValueError, match="Unknown preset"):
            VerifierConfig.from_preset("nonexistent")

    def test_verifier_config_validation(self):
        with pytest.raises(ValueError, match="num_verifiers must be >= 1"):
            VerifierConfig(num_verifiers=0)

    def test_consensus_result_basics(self):
        result = ConsensusResult(
            verdict=Verdict.CORRECT,
            confidence=0.91,
            confidence_range=(0.85, 0.95),
            critique="Looks good",
            issues=[],
            individual_results=[],
            domain_detected="math",
            num_verifiers=3,
            elapsed_seconds=12.5,
        )
        assert result.consensus_ratio == "0/0"  # no individual results
        assert result.verdict == Verdict.CORRECT

    def test_consensus_result_with_individuals(self):
        vr1 = VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.95)
        vr2 = VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90)
        vr3 = VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="hmm", confidence=0.85)
        result = ConsensusResult(
            verdict=Verdict.CORRECT,
            confidence=0.90,
            confidence_range=(0.85, 0.95),
            critique="Synthesized",
            issues=[ConsensusIssue(text="Minor gap", severity=IssueSeverity.MINOR, flagged_by=1)],
            individual_results=[vr1, vr2, vr3],
            domain_detected="physics",
            num_verifiers=3,
            elapsed_seconds=30.0,
        )
        assert result.consensus_ratio == "2/3"

    def test_consensus_issue(self):
        issue = ConsensusIssue(text="Sign error in step 3", severity=IssueSeverity.MAJOR, flagged_by=2)
        assert issue.flagged_by == 2
        assert issue.severity == IssueSeverity.MAJOR
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_alethic.py::TestVerifierModels -v`
Expected: FAIL with `ImportError`

**Step 3: Implement models**

Add to `src/alethic/models.py` after line 222 (after `AgentConfig.from_preset`), before `Solution`:

```python
@dataclass(frozen=True)
class VerifierConfig:
    """Configuration for standalone verify and check commands.

    Controls multi-verifier consensus: K independent verifiers run in parallel,
    results are mechanically aggregated, then a lightweight LLM pass cleans up
    the merged critique.
    """

    model: str = "claude-opus-4-6"
    num_verifiers: int = 3
    tool_guidance: frozenset[str] = frozenset({"sympy", "numpy", "scipy", "matplotlib"})
    domain: str | None = None  # None = auto-detect
    enable_code_execution: bool = True
    temperature: float = 0.2
    max_tokens: int = 16384
    extended_thinking: bool = False
    thinking_budget: int = 15000
    verbose: bool = True

    def __post_init__(self) -> None:
        if self.num_verifiers < 1:
            raise ValueError(f"num_verifiers must be >= 1, got {self.num_verifiers}")
        _VALID_TOOLS = {"sympy", "numpy", "scipy", "matplotlib"}
        invalid = self.tool_guidance - _VALID_TOOLS
        if invalid:
            raise ValueError(f"Unknown tool_guidance values: {invalid}. Valid: {_VALID_TOOLS}")
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {self.max_tokens}")

    PRESETS: ClassVar[dict[str, dict[str, Any]]] = {
        "quick": {"num_verifiers": 2, "extended_thinking": False, "max_tokens": 16384},
        "default": {"num_verifiers": 3, "extended_thinking": False, "max_tokens": 16384},
        "thorough": {"num_verifiers": 5, "extended_thinking": True, "thinking_budget": 15000, "max_tokens": 32768},
        "extreme": {"num_verifiers": 7, "extended_thinking": True, "thinking_budget": 40000, "max_tokens": 65536},
    }

    @classmethod
    def from_preset(cls, name: str, **overrides: Any) -> VerifierConfig:
        if name not in cls.PRESETS:
            raise ValueError(f"Unknown preset '{name}'. Available: {', '.join(cls.PRESETS)}")
        params = dict(cls.PRESETS[name])
        params.update(overrides)
        return cls(**params)
```

Add after `VerificationResult` (after line 273):

```python
@dataclass(frozen=True)
class ConsensusIssue:
    """An issue flagged by one or more independent verifiers."""

    text: str
    severity: IssueSeverity = IssueSeverity.MAJOR
    flagged_by: int = 1  # how many of K verifiers flagged this


@dataclass
class ConsensusResult:
    """Synthesized result from K independent verifications."""

    verdict: Verdict
    confidence: float
    confidence_range: tuple[float, float]
    critique: str
    issues: list[ConsensusIssue]
    individual_results: list[VerificationResult]
    domain_detected: str
    num_verifiers: int
    elapsed_seconds: float = 0.0

    @property
    def consensus_ratio(self) -> str:
        """E.g. '3/3' or '2/3' — how many verifiers agree with the majority verdict."""
        if not self.individual_results:
            return "0/0"
        agree = sum(1 for r in self.individual_results if r.verdict == self.verdict)
        return f"{agree}/{len(self.individual_results)}"
```

**Step 4: Update `__init__.py` exports**

Add `VerifierConfig`, `ConsensusResult`, `ConsensusIssue` to imports and `__all__` in `src/alethic/__init__.py`.

**Step 5: Run tests**

Run: `pytest tests/test_alethic.py::TestVerifierModels -v`
Expected: PASS

**Step 6: Run full test suite to ensure no regressions**

Run: `pytest -v`
Expected: All existing tests PASS

**Step 7: Lint and type check**

Run: `ruff check src/alethic/models.py && mypy src/alethic/models.py`

**Step 8: Commit**

```bash
git add src/alethic/models.py src/alethic/__init__.py tests/test_alethic.py
git commit -m "feat: add VerifierConfig, ConsensusResult, ConsensusIssue models"
```

---

### Task 3: Synthesizer Module

Mechanical aggregation + LLM critique cleanup.

**Files:**
- Create: `src/alethic/synthesizer.py`
- Test: `tests/test_synthesizer.py`

**Step 1: Write the failing tests**

```python
# tests/test_synthesizer.py
"""Tests for consensus synthesis."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alethic.models import (
    Issue,
    IssueSeverity,
    SectionConfidence,
    Verdict,
    VerificationResult,
)
from alethic.synthesizer import aggregate_mechanical, synthesize_critique


class TestMechanicalAggregation:
    def test_unanimous_correct(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="Good", confidence=0.95),
            VerificationResult(verdict=Verdict.CORRECT, critique="Also good", confidence=0.90),
            VerificationResult(verdict=Verdict.CORRECT, critique="Agreed", confidence=0.92),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.CORRECT
        assert abs(agg["confidence"] - 0.9233) < 0.01
        assert agg["confidence_range"] == (0.90, 0.95)

    def test_majority_verdict(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.85),
            VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="hmm", confidence=0.75),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.CORRECT

    def test_no_majority_takes_most_severe(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
            VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="hmm", confidence=0.80),
            VerificationResult(verdict=Verdict.MAJOR_FLAW, critique="bad", confidence=0.60),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.MAJOR_FLAW

    def test_issues_union_with_vote_counts(self):
        results = [
            VerificationResult(
                verdict=Verdict.MINOR_ISSUES, critique="a", confidence=0.80,
                issues=[Issue(text="Sign error in step 3", severity=IssueSeverity.MAJOR)],
            ),
            VerificationResult(
                verdict=Verdict.MINOR_ISSUES, critique="b", confidence=0.82,
                issues=[
                    Issue(text="Sign error in step 3", severity=IssueSeverity.MAJOR),
                    Issue(text="Missing edge case", severity=IssueSeverity.MINOR),
                ],
            ),
            VerificationResult(
                verdict=Verdict.CORRECT, critique="c", confidence=0.90,
                issues=[],
            ),
        ]
        agg = aggregate_mechanical(results)
        # "Sign error" should be flagged by 2, "Missing edge case" by 1
        sign_issues = [i for i in agg["issues"] if "sign error" in i.text.lower()]
        assert len(sign_issues) == 1
        assert sign_issues[0].flagged_by == 2

    def test_single_verifier(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.CORRECT
        assert agg["confidence"] == 0.90
        assert agg["confidence_range"] == (0.90, 0.90)
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_synthesizer.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement synthesizer**

```python
# src/alethic/synthesizer.py
"""Consensus synthesis for multi-verifier results.

Two-stage pipeline:
1. Mechanical aggregation (deterministic): majority-vote verdict, mean confidence,
   union of issues with vote counts.
2. LLM critique cleanup (one API call): merges K raw critiques into one coherent text.
   Does NOT override verdict or confidence.
"""
from __future__ import annotations

import logging
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from alethic.models import (
    ConsensusIssue,
    IssueSeverity,
    Verdict,
    VerificationResult,
)

logger = logging.getLogger("alethic")

# Severity ordering for tie-breaking (most severe first)
_SEVERITY_ORDER = {
    Verdict.MAJOR_FLAW: 0,
    Verdict.UNSOLVED: 1,
    Verdict.MINOR_ISSUES: 2,
    Verdict.CORRECT: 3,
}

_ISSUE_SIMILARITY_THRESHOLD = 0.6

SYNTHESIZER_SYSTEM = """\
You are a technical editor. You receive {k} independent verification reports \
and a mechanical aggregation (verdict, confidence, issues with vote counts).

Your task: produce ONE coherent, well-written critique that represents the \
consensus view.

## Rules

1. You MUST NOT change the verdict or confidence — those are determined \
   mechanically and are final.
2. Weight issues by how many reviewers flagged them.
3. Resolve contradictions explicitly (e.g., "2 of 3 reviewers found X; \
   1 disagreed because Y").
4. Eliminate redundancy across reports.
5. Maintain the severity classifications from the aggregation.
6. Be concise — this is a report, not an essay.

## Output format

Write ONLY the unified critique text. Do not include verdict, confidence, \
or any other metadata — those are handled separately.
"""

SYNTHESIZER_USER = """\
## Mechanical Aggregation

Verdict: {verdict}
Confidence: {confidence:.2f}

Issues:
{issues_text}

## Individual Reports

{reports_text}
"""


def _similar(a: str, b: str) -> bool:
    """Check if two issue texts are similar enough to merge."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= _ISSUE_SIMILARITY_THRESHOLD


def aggregate_mechanical(results: list[VerificationResult]) -> dict[str, Any]:
    """Deterministic aggregation of K verification results.

    Returns dict with keys: verdict, confidence, confidence_range, issues, section_confidences.
    """
    # Majority-vote verdict
    verdict_counts = Counter(r.verdict for r in results)
    most_common = verdict_counts.most_common()
    if len(most_common) == 1 or most_common[0][1] > most_common[1][1]:
        verdict = most_common[0][0]
    else:
        # Tie — pick most severe
        tied = [v for v, c in most_common if c == most_common[0][1]]
        verdict = min(tied, key=lambda v: _SEVERITY_ORDER.get(v, 99))

    # Mean confidence
    confidences = [r.confidence for r in results]
    confidence = sum(confidences) / len(confidences)
    confidence_range = (min(confidences), max(confidences))

    # Union of issues with vote counts (deduplicated by similarity)
    merged_issues: list[ConsensusIssue] = []
    for r in results:
        for issue in r.issues:
            # Check if a similar issue already exists
            found = False
            for mi in merged_issues:
                if _similar(issue.text, mi.text):
                    # Merge: increment flagged_by, keep higher severity
                    higher_sev = min(
                        mi.severity, issue.severity,
                        key=lambda s: {"critical": 0, "major": 1, "minor": 2}.get(s.value, 1)
                    )
                    idx = merged_issues.index(mi)
                    merged_issues[idx] = ConsensusIssue(
                        text=mi.text,
                        severity=higher_sev,
                        flagged_by=mi.flagged_by + 1,
                    )
                    found = True
                    break
            if not found:
                merged_issues.append(
                    ConsensusIssue(text=issue.text, severity=issue.severity, flagged_by=1)
                )

    # Sort by flagged_by descending, then severity
    sev_order = {IssueSeverity.CRITICAL: 0, IssueSeverity.MAJOR: 1, IssueSeverity.MINOR: 2}
    merged_issues.sort(key=lambda i: (-i.flagged_by, sev_order.get(i.severity, 1)))

    return {
        "verdict": verdict,
        "confidence": confidence,
        "confidence_range": confidence_range,
        "issues": merged_issues,
    }


def synthesize_critique(
    client,
    results: list[VerificationResult],
    aggregation: dict[str, Any],
    *,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
) -> str:
    """LLM critique cleanup — merges K raw critiques into one coherent text.

    Does NOT override verdict or confidence.
    """
    issues_lines = []
    for issue in aggregation["issues"]:
        issues_lines.append(
            f"- [{issue.severity.value.upper()}] {issue.text} ({issue.flagged_by}/{len(results)})"
        )
    issues_text = "\n".join(issues_lines) if issues_lines else "None"

    reports = []
    for i, r in enumerate(results, 1):
        reports.append(
            f"### Verifier {i}: {r.verdict.value.upper()} ({r.confidence:.2f})\n\n{r.critique}"
        )
    reports_text = "\n\n".join(reports)

    system = SYNTHESIZER_SYSTEM.format(k=len(results))
    user_msg = SYNTHESIZER_USER.format(
        verdict=aggregation["verdict"].value.upper(),
        confidence=aggregation["confidence"],
        issues_text=issues_text,
        reports_text=reports_text,
    )

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.3,
    )

    parts = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(parts) if parts else "[Synthesis failed]"
```

**Step 4: Run tests**

Run: `pytest tests/test_synthesizer.py -v`
Expected: PASS (all 5 mechanical aggregation tests)

**Step 5: Lint**

Run: `ruff check src/alethic/synthesizer.py tests/test_synthesizer.py`

**Step 6: Commit**

```bash
git add src/alethic/synthesizer.py tests/test_synthesizer.py
git commit -m "feat: add consensus synthesizer with mechanical aggregation"
```

---

### Task 4: Sandbox Expansion (matplotlib)

Add matplotlib to the sandbox allowlist.

**Files:**
- Modify: `src/alethic/tools.py` (lines 57-63 and 93-98)
- Test: `tests/test_alethic.py` (extend sandbox tests)

**Step 1: Write the failing test**

```python
def test_matplotlib_allowed_in_sandbox(self):
    """matplotlib should be importable in the sandbox."""
    result = execute_python("import matplotlib; print(matplotlib.__name__)")
    assert "matplotlib" in result
    assert "not allowed" not in result

def test_matplotlib_agg_backend(self):
    """matplotlib.use('Agg') should work in sandbox."""
    result = execute_python(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nprint('ok')"
    )
    assert "ok" in result
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_alethic.py -k "matplotlib" -v`
Expected: FAIL with "Import of 'matplotlib' is not allowed"

**Step 3: Add matplotlib to allowlists**

In `src/alethic/tools.py`, add `"matplotlib"` to both `_ALLOWED_MODULES` (line 62) and the `_ALLOWED_MODULES` set inside `_WORKER_SCRIPT` (line 97). Also update `PYTHON_TOOL` description (line 16-29) to mention matplotlib.

**Step 4: Run tests**

Run: `pytest tests/test_alethic.py -k "matplotlib" -v`
Expected: PASS (if matplotlib is installed in the test env)

**Step 5: Run full test suite**

Run: `pytest -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/alethic/tools.py tests/test_alethic.py
git commit -m "feat: add matplotlib to sandbox allowlist"
```

---

### Task 5: Check-Specific Prompts

New prompt templates for the internal consistency checker.

**Files:**
- Create: `src/alethic/check_prompts.py`
- Test: `tests/test_alethic.py` (verify prompts are importable and non-empty)

**Step 1: Write the failing test**

```python
def test_check_prompts_exist(self):
    from alethic.check_prompts import CHECKER_SYSTEM, CHECKER_USER
    assert "internally valid" in CHECKER_SYSTEM.lower() or "proof auditor" in CHECKER_SYSTEM.lower()
    assert "{solution}" in CHECKER_USER
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_alethic.py -k "check_prompts" -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Create check_prompts.py**

New prompts for the internal consistency checker (solution only, no problem statement). See design doc Section 5b for the prompt design. The CHECKER_USER template takes `{solution}` only (no `{problem}` placeholder). Include:

- `CHECKER_SYSTEM`: System prompt for proof auditor role
- `CHECKER_USER`: User template with `{solution}` placeholder
- `CHECKER_TOOL_GUIDANCE`: Tool guidance map for scipy/matplotlib verifier overlays (in addition to sympy/numpy)
  - `scipy` verifier guidance: scipy.constants, scipy.integrate, scipy.special
  - `matplotlib` verifier guidance: plot functions to visually verify claims, save to file

The existing `TOOL_GUIDANCE` from `prompts.py` covers sympy and numpy. The new file adds scipy and matplotlib entries and exports a combined `CHECK_TOOL_GUIDANCE` dict that merges all four.

**Step 4: Run tests**

Run: `pytest tests/test_alethic.py -k "check_prompts" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/check_prompts.py tests/test_alethic.py
git commit -m "feat: add check-specific prompts for internal consistency auditing"
```

---

### Task 6: VerifierAgent and CheckerAgent

The core consensus pipeline.

**Files:**
- Create: `src/alethic/verifier_agent.py`
- Test: `tests/test_verify_check.py`

**Step 1: Write the failing tests**

```python
# tests/test_verify_check.py
"""Tests for VerifierAgent and CheckerAgent."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alethic.models import (
    ConsensusResult,
    Verdict,
    VerifierConfig,
    VerificationResult,
)
from alethic.verifier_agent import CheckerAgent, VerifierAgent


class TestVerifierAgent:
    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.subagents.verify")
    def test_verify_runs_k_verifiers(self, mock_verify, mock_synth):
        """verify() should call the verify subagent K times."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="ok", confidence=0.90
        )
        mock_synth.return_value = "Synthesized critique"

        config = VerifierConfig(num_verifiers=3)
        agent = VerifierAgent(config=config, api_key="test-key")
        result = agent.verify(problem="Is 1+1=2?", solution="Yes, 1+1=2.")

        assert mock_verify.call_count == 3
        assert isinstance(result, ConsensusResult)
        assert result.num_verifiers == 3
        assert result.verdict == Verdict.CORRECT

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.subagents.verify")
    def test_check_runs_without_problem(self, mock_verify, mock_synth):
        """check() should work with solution only."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="valid", confidence=0.88
        )
        mock_synth.return_value = "Looks valid"

        config = VerifierConfig(num_verifiers=2)
        agent = CheckerAgent(config=config, api_key="test-key")
        result = agent.check(solution="2+2=4 because of Peano axioms...")

        assert mock_verify.call_count == 2
        assert isinstance(result, ConsensusResult)

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.subagents.verify")
    def test_domain_auto_detected(self, mock_verify, mock_synth):
        """Domain should be auto-detected from solution text."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="ok", confidence=0.90
        )
        mock_synth.return_value = "ok"

        config = VerifierConfig(num_verifiers=2)
        agent = VerifierAgent(config=config, api_key="test-key")
        result = agent.verify(
            problem="Derive the energy levels",
            solution="Starting from the Hamiltonian H = p²/2m + V(x)..."
        )
        assert result.domain_detected == "physics"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verify_check.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement VerifierAgent**

```python
# src/alethic/verifier_agent.py
"""Standalone verification agents with multi-verifier consensus.

VerifierAgent: problem + solution → ConsensusResult
CheckerAgent: solution only → ConsensusResult (internal consistency)
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from alethic.check_prompts import CHECKER_SYSTEM, CHECKER_USER, CHECK_TOOL_GUIDANCE
from alethic.domain import detect_domain
from alethic.models import (
    AgentConfig,
    ConsensusResult,
    Solution,
    VerifierConfig,
    VerificationResult,
)
from alethic.prompts import TOOL_GUIDANCE, VERIFIER_SYSTEM, VERIFIER_USER
from alethic.physics_prompts import (
    PHYSICS_TOOL_GUIDANCE,
    PHYSICS_VERIFIER_SYSTEM,
    PHYSICS_VERIFIER_USER,
)
from alethic.subagents import verify as verify_subagent
from alethic.synthesizer import aggregate_mechanical, synthesize_critique

logger = logging.getLogger("alethic")


class VerifierAgent:
    """Runs K independent verifications and synthesizes a consensus."""

    def __init__(self, config: VerifierConfig | None = None, *, api_key: str | None = None):
        self.config = config or VerifierConfig()
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    def _select_prompts(self, domain: str) -> tuple[str, str]:
        """Return (system_prompt, user_template) for the detected domain."""
        if domain == "physics":
            return PHYSICS_VERIFIER_SYSTEM, PHYSICS_VERIFIER_USER
        return VERIFIER_SYSTEM, VERIFIER_USER

    def _build_agent_config(self) -> AgentConfig:
        """Adapt VerifierConfig to AgentConfig for the verify() subagent."""
        return AgentConfig(
            model=self.config.model,
            enable_code_execution=self.config.enable_code_execution,
            temperature_verifier=self.config.temperature,
            max_tokens=self.config.max_tokens,
            extended_thinking=self.config.extended_thinking,
            thinking_budget=self.config.thinking_budget,
            tool_guidance=frozenset(t for t in self.config.tool_guidance if t in {"sympy", "numpy"}),
            verbose=False,  # sub-verifiers are silent
        )

    def _run_single_verify(
        self, problem: str, solution_text: str, system: str, user_template: str
    ) -> VerificationResult:
        """Run one independent verification."""
        agent_config = self._build_agent_config()
        sol = Solution(problem=problem, solution_text=solution_text, iteration=0)
        return verify_subagent(
            self.client, problem=problem, solution=sol, config=agent_config,
            system_prompt=system, user_template=user_template,
        )

    def verify(self, problem: str, solution: str) -> ConsensusResult:
        """Verify a solution against a stated problem with K-verifier consensus."""
        start = time.time()
        domain = detect_domain(f"{problem}\n{solution}", override=self.config.domain)
        system, user_template = self._select_prompts(domain)
        k = self.config.num_verifiers

        if self.config.verbose:
            print(f"Running {k} independent verifiers (domain: {domain})...")

        # Run K verifiers in parallel
        results: list[VerificationResult] = []
        with ThreadPoolExecutor(max_workers=k) as executor:
            futures = [
                executor.submit(self._run_single_verify, problem, solution, system, user_template)
                for _ in range(k)
            ]
            for future in as_completed(futures):
                results.append(future.result())

        return self._synthesize(results, domain, start)

    def check(self, solution: str) -> ConsensusResult:
        """Check internal consistency of a solution (no problem statement)."""
        raise NotImplementedError("Use CheckerAgent for check()")

    def _synthesize(
        self, results: list[VerificationResult], domain: str, start: float
    ) -> ConsensusResult:
        """Mechanical aggregation + LLM critique cleanup."""
        aggregation = aggregate_mechanical(results)

        if self.config.verbose:
            print(f"Aggregated: {aggregation['verdict'].value} ({aggregation['confidence']:.2f})")
            print("Synthesizing critique...")

        critique = synthesize_critique(
            self.client, results, aggregation, model=self.config.model
        )

        return ConsensusResult(
            verdict=aggregation["verdict"],
            confidence=aggregation["confidence"],
            confidence_range=aggregation["confidence_range"],
            critique=critique,
            issues=aggregation["issues"],
            individual_results=results,
            domain_detected=domain,
            num_verifiers=len(results),
            elapsed_seconds=time.time() - start,
        )


class CheckerAgent(VerifierAgent):
    """Internal consistency checker — solution only, no problem statement."""

    def _select_prompts(self, domain: str) -> tuple[str, str]:
        # Check uses its own prompts regardless of domain
        # (but domain still influences tool guidance)
        return CHECKER_SYSTEM, CHECKER_USER

    def check(self, solution: str) -> ConsensusResult:
        """Check internal consistency with K-verifier consensus."""
        start = time.time()
        domain = detect_domain(solution, override=self.config.domain)
        system, user_template = self._select_prompts(domain)
        k = self.config.num_verifiers

        if self.config.verbose:
            print(f"Running {k} independent checkers (domain: {domain})...")

        results: list[VerificationResult] = []
        with ThreadPoolExecutor(max_workers=k) as executor:
            futures = [
                executor.submit(
                    self._run_single_verify,
                    "",  # no problem for check
                    solution,
                    system,
                    user_template,
                )
                for _ in range(k)
            ]
            for future in as_completed(futures):
                results.append(future.result())

        return self._synthesize(results, domain, start)

    def verify(self, problem: str, solution: str) -> ConsensusResult:
        raise NotImplementedError("Use VerifierAgent for verify()")
```

**Step 4: Run tests**

Run: `pytest tests/test_verify_check.py -v`
Expected: PASS

**Step 5: Update `__init__.py`**

Add `VerifierAgent`, `CheckerAgent` to imports and `__all__`.

**Step 6: Lint**

Run: `ruff check src/alethic/verifier_agent.py tests/test_verify_check.py`

**Step 7: Commit**

```bash
git add src/alethic/verifier_agent.py src/alethic/__init__.py tests/test_verify_check.py
git commit -m "feat: add VerifierAgent and CheckerAgent with consensus pipeline"
```

---

### Task 7: CLI Subcommands

Add `verify` and `check` to the CLI.

**Files:**
- Modify: `src/alethic/cli.py`
- Test: `tests/test_adversarial_cli.py` (extend with verify/check tests)

**Step 1: Write the failing tests**

```python
def test_detect_verify_subcommand(self):
    from alethic.cli import _detect_subcommand
    cmd, argv = _detect_subcommand(["verify", "--problem", "test", "solution.md"])
    assert cmd == "verify"

def test_detect_check_subcommand(self):
    from alethic.cli import _detect_subcommand
    cmd, argv = _detect_subcommand(["check", "solution.md"])
    assert cmd == "check"
```

**Step 2: Run tests to verify they fail**

**Step 3: Implement CLI changes**

Changes to `src/alethic/cli.py`:

1. Update `_detect_subcommand()` (line 277): add `"verify"`, `"check"` to the recognized set
2. Add new arguments to `build_parser()`:
   - `--problem/-p` (verify only — redefine: for solve/derive this conflicts with `--preset`, so use `--problem-text` and `--problem-file/-P`)
   - `--solution/-s` (solution text inline)
   - `--solution-file/-S` (solution from file)
   - `--domain` (math/physics override)
   - `--verifiers/-K` (number of verifiers)
3. Add `_FLAGS_WITH_VALUE` entries for new flags
4. Add `_build_verifier_config()` function (like `_build_config` but for `VerifierConfig`)
5. Add `_verify_handler()` and `_check_handler()` functions
6. Update `main()` to dispatch: `if command in ("verify", "check"): return _verify_check_handler(args, command)`
7. Update description and epilog with verify/check examples

**Step 4: Run tests**

Run: `pytest tests/test_adversarial_cli.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest -v`

**Step 6: Commit**

```bash
git add src/alethic/cli.py tests/test_adversarial_cli.py
git commit -m "feat: add verify and check CLI subcommands"
```

---

### Task 8: Output Formatting

Formatted stdout, JSON, and quiet-mode output for ConsensusResult.

**Files:**
- Create: `src/alethic/output.py`
- Test: `tests/test_output.py`

**Step 1: Write the failing tests**

Test `format_consensus()` produces expected text with box-drawing chars, JSON mode produces valid JSON, quiet mode produces single-line output.

**Step 2: Implement output.py**

Functions:
- `format_consensus(result: ConsensusResult, mode: str) -> str` — renders to text/json/quiet
- Reuse the output format from design doc Section 6

**Step 3: Wire into CLI handler**

**Step 4: Run tests, lint, commit**

```bash
git add src/alethic/output.py tests/test_output.py src/alethic/cli.py
git commit -m "feat: add formatted output for consensus results"
```

---

### Task 9: Session Input Resolution

Support pointing at existing `.alethic/` session directories.

**Files:**
- Create: `src/alethic/session_reader.py`
- Test: `tests/test_session_reader.py`

**Step 1: Write the failing tests**

Test `resolve_session_input()` with mock session dirs (use `tmp_path` fixture):
- Session with `output.md` and `problem.md` → returns (problem, solution)
- Session with only `worklog/best_solution.md` → falls back
- Missing `problem.md` → returns (None, solution)
- Non-session directory → raises ValueError

**Step 2: Implement session_reader.py**

```python
def resolve_session_input(path: str) -> tuple[str | None, str]:
    """Extract (problem, solution) from an alethic session directory."""
```

**Step 3: Wire into CLI — when first positional arg is a directory with `session.json`, use session reader**

**Step 4: Run tests, lint, commit**

```bash
git add src/alethic/session_reader.py tests/test_session_reader.py src/alethic/cli.py
git commit -m "feat: add session input resolution for verify/check"
```

---

### Task 10: Skill — /alethic-verify

Create the Claude Code skill.

**Files:**
- Create: `skills/alethic-verify/SKILL.md`
- Create: `skills/alethic-verify/references/verifier.md`
- Create: `skills/alethic-verify/references/tools/sympy-verifier.md`
- Create: `skills/alethic-verify/references/tools/numpy-verifier.md`
- Create: `skills/alethic-verify/references/tools/scipy-verifier.md`
- Create: `skills/alethic-verify/references/tools/matplotlib-verifier.md`

**Step 1: Create SKILL.md**

Follow the thin configurator pattern from `skills/alethic-solve/SKILL.md`. Set domain variables:
- mode: verify
- requires_problem: true
- default_tools: sympy,numpy,scipy,matplotlib
- num_verifiers_preset_table: quick=2, default=3, thorough=5, extreme=7

Load `verify-orchestrator.md` from `alethic-common/`.

**Step 2: Create reference prompts**

Adapt from `skills/alethic-solve/references/` — the verifier prompt for "does this solve the stated problem?" with structured output format.

**Step 3: Create tool overlays**

Adapt from existing `skills/alethic-solve/references/tools/` — add scipy and matplotlib overlays.

**Step 4: Commit**

```bash
git add skills/alethic-verify/
git commit -m "feat: add /alethic-verify skill configurator and prompts"
```

---

### Task 11: Skill — /alethic-check

Create the Claude Code skill.

**Files:**
- Create: `skills/alethic-check/SKILL.md`
- Create: `skills/alethic-check/references/checker.md`
- Create: `skills/alethic-check/references/tools/` (same 4 overlays)

**Step 1: Create SKILL.md**

Same pattern as verify but:
- mode: check
- requires_problem: false
- Checker-specific prompt (internal consistency, no problem statement)

**Step 2: Create reference prompts**

The checker prompt from design doc Section 5b — proof auditor role, 6-point evaluation criteria.

**Step 3: Create tool overlays**

Same as verify (or symlink).

**Step 4: Commit**

```bash
git add skills/alethic-check/
git commit -m "feat: add /alethic-check skill configurator and prompts"
```

---

### Task 12: Shared Verify Orchestrator

The skill orchestrator shared by both verify and check skills.

**Files:**
- Create: `skills/alethic-common/verify-orchestrator.md`
- Create: `skills/alethic-common/references/domain-keywords.json` (copy from src/alethic/data/)
- Create: `skills/alethic-common/references/synthesizer.md`

**Step 1: Write verify-orchestrator.md**

Parameterized by thin configurator variables. Steps:
- Step 0: Parse input, resolve session dirs, auto-detect domain
- Step 1: Launch K verifier sub-agents in parallel (Task tool)
- Step 2: Mechanical aggregation
- Step 3: Synthesizer sub-agent for critique cleanup
- Step 4: Assemble final report, write output.md

**Step 2: Write synthesizer.md**

Prompt template for the LLM critique cleanup sub-agent.

**Step 3: Copy domain-keywords.json**

Same dictionary for both library and skills.

**Step 4: Commit**

```bash
git add skills/alethic-common/verify-orchestrator.md skills/alethic-common/references/
git commit -m "feat: add shared verify-orchestrator and synthesizer prompt"
```

---

### Task 13: Plugin Manifest Updates

Register new skills in plugin.json.

**Files:**
- Modify: `.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json` (if applicable)

**Step 1: Add /alethic-verify and /alethic-check to plugin.json skills array**

**Step 2: Commit**

```bash
git add .claude-plugin/
git commit -m "feat: register /alethic-verify and /alethic-check in plugin manifest"
```

---

### Task 14: CLAUDE.md and Documentation Updates

Update project docs to reflect new commands.

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md` (if exists)

**Step 1: Update CLAUDE.md**

- Add verify/check to Dev Commands section
- Add verify/check to Module Map
- Add new skill files to the Skill file table
- Update Key Design Decisions with consensus verification
- Add VerifierConfig preset table

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with verify and check commands"
```

---

### Task 15: Final Integration Test

Verify everything works end-to-end.

**Files:** None (test-only)

**Step 1: Run full test suite**

Run: `pytest -v --tb=short`
Expected: All tests PASS

**Step 2: Lint entire project**

Run: `ruff check src tests`

**Step 3: Type check**

Run: `mypy src/alethic`

**Step 4: Manual smoke test (if API key available)**

```bash
echo "2+2=4 because addition." > /tmp/solution.md
alethic verify "What is 2+2?" --solution-file /tmp/solution.md --preset quick
alethic check --solution-file /tmp/solution.md --preset quick
```

**Step 5: Final commit (if any fixups needed)**

```bash
git commit -m "fix: integration test fixups"
```
