# Hierarchical Inconsistency Classifier — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `error_taxonomy.py` from flat keyword-first-match to a hierarchical tree classifier with structured output, backward-compatible wrapper, and new `wrong_method` category.

**Architecture:** Replace `_TAXONOMY_KEYWORDS` dict + `_TAXONOMY_PATTERNS` list with `_TREE` (level structure) + `_KEYWORDS` (category→keywords) + `_KEYWORD_PATTERNS` (category→regex). New `classify_inconsistency()` returns `InconsistencyResult(level, primary, all_matches)`. Old `classify_errors()` wraps it. One consumer change in `agent.py` for VERIFY event `error_level` field.

**Tech Stack:** Python 3.13, dataclasses, re (regex). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-23-inconsistency-classifier-design.md`

---

### Task 1: Add InconsistencyResult dataclass and classify_inconsistency()

**Files:**
- Modify: `src/alethic/error_taxonomy.py`

- [ ] **Step 1: Add InconsistencyResult dataclass**

At the top of `error_taxonomy.py`, after imports, add:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class InconsistencyResult:
    """Hierarchical inconsistency classification result.

    level: which layer of the inconsistency tree fired
    primary: best category at the firing level
    all_matches: category -> keyword hit count across all levels
    """
    level: str
    primary: str
    all_matches: dict[str, int]
```

- [ ] **Step 2: Restructure keywords into _TREE + _KEYWORDS + _KEYWORD_PATTERNS**

Replace the current `_TAXONOMY_KEYWORDS` dict and `_TAXONOMY_PATTERNS` list with three structures. The `_KEYWORDS` dict holds the same keyword lists as before (with `wrong_method` added). `_TREE` defines level groupings. `_KEYWORD_PATTERNS` is a dict (not list) keyed by category.

```python
_TREE: list[tuple[str, list[str]]] = [
    ("problem",      ["false_premise", "counterexample"]),
    ("approach",     ["wrong_method"]),
    ("structural",   ["missing_case", "logic"]),
    ("mechanical",   ["algebra", "units"]),
    ("presentation", ["interpretation", "citation"]),
]

_KEYWORDS: dict[str, list[str]] = {
    "false_premise": [
        "false premise", "false claim", "claim is false", "statement is false",
        "does not hold", "no valid solution", "no solution exists",
        "unsolvable", "cannot be proved", "impossible to prove",
        "contradicts known", "violates known",
    ],
    "counterexample": [
        "counterexample", "flaw found", "breaker found",
        "regime failure", "falsif",
    ],
    "wrong_method": [
        "wrong approach", "different method", "different approach",
        "not suitable", "inapplicable", "should use", "consider using",
        "try instead", "does not apply here", "not the right",
    ],
    "missing_case": [
        "missing case", "edge case", "special case",
        "boundary case", "boundary condition", "not handled", "case analysis",
        "degenerate", "not considered", "overlooked",
    ],
    "logic": [
        "does not follow", "non sequitur", "circular", "circular argument",
        "implication", "gap in", "logical gap", "invalid inference",
        "unjustified", "without justification", "not proven", "assumption not established",
    ],
    "algebra": [
        "sign error", "wrong sign", "arithmetic", "calculation error",
        "simplif", "expand", "factor", "distribut", "algebraic error",
        "incorrect step", "wrong value", "computation error",
    ],
    "units": [
        "dimension", "dimensional", "si unit", "inconsistent units",
        "dimensionless", "dimensional mismatch",
    ],
    "interpretation": [
        "misinterpret", "misread", "wrong problem", "reinterpret",
        "different question", "weaker problem", "specification gaming",
    ],
    "citation": [
        "citation", "cite", "well known", "standard result", "it can be shown",
        "it is known", "no source", "no reference", "no proof given", "vague appeal",
        "theorem name", "by a known", "appeal to",
    ],
}

_KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {
    cat: re.compile("|".join(re.escape(kw) for kw in kws))
    for cat, kws in _KEYWORDS.items()
}
```

