# Hierarchical Inconsistency Classifier

**Date:** 2026-03-23
**Scope:** `src/alethic/error_taxonomy.py` refactor + consumer updates
**Motivation:** The flat keyword-first-match classifier conflates *where* the inconsistency lives with *what* category it is. A hierarchical tree separates these signals: the level tells the agent how much to change, the category tells the reviser where to look.

## Core Concept

The verifier finds **inconsistency** between the solution and reality. The taxonomy is a tree that categorizes where the inconsistency lives. The classifier traverses top-down; the first level that fires determines the scope of the problem.

## Inconsistency Tree

```
Level 0: PROBLEM — Is the problem statement itself wrong?
  ├── false_premise
  └── counterexample

Level 1: APPROACH — Is the solving strategy viable?
  └── wrong_method

Level 2: STRUCTURAL — Is the proof architecture broken?
  ├── missing_case
  └── logic

Level 3: MECHANICAL — Are the computations correct?
  ├── algebra
  └── units

Level 4: PRESENTATION — Is the framing/attribution adequate?
  ├── interpretation
  └── citation
```

Levels are a scaffold, not gospel. They can flatten, split, or be replaced by a trained classifier as data accumulates.

### Within-Level Severity

Categories within each level are ordered by severity (first = higher):
- Level 0: false_premise > counterexample
- Level 1: only wrong_method
- Level 2: missing_case > logic
- Level 3: algebra > units
- Level 4: interpretation > citation

This ordering is used as a tiebreaker when multiple categories at the same level have equal hit counts.

## Data Model

```python
@dataclass(frozen=True)
class InconsistencyResult:
    level: str              # "problem" | "approach" | "structural" | "mechanical" | "presentation" | "unknown"
    primary: str            # best category at the firing level, or "general"
    all_matches: dict[str, int]  # category -> hit count across ALL levels
```

## Classification Logic

### Tree Definition

`_TREE` replaces the current `_TAXONOMY_KEYWORDS` dict as the authoritative category structure. Keywords move to a separate `_KEYWORDS` dict keyed by category; `_KEYWORD_PATTERNS` (compiled regexes per category) is built from `_KEYWORDS`.

```python
_TREE: list[tuple[str, list[str]]] = [
    ("problem",      ["false_premise", "counterexample"]),
    ("approach",     ["wrong_method"]),
    ("structural",   ["missing_case", "logic"]),
    ("mechanical",   ["algebra", "units"]),
    ("presentation", ["interpretation", "citation"]),
]

_KEYWORDS: dict[str, list[str]] = {
    "false_premise": [...],
    "counterexample": [...],
    "wrong_method": [...],
    # ... all 9 categories
}

_KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {
    cat: re.compile("|".join(re.escape(kw) for kw in kws))
    for cat, kws in _KEYWORDS.items()
}
```

### Scoring Function (Option C Hook)

Scoring is extracted into a pluggable function, swappable at module level:

```python
def _score_keyword(categories: list[str], lower: str) -> dict[str, int]:
    """Score categories by keyword hit count. Returns {category: hits}."""
    scores = {}
    for cat in categories:
        hits = len(_KEYWORD_PATTERNS[cat].findall(lower))
        if hits > 0:
            scores[cat] = hits
    return scores

_score_fn = _score_keyword  # Swappable for Option C
```

### Algorithm

```python
def classify_inconsistency(critique: str) -> InconsistencyResult:
    lower = critique.lower()
    all_matches: dict[str, int] = {}
    firing_level: str | None = None
    primary: str | None = None

    for level_name, categories in _TREE:
        level_scores = _score_fn(categories, lower)

        # Record all hits for all_matches (even if this level doesn't fire)
        all_matches.update(level_scores)

        # First level with any hits determines firing_level and primary
        if firing_level is None and level_scores:
            firing_level = level_name
            # Within this level: highest hits wins, ties broken by list position
            primary = max(
                level_scores,
                key=lambda c: (level_scores[c], -categories.index(c)),
            )

    if firing_level is None:
        return InconsistencyResult("unknown", "general", {})
    return InconsistencyResult(firing_level, primary, all_matches)
```

Key property: `primary` is selected **only from categories at the firing level**, never across levels. The `categories.index(c)` tiebreaker uses within-level ordering (first = higher severity, so negative index = prefer earlier).

### Backward Compatibility

```python
def classify_errors(critique: str) -> str:
    """Backward-compatible wrapper. Returns primary category string."""
    return classify_inconsistency(critique).primary
```

All existing call sites that use `classify_errors()` continue to work unchanged.

## Worked Examples

### Example 1: Single-Level Match
```
Input:  "There is a missing case when n=0"
Scan:   Level 0 (problem): no hits
        Level 1 (approach): no hits
        Level 2 (structural): missing_case=1
        → fires here, primary="missing_case"
        Level 3-4: scanned for all_matches
Output: level="structural", primary="missing_case", all_matches={"missing_case": 1}
```

### Example 2: Multi-Level Match (first level wins)
```
Input:  "Sign error and the approach is not suitable"
Scan:   Level 0 (problem): no hits
        Level 1 (approach): wrong_method=1
        → fires here, primary="wrong_method"
        Level 3 (mechanical): algebra=1 (but level already fired)
Output: level="approach", primary="wrong_method", all_matches={"wrong_method": 1, "algebra": 1}
```

### Example 3: Within-Level Tiebreak
```
Input:  "Missing edge case and the logic does not follow"
Scan:   Level 2 (structural): missing_case=1, logic=1
        → fires here, both have 1 hit, tie broken by position: missing_case wins
Output: level="structural", primary="missing_case", all_matches={"missing_case": 1, "logic": 1}
```

