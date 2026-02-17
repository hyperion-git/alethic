# Future Design Ideas

Ideas for future Alethic development. Each needs a proper design phase (brainstorming + expert panel) before implementation.

---

## 1. Sub-stepping routine for targeted issue resolution

**Origin:** Post-v2.0.0 discussion (2026-02-17)

**Idea:** After the Verifier returns, instead of sending the whole solution to the Reviser, a sub-loop extracts individual MINOR issues or low-confidence sections and runs focused mini-GVR cycles on just those parts.

**Current state:** Commit 3 (v2.0.0) added section-targeted revision — the Reviser receives low-confidence sections as guidance. This handles most cases in a single pass without a sub-loop.

**When to revisit:** If monitoring shows the Reviser consistently fails to fix MINOR issues in one pass despite section targeting.

**Prerequisite:** Would benefit significantly from structured solution format (numbered steps/sections with IDs) rather than free-form prose. Without structure, extracting and splicing subsections is fragile. This is essentially grafting Vibefeld's per-step granularity onto Alethic's whole-solution design.

**Complexity:** High. Multiplies API calls, requires section extraction/splicing logic, and shifts the architecture away from whole-solution verification.

**See also:** `docs/comparison.md` — Vibefeld/Alethfeld operate at per-step granularity with structured nodes; Alethic operates at whole-solution granularity.