Remove the old `_TAXONOMY_KEYWORDS` and `_TAXONOMY_PATTERNS` entirely.

- [ ] **Step 3: Add scoring function and classify_inconsistency()**

```python
def _score_keyword(categories: list[str], lower: str) -> dict[str, int]:
    """Score categories by keyword hit count. Returns {category: hits}."""
    scores = {}
    for cat in categories:
        hits = len(_KEYWORD_PATTERNS[cat].findall(lower))
        if hits > 0:
            scores[cat] = hits
    return scores


_score_fn = _score_keyword


def classify_inconsistency(critique: str) -> InconsistencyResult:
    """Classify a verifier critique into the inconsistency tree.

    Traverses levels top-down. The first level with keyword hits determines
    the firing level and primary category. All levels are scanned to populate
    all_matches.

    Returns InconsistencyResult with level, primary category, and all matches.
    """
    lower = critique.lower()
    all_matches: dict[str, int] = {}
    firing_level: str | None = None
    primary: str | None = None

    for level_name, categories in _TREE:
        level_scores = _score_fn(categories, lower)
        all_matches.update(level_scores)

        if firing_level is None and level_scores:
            firing_level = level_name
            primary = max(
                level_scores,
                key=lambda c: (level_scores[c], -categories.index(c)),
            )

    if firing_level is None:
        return InconsistencyResult("unknown", "general", {})
    return InconsistencyResult(firing_level, primary, all_matches)
```

- [ ] **Step 4: Update classify_errors() to wrap classify_inconsistency()**

Replace the current `classify_errors()` body:

```python
def classify_errors(critique: str) -> str:
    """Classify a verifier critique into an error category via keyword heuristics.

    Backward-compatible wrapper around classify_inconsistency().
    Returns the primary category string.
    """
    return classify_inconsistency(critique).primary
```

- [ ] **Step 5: Add get_all_categories() helper**

```python
def get_all_categories() -> set[str]:
    """Return all categories defined in the inconsistency tree."""
    return {cat for _, cats in _TREE for cat in cats}
```

- [ ] **Step 6: Update classify_errors_routed()**

```python
def classify_errors_routed(critique: str) -> tuple[str, OracleType, bool]:
    """Classify critique and return (category, next_oracle, force_adversarial)."""
    category = classify_inconsistency(critique).primary
    oracle, force_adv = _ORACLE_ROUTING[category]
    return category, oracle, force_adv
```

- [ ] **Step 7: Add wrong_method to REVISION_ADDENDA and _ORACLE_ROUTING**

```python
# In REVISION_ADDENDA dict:
"wrong_method": (
    "\n\n## Revision focus: change of approach\n"
    "The current method appears fundamentally unsuitable for this problem. "
    "Do NOT revise within the current approach — choose a categorically "
    "different method. Consider what mathematical/physical structure the "
    "problem has (symmetry, recursion, conservation law, etc.) and pick "
    "a technique that exploits that structure directly."
),

# In _ORACLE_ROUTING dict:
"wrong_method": (OracleType.LAYER3_LLM_ADVERSARIAL, True),
```

- [ ] **Step 8: Verify module loads without errors**

Run: `python -c "from alethic.error_taxonomy import classify_inconsistency, classify_errors, InconsistencyResult; print('OK')"`

Expected: `OK`

- [ ] **Step 9: Commit**

```bash
git add src/alethic/error_taxonomy.py
git commit -m "refactor(taxonomy): hierarchical inconsistency classifier with tree-based levels

Replace flat _TAXONOMY_KEYWORDS with _TREE + _KEYWORDS + _KEYWORD_PATTERNS.
New classify_inconsistency() returns InconsistencyResult(level, primary,
all_matches). classify_errors() becomes backward-compatible wrapper.
New wrong_method category at approach level. Pluggable _score_fn for
future trained classifier (Option C)."
```

---

### Task 2: Update tests for new data structures

**Files:**
- Modify: `tests/test_probe_taxonomy.py`

- [ ] **Step 1: Update imports**