### Example 4: No Match
```
Input:  "The proof is beautiful but wrong"
Scan:   All levels: no hits
Output: level="unknown", primary="general", all_matches={}
```

## Keywords

### Existing Categories (unchanged from severity-reorder commit)

- **false_premise**: "false premise", "false claim", "claim is false", "statement is false", "does not hold", "no valid solution", "no solution exists", "unsolvable", "cannot be proved", "impossible to prove", "contradicts known", "violates known"
- **counterexample**: "counterexample", "flaw found", "breaker found", "regime failure", "falsif"
- **missing_case**: "missing case", "edge case", "special case", "boundary case", "boundary condition", "not handled", "case analysis", "degenerate", "not considered", "overlooked"
- **logic**: "does not follow", "non sequitur", "circular", "circular argument", "implication", "gap in", "logical gap", "invalid inference", "unjustified", "without justification", "not proven", "assumption not established"
- **algebra**: "sign error", "wrong sign", "arithmetic", "calculation error", "simplif", "expand", "factor", "distribut", "algebraic error", "incorrect step", "wrong value", "computation error"
- **units**: "dimension", "dimensional", "si unit", "inconsistent units", "dimensionless", "dimensional mismatch"
- **interpretation**: "misinterpret", "misread", "wrong problem", "reinterpret", "different question", "weaker problem", "specification gaming"
- **citation**: "citation", "cite", "well known", "standard result", "it can be shown", "it is known", "no source", "no reference", "no proof given", "vague appeal", "theorem name", "by a known", "appeal to"

### New Category

- **wrong_method**: "wrong approach", "different method", "different approach", "not suitable", "inapplicable", "should use", "consider using", "try instead", "does not apply here", "not the right"

Note: "this technique" was removed (too broad — matches "The proof technique is elegant" falsely).

### New Revision Addendum

```
## Revision focus: change of approach
The current method appears fundamentally unsuitable for this problem.
Do NOT revise within the current approach — choose a categorically
different method. Consider what mathematical/physical structure the
problem has (symmetry, recursion, conservation law, etc.) and pick
a technique that exploits that structure directly.
```

## Consumer Changes

### error_taxonomy.py

- `_TAXONOMY_KEYWORDS` dict replaced by `_KEYWORDS` dict (category -> keyword list) + `_TREE` list (level structure).
- `_TAXONOMY_PATTERNS` replaced by `_KEYWORD_PATTERNS` dict (category -> compiled regex).
- New `_score_keyword()` function (extracted for Option C pluggability).
- New `classify_inconsistency()` function (primary API, returns `InconsistencyResult`).
- `classify_errors()` becomes a one-line wrapper returning `result.primary`.
- `classify_errors_routed()` updated to use `classify_inconsistency()` internally.
- `REVISION_ADDENDA`: add `wrong_method` entry.
- `_ORACLE_ROUTING`: add `wrong_method` entry (`LAYER3_LLM_ADVERSARIAL, True`).
- New public helper: `get_all_categories() -> set[str]` (extracts all categories from `_TREE`, used by tests).

### agent.py

- `classify_errors()` call sites: **no changes** (backward compatible wrapper).
- VERIFY event emission: one call site changes from `classify_errors()` to `classify_inconsistency()` to capture both `error_category` and `error_level`:
  ```python
  result = classify_inconsistency(ver.critique)
  # In VERIFY event data:
  error_category=result.primary,
  error_level=result.level,
  ```

### oracle_router.py

- **No changes.** `revision_budget()` and `_compute_dynamic_n()` continue to switch on `error_category` strings from `EvidenceState`.
- `EvidenceState` is unchanged — `error_category` continues to store the primary category string.
- Future: these methods can optionally use `level` for coarser decisions.

## What's NOT Changing

- Prompt templates (generator, verifier, reviser) — no changes
- Skill orchestrator — no changes
- `get_revision_addendum()` — same interface, gains one new key
- `_ORACLE_ROUTING` — same interface, gains one new key
- `EvidenceState` — no new fields

## Testing

### Tests That Change

- `test_probe_taxonomy.py::TestMultiCategoryPriority::test_priority_order_is_deterministic`: Update to check `_TREE`-derived order instead of flat dict key order.
- `test_probe_taxonomy.py::TestRoutingTableCompleteness`: Change `_TAXONOMY_KEYWORDS` imports to use `get_all_categories()`.
- `test_probe_taxonomy.py::TestKeywordCoverage`: Change `_TAXONOMY_KEYWORDS` imports to use `_KEYWORDS`.
- Priority tests: update to reflect tree-based resolution (first-firing-level, not global order).

### New Tests (TestInconsistencyResult class)

```python
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

def test_within_level_highest_hits_wins(self):
    result = classify_inconsistency("sign error, wrong sign, arithmetic — also a logical gap")
    assert result.level == "structural"  # logic fires at level 2
    # Wait — algebra is level 3, logic is level 2. Logic fires first.
    assert result.primary == "logic"
    assert result.all_matches["algebra"] == 3  # more hits, but lower level

def test_within_level_tiebreak_by_position(self):
    result = classify_inconsistency("missing case and does not follow")
    assert result.level == "structural"
    assert result.primary == "missing_case"  # first in level, tiebreaker

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

def test_classify_errors_routed_still_works(self):
    cat, oracle, force = classify_errors_routed("sign error")
    assert cat == "algebra"

def test_empty_critique(self):
    result = classify_inconsistency("")
    assert result.level == "unknown"
    assert result.primary == "general"
```
