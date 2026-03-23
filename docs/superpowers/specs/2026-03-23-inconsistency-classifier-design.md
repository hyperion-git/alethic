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

```python
_TREE: list[tuple[str, list[str]]] = [
    ("problem",      ["false_premise", "counterexample"]),
    ("approach",     ["wrong_method"]),
    ("structural",   ["missing_case", "logic"]),
    ("mechanical",   ["algebra", "units"]),
    ("presentation", ["interpretation", "citation"]),
]
```

### Algorithm

```
classify_inconsistency(critique: str) -> InconsistencyResult:
    lower = critique.lower()
    all_matches = {}
    firing_level = None
    primary = None

    for level_name, categories in _TREE:
        for category in categories:
            hits = len(_KEYWORD_PATTERNS[category].findall(lower))
            if hits > 0:
                all_matches[category] = hits
                if firing_level is None:
                    firing_level = level_name
                    if primary is None or hits > all_matches.get(primary, 0):
                        primary = category

        # After processing all categories at this level:
        # If this level fired, primary is set. Continue scanning
        # remaining levels to populate all_matches, but don't
        # change firing_level or primary.

    if firing_level is None:
        return InconsistencyResult("unknown", "general", {})
    return InconsistencyResult(firing_level, primary, all_matches)
```

Within a level, the category with the most keyword hits wins. Ties are broken by position in the category list (first = higher severity within that level). The first level to fire determines `level` and `primary`. All levels are scanned to fill `all_matches`.

### Backward Compatibility

```python
def classify_errors(critique: str) -> str:
    """Backward-compatible wrapper. Returns primary category string."""
    return classify_inconsistency(critique).primary
```

All existing call sites (`agent.py`, `oracle_router.py`, `subagents.py`) continue to work unchanged.

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

- **wrong_method**: "wrong approach", "different method", "different approach", "this technique", "not suitable", "inapplicable", "should use", "consider using", "try instead", "does not apply here", "not the right"

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

### agent.py

- `classify_errors()` call sites: **no changes** (backward compatible wrapper).
- VERIFY event emission: add `error_level` field alongside existing `error_category`. This collects calibration data for future Option C (trained classifier).

### oracle_router.py

- `revision_budget()` and `_compute_dynamic_n()`: **no changes initially**. These switch on category strings, which still work.
- Future: these methods can use `level` for coarser decisions (e.g., problem/approach → skip revision, mechanical → eligible for adaptive budget).

### error_taxonomy.py

- `_TAXONOMY_KEYWORDS` dict replaced by `_KEYWORD_PATTERNS` dict (category -> compiled regex, same as before).
- New `_TREE` list defines the level structure.
- New `classify_inconsistency()` function (primary API).
- `classify_errors()` becomes a wrapper.
- `classify_errors_routed()` updated to use `classify_inconsistency()` internally.
- `REVISION_ADDENDA`: add `wrong_method` entry.
- `_ORACLE_ROUTING`: add `wrong_method` entry (LAYER3_LLM_ADVERSARIAL, True — same as false_premise, since wrong method needs a fresh approach).

## What's NOT Changing

- Prompt templates (generator, verifier, reviser) — no changes
- Skill orchestrator — no changes
- `get_revision_addendum()` — same interface, gains one new key
- `_ORACLE_ROUTING` — same interface, gains one new key

## Option C Hook

The `_TREE` list + per-level scoring function is the seam for a future trained classifier:

```python
# Current: keyword hit count
def _score_categories(categories: list[str], lower: str) -> dict[str, int]:
    return {cat: len(pattern.findall(lower)) for cat, pattern in ...}

# Future: trained model per level
def _score_categories(categories: list[str], lower: str, model=None) -> dict[str, float]:
    if model: return model.predict_proba(lower, categories)
    return {cat: len(pattern.findall(lower)) for cat, pattern in ...}  # fallback
```

Same tree traversal, same interface, different scoring. Calibration data format: `(critique_text, level, primary_category, revision_succeeded)` — collected from VERIFY events.

## Testing

- Update `test_probe_taxonomy.py::TestMultiCategoryPriority` for new tree-based resolution
- Add `TestInconsistencyResult` class testing:
  - Single-level match returns correct level + primary
  - Multi-level match returns first-firing level as primary, all matches populated
  - No-match returns `("unknown", "general", {})`
  - `wrong_method` keywords classified correctly at approach level
  - `classify_errors()` wrapper returns same as `result.primary`
  - `classify_errors_routed()` still works
- Existing roundtrip, completeness, and edge-case tests should pass with minimal changes