Replace:
```python
from alethic.error_taxonomy import (
    REVISION_ADDENDA,
    _ORACLE_ROUTING,
    _TAXONOMY_KEYWORDS,
    classify_errors,
    classify_errors_routed,
    get_revision_addendum,
)
```

With:
```python
from alethic.error_taxonomy import (
    REVISION_ADDENDA,
    InconsistencyResult,
    _KEYWORDS,
    _ORACLE_ROUTING,
    _TREE,
    classify_errors,
    classify_errors_routed,
    classify_inconsistency,
    get_all_categories,
    get_revision_addendum,
)
```

- [ ] **Step 2: Update TestRoutingTableCompleteness**

Replace ALL `_TAXONOMY_KEYWORDS` references with `get_all_categories()` or `_KEYWORDS`. Exhaustive list of changes:

- Line 33: `for category in _TAXONOMY_KEYWORDS:` → `for category in get_all_categories():`
- Line 44: `for category in _TAXONOMY_KEYWORDS:` → `for category in get_all_categories():`
- Line 65: `set(_TAXONOMY_KEYWORDS.keys()) | {"general"}` → `get_all_categories() | {"general"}`
- Line 73: `set(_TAXONOMY_KEYWORDS.keys()) | {"general"}` → `get_all_categories() | {"general"}`
- Line 83: `set(_TAXONOMY_KEYWORDS.keys()) | {"general"}` → `get_all_categories() | {"general"}`
- Line 85: `for category, keywords in _TAXONOMY_KEYWORDS.items():` → `for category, keywords in _KEYWORDS.items():`
- Line 101: `all_taxonomy_cats = set(_TAXONOMY_KEYWORDS.keys())` → `all_taxonomy_cats = get_all_categories()`
- Line 99: Add `"wrong_method"` to the `escalate_categories` set (since wrong_method routes to LAYER3_LLM_ADVERSARIAL with force_adversarial=True)
- Line 171: Update class docstring from "_TAXONOMY_KEYWORDS dict order" to "_TREE level order"
- Line 465: `list(_TAXONOMY_KEYWORDS.keys()) + ["general"]` → `list(get_all_categories()) + ["general"]`
- Line 481-482: `for category, keywords in _TAXONOMY_KEYWORDS.items():` → `for category, keywords in _KEYWORDS.items():`
- Line 487-488: same pattern
- Line 494-495: same pattern
- Line 502-503: same pattern
- Line 513-514: same pattern

- [ ] **Step 3: Update TestMultiCategoryPriority**

**Replace** the existing `test_priority_order_is_deterministic()` method entirely with two new tests:

```python
def test_tree_structure_is_deterministic(self):
    """_TREE defines 5 levels in severity-descending order."""
    expected_levels = ["problem", "approach", "structural", "mechanical", "presentation"]
    actual_levels = [level for level, _ in _TREE]
    assert actual_levels == expected_levels

def test_tree_categories_complete(self):
    """Every category in _KEYWORDS appears in exactly one _TREE level."""
    tree_cats = {cat for _, cats in _TREE for cat in cats}
    assert tree_cats == set(_KEYWORDS.keys())
```

Replace all the old `X_beats_Y` tests with tree-level-aware tests (these are already written in the spec's Testing section — copy them in). Key changes:
- `test_false_premise_beats_algebra` → still passes (problem level fires before mechanical)
- `test_logic_beats_algebra` → still passes (structural fires before mechanical)
- `test_algebra_beats_interpretation` → still passes (mechanical fires before presentation)
- Add: `test_wrong_method_beats_algebra`, `test_wrong_method_at_approach_level`

- [ ] **Step 4: Update TestKeywordCoverage**

Replace `_TAXONOMY_KEYWORDS` with `_KEYWORDS` in all references. Add `wrong_method` to coverage expectations.

- [ ] **Step 5: Update TestAddendumContentVerification**

Replace `for category in _TAXONOMY_KEYWORDS:` with `for category in _KEYWORDS:`. Add test for `wrong_method` addendum content (check it mentions "approach" or "method").

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_probe_taxonomy.py -v`

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add tests/test_probe_taxonomy.py
git commit -m "test(taxonomy): update tests for hierarchical tree structure

Replace _TAXONOMY_KEYWORDS imports with _KEYWORDS/get_all_categories().
Update priority tests for tree-level-based resolution.
Add wrong_method coverage and addendum tests."
```

---

### Task 3: Add TestInconsistencyResult test class

**Files:**
- Modify: `tests/test_probe_taxonomy.py`

- [ ] **Step 1: Add new test class with all spec'd tests**

Add at end of file:

```python
class TestInconsistencyResult:
    """Tests for the hierarchical classify_inconsistency() function."""

    def test_single_level_match(self):
        result = classify_inconsistency("There is a sign error in step 3")
        assert result.level == "mechanical"
        assert result.primary == "algebra"
        assert result.all_matches == {"algebra": 1}

    def test_multi_level_first_wins(self):
        result = classify_inconsistency("Sign error and the approach is not suitable")
        assert result.level == "approach"
        assert result.primary == "wrong_method"
        assert "algebra" in result.all_matches
        assert "wrong_method" in result.all_matches

    def test_first_firing_level_wins_over_higher_hits(self):
        # Algebra has 3 hits (sign error, wrong sign, arithmetic) but logic
        # fires first at level 2 (structural). First-firing-level wins,
        # not highest hit count across all levels.
        result = classify_inconsistency(
            "sign error, wrong sign, arithmetic — also a logical gap"
        )
        assert result.level == "structural"
        assert result.primary == "logic"
        assert result.all_matches["algebra"] == 3  # counted but lower level

    def test_within_level_tiebreak_by_position(self):
        result = classify_inconsistency("missing case and does not follow")
        assert result.level == "structural"
        assert result.primary == "missing_case"

    def test_no_match_returns_general(self):
        result = classify_inconsistency("The proof is beautiful but wrong")
        assert result.level == "unknown"
        assert result.primary == "general"
        assert result.all_matches == {}

    def test_wrong_method_at_approach_level(self):
        result = classify_inconsistency("The approach is not suitable for this problem")
        assert result.level == "approach"
        assert result.primary == "wrong_method"

    def test_wrong_method_beats_algebra(self):
        result = classify_inconsistency("Sign error and this method is inapplicable")
        assert result.level == "approach"
        assert result.primary == "wrong_method"

    def test_classify_errors_wrapper(self):
        assert classify_errors("sign error") == "algebra"
        assert classify_errors("false premise") == "false_premise"
        assert classify_errors("nothing here") == "general"
        assert classify_errors("wrong approach") == "wrong_method"

    def test_classify_errors_routed_still_works(self):
        cat, oracle, force = classify_errors_routed("sign error")
        assert cat == "algebra"

    def test_empty_critique(self):
        result = classify_inconsistency("")
        assert result.level == "unknown"
        assert result.primary == "general"

    def test_all_matches_populated_across_levels(self):
        result = classify_inconsistency(
            "false premise with a sign error and missing case"
        )
        assert result.level == "problem"
        assert result.primary == "false_premise"
        assert "false_premise" in result.all_matches
        assert "algebra" in result.all_matches
        assert "missing_case" in result.all_matches

    def test_result_is_frozen(self):
        result = classify_inconsistency("sign error")
        with pytest.raises(AttributeError):
            result.level = "other"
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_probe_taxonomy.py::TestInconsistencyResult -v`

Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_probe_taxonomy.py
git commit -m "test(taxonomy): add TestInconsistencyResult for hierarchical classifier"
```

---

### Task 4: Update agent.py VERIFY event to include error_level

**Files:**
- Modify: `src/alethic/agent.py`

- [ ] **Step 1: Update import**

At `src/alethic/agent.py:41`, change:
```python
from alethic.error_taxonomy import classify_errors, get_revision_addendum
```
to:
```python
from alethic.error_taxonomy import classify_errors, classify_inconsistency, get_revision_addendum
```

- [ ] **Step 2: Update VERIFY event emission**

At `src/alethic/agent.py:887-891`, change:
```python
                        error_category=classify_errors(ver.critique),
```
to:
```python
                        error_category=classify_errors(ver.critique),
                        error_level=classify_inconsistency(ver.critique).level,
```

Note: this calls `classify_inconsistency` twice (once via wrapper, once directly). Acceptable because it's a pure function with negligible cost (regex only). If preferred, can be refactored to call once:
```python
                        _inc = classify_inconsistency(ver.critique)
                        # ... in event data:
                        error_category=_inc.primary,
                        error_level=_inc.level,
```

Use the single-call version to avoid redundancy.

- [ ] **Step 3: Update __init__.py exports**

At `src/alethic/__init__.py`, add `InconsistencyResult` and `classify_inconsistency` to exports if appropriate. Check the current `__all__` list and add only if `classify_errors` is already exported.

- [ ] **Step 4: Run full test suite**

Run: `pytest --tb=short -q`

Expected: All 1310+ pass, 3 xfailed.

- [ ] **Step 5: Commit**

```bash
git add src/alethic/agent.py src/alethic/__init__.py
git commit -m "feat(taxonomy): add error_level to VERIFY events for calibration data collection

Uses classify_inconsistency() to populate both error_category and
error_level in VERIFY event emission. Collects hierarchical level
data (problem/approach/structural/mechanical/presentation) for
future trained classifier (Option C)."
```

---

### Task 5: Update test_error_taxonomy.py for wrong_method

**Files:**
- Modify: `tests/test_error_taxonomy.py`

- [ ] **Step 1: Check current test file**

Read `tests/test_error_taxonomy.py` and verify all existing tests still pass with the new module structure. The tests use local imports (`from alethic.error_taxonomy import classify_errors`), so they should work as-is since `classify_errors` still exists.

- [ ] **Step 2: Add wrong_method tests**

Add tests verifying:
- `classify_errors("The approach is inapplicable")` returns `"wrong_method"`
- `classify_errors_routed("wrong approach")` returns `("wrong_method", OracleType.LAYER3_LLM_ADVERSARIAL, True)`
- `get_revision_addendum("wrong_method")` returns non-empty string containing "approach" or "method"

- [ ] **Step 3: Run all taxonomy tests**

Run: `pytest tests/test_error_taxonomy.py tests/test_probe_taxonomy.py -v`

Expected: All pass.

- [ ] **Step 4: Run full suite**

Run: `pytest --tb=short -q`

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_error_taxonomy.py
git commit -m "test(taxonomy): add wrong_method tests to test_error_taxonomy"
```

---

### Task 6: Final verification and version note

**Files:**
- None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest --tb=short -q`

Expected: All pass (1310+ passed, 3 xfailed).

- [ ] **Step 2: Run ruff linting**

Run: `ruff check src/alethic/error_taxonomy.py tests/test_probe_taxonomy.py tests/test_error_taxonomy.py`

Expected: No errors.

- [ ] **Step 3: Verify backward compatibility**

Run:
```python
python -c "
from alethic.error_taxonomy import classify_errors, classify_errors_routed, get_revision_addendum
print(classify_errors('sign error'))          # algebra
print(classify_errors('false premise'))       # false_premise
print(classify_errors('wrong approach'))      # wrong_method
print(classify_errors('nothing matches'))     # general
cat, oracle, force = classify_errors_routed('sign error')
print(cat, oracle, force)                     # algebra LAYER2_CONSISTENCY False
print(bool(get_revision_addendum('wrong_method')))  # True
"
```

Expected: All print correct values.

- [ ] **Step 4: Verify new API**

Run:
```python
python -c "
from alethic.error_taxonomy import classify_inconsistency
r = classify_inconsistency('Sign error and false premise')
print(r.level, r.primary, r.all_matches)
# problem false_premise {'false_premise': 1, 'algebra': 1}
"
```

Expected: `problem false_premise {'false_premise': 1, 'algebra': 1}`
