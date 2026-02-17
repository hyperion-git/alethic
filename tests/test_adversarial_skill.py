"""Adversarial tests validating /alethic-derive skill files against /alethic-solve skill files.

Checks structural consistency between the two skill variants: YAML frontmatter,
step structure, prompt templates, physics-specific content, preset tables,
reference files, and document structure differences.
"""

from __future__ import annotations

import os
import re

import pytest

# ── Paths ──────────────────────────────────────────────────────────────

BASE = os.path.join(os.path.dirname(__file__), os.pardir, "skills")
SOLVE_SKILL = os.path.join(BASE, "alethic-solve", "SKILL.md")
DERIVE_SKILL = os.path.join(BASE, "alethic-derive", "SKILL.md")
SOLVE_REFS = os.path.join(BASE, "alethic-solve", "references")
DERIVE_REFS = os.path.join(BASE, "alethic-derive", "references")

REF_FILES = ["generator.md", "verifier.md", "reviser.md", "beautifier.md"]


# ── Helpers ────────────────────────────────────────────────────────────


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from a Markdown file (simple parser, no PyYAML)."""
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    assert match, "No YAML frontmatter found"
    raw = match.group(1)
    result: dict = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for line in raw.split("\n"):
        # List item under a key
        list_match = re.match(r"^\s+-\s+(.*)", line)
        if list_match and current_key is not None and current_list is not None:
            val = list_match.group(1).strip().strip('"').strip("'")
            current_list.append(val)
            result[current_key] = current_list
            continue
        # Key-value pair
        kv_match = re.match(r"^(\S[^:]*?):\s*(.*)", line)
        if kv_match:
            key = kv_match.group(1).strip()
            val = kv_match.group(2).strip().strip('"').strip("'")
            if val:
                result[key] = val
                current_key = None
                current_list = None
            else:
                # Value on subsequent lines (list)
                current_key = key
                current_list = []
    return result


def _extract_tag(text: str, tag: str) -> str:
    """Extract the content between <tag> and </tag>."""
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, text, re.DOTALL)
    assert match, f"Tag <{tag}> not found"
    return match.group(1)


def _extract_preset_table(text: str) -> list[dict]:
    """Parse the preset table from a SKILL.md file.

    Returns a list of dicts with keys: preset, iters, revs, threshold, budget.
    """
    # Find the table under "### Presets" — look for Markdown table rows with pipe chars.
    lines = text.split("\n")
    rows = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("| Preset"):
            in_table = True
            continue
        if in_table:
            if stripped.startswith("|---"):
                continue
            if stripped.startswith("|") and "`" in stripped:
                cells = [c.strip().strip("`") for c in stripped.split("|")[1:-1]]
                rows.append({
                    "preset": cells[0],
                    "iters": cells[1],
                    "revs": cells[2],
                    "threshold": cells[3],
                    "budget": cells[4],
                })
            else:
                break
    return rows


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def solve_skill() -> str:
    return _read(SOLVE_SKILL)


@pytest.fixture(scope="module")
def derive_skill() -> str:
    return _read(DERIVE_SKILL)


@pytest.fixture(scope="module")
def solve_frontmatter(solve_skill: str) -> dict:
    return _parse_frontmatter(solve_skill)


@pytest.fixture(scope="module")
def derive_frontmatter(derive_skill: str) -> dict:
    return _parse_frontmatter(derive_skill)


# ── 1. SKILL.md frontmatter ───────────────────────────────────────────


class TestFrontmatter:
    """Verify alethic-derive/SKILL.md has valid YAML frontmatter matching alethic-solve's tools."""

    def test_derive_name(self, derive_frontmatter: dict):
        assert derive_frontmatter["name"] == "alethic-derive"

    def test_derive_has_description(self, derive_frontmatter: dict):
        assert "description" in derive_frontmatter
        assert len(derive_frontmatter["description"]) > 0

    def test_derive_has_argument_hint(self, derive_frontmatter: dict):
        assert "argument-hint" in derive_frontmatter
        assert len(derive_frontmatter["argument-hint"]) > 0

    def test_derive_allowed_tools_match_solve(
        self, solve_frontmatter: dict, derive_frontmatter: dict
    ):
        assert sorted(derive_frontmatter["allowed-tools"]) == sorted(
            solve_frontmatter["allowed-tools"]
        ), (
            f"Derive tools {derive_frontmatter['allowed-tools']} "
            f"do not match solve tools {solve_frontmatter['allowed-tools']}"
        )


# ── 2. All 5 steps present ───────────────────────────────────────────


class TestStepStructure:
    """Both SKILL.md files should have Steps 1-5."""

    STEPS = [
        ("Step 1", "Setup"),
        ("Step 2", "Main Loop"),
        ("Step 3", "Failure Admission"),
        ("Step 4", "Format Output"),
        ("Step 5", "Present Results"),
    ]

    @pytest.mark.parametrize("step_num,step_name", STEPS)
    def test_solve_has_step(self, solve_skill: str, step_num: str, step_name: str):
        pattern = rf"##\s+{step_num}.*{step_name}"
        assert re.search(pattern, solve_skill, re.IGNORECASE), (
            f"solve/SKILL.md missing '{step_num}: {step_name}'"
        )

    @pytest.mark.parametrize("step_num,step_name", STEPS)
    def test_derive_has_step(self, derive_skill: str, step_num: str, step_name: str):
        pattern = rf"##\s+{step_num}.*{step_name}"
        assert re.search(pattern, derive_skill, re.IGNORECASE), (
            f"derive/SKILL.md missing '{step_num}: {step_name}'"
        )


# ── 3. All 4 prompt templates present ────────────────────────────────


class TestPromptTemplates:
    """alethic-derive/SKILL.md should have all 4 prompt template tags."""

    TAGS = [
        "generator_prompt",
        "verifier_prompt",
        "reviser_prompt",
        "beautifier_prompt",
    ]

    @pytest.mark.parametrize("tag", TAGS)
    def test_derive_has_prompt_tag(self, derive_skill: str, tag: str):
        assert f"<{tag}>" in derive_skill, f"derive/SKILL.md missing <{tag}>"
        assert f"</{tag}>" in derive_skill, f"derive/SKILL.md missing </{tag}>"


# ── 4. Prompt templates are physics-specific ─────────────────────────


class TestPhysicsSpecificity:
    """Derive prompts should use physics-specific language."""

    def test_generator_mentions_physics_or_derivation(self, derive_skill: str):
        gen = _extract_tag(derive_skill, "generator_prompt")
        gen_lower = gen.lower()
        assert "physics" in gen_lower or "derivation" in gen_lower, (
            "derive generator prompt should mention 'physics' or 'derivation'"
        )

    def test_verifier_mentions_physics_derivation_verifier(self, derive_skill: str):
        ver = _extract_tag(derive_skill, "verifier_prompt")
        ver_lower = ver.lower()
        assert "physics derivation verifier" in ver_lower, (
            "derive verifier prompt should mention 'physics derivation verifier'"
        )

    def test_reviser_mentions_derivation_approach_or_physics(self, derive_skill: str):
        rev = _extract_tag(derive_skill, "reviser_prompt")
        rev_lower = rev.lower()
        assert "derivation" in rev_lower or "physics" in rev_lower, (
            "derive reviser prompt should mention 'derivation approach' or 'physics'"
        )


# ── 5. Beautifier has physics LaTeX symbols ──────────────────────────


class TestBeautifierPhysicsSymbols:
    """alethic-derive's beautifier should include physics-specific LaTeX symbols."""

    PHYSICS_SYMBOLS = [
        r"\hbar",
        r"\nabla",
        r"\partial",
        r"\langle",
        r"\rangle",
        r"\mathcal{H}",
        r"\mathcal{L}",
        r"\dagger",
        r"\mathrm{d}",
    ]

    @pytest.mark.parametrize("symbol", PHYSICS_SYMBOLS)
    def test_derive_beautifier_has_symbol(self, derive_skill: str, symbol: str):
        beautifier = _extract_tag(derive_skill, "beautifier_prompt")
        assert symbol in beautifier, (
            f"derive beautifier prompt missing physics symbol: {symbol}"
        )

    @pytest.mark.parametrize("symbol", PHYSICS_SYMBOLS)
    def test_solve_beautifier_lacks_symbol(self, solve_skill: str, symbol: str):
        beautifier = _extract_tag(solve_skill, "beautifier_prompt")
        assert symbol not in beautifier, (
            f"solve beautifier prompt should NOT contain physics symbol: {symbol}"
        )


# ── 6. Solve SKILL.md unchanged ──────────────────────────────────────


class TestSolveUnchanged:
    """Verify alethic-solve/SKILL.md still says 'mathematical' (not physics)."""

    def test_solve_says_mathematical(self, solve_skill: str):
        assert "mathematical" in solve_skill.lower(), (
            "solve/SKILL.md should contain 'mathematical'"
        )

    def test_solve_generator_not_physics(self, solve_skill: str):
        gen = _extract_tag(solve_skill, "generator_prompt")
        first_sentence = gen.strip().split("\n")[0].lower()
        assert "physics" not in first_sentence, (
            "solve generator opening should NOT mention 'physics'"
        )


# ── 7. Reference files exist ─────────────────────────────────────────


class TestReferenceFilesExist:
    """All 4 reference files exist in skills/alethic-derive/references/."""

    @pytest.mark.parametrize("ref_file", REF_FILES)
    def test_derive_reference_exists(self, ref_file: str):
        path = os.path.join(DERIVE_REFS, ref_file)
        assert os.path.isfile(path), f"Missing derive reference file: {path}"


# ── 8. Reference files have authority note ───────────────────────────


class TestReferenceAuthorityNote:
    """Each derive reference file should point to skills/alethic-derive/SKILL.md."""

    @pytest.mark.parametrize("ref_file", REF_FILES)
    def test_derive_reference_has_authority_note(self, ref_file: str):
        path = os.path.join(DERIVE_REFS, ref_file)
        content = _read(path)
        # The note should appear near the top and reference alethic-derive/SKILL.md
        # Look in the first 300 characters for the authority note
        header = content[:400].lower()
        assert "skills/alethic-derive/skill.md" in header, (
            f"{ref_file} should reference skills/alethic-derive/SKILL.md as authoritative "
            f"in its opening, but header is: {content[:200]!r}"
        )


# ── 9. Verifier has physics-specific error checklist ─────────────────


class TestVerifierPhysicsErrors:
    """alethic-derive verifier should mention physics-specific error categories."""

    PHYSICS_ERRORS = [
        "dimensional inconsistency",
        "conservation law",
        "sign convention",
        "unjustified approximation",
        "boundary condition error",
    ]

    @pytest.mark.parametrize("error_type", PHYSICS_ERRORS)
    def test_derive_verifier_has_physics_error(self, derive_skill: str, error_type: str):
        verifier = _extract_tag(derive_skill, "verifier_prompt")
        verifier_lower = verifier.lower()
        assert error_type in verifier_lower, (
            f"derive verifier prompt missing physics error type: {error_type}"
        )


# ── 10. Preset table identical ───────────────────────────────────────


class TestPresetTableIdentical:
    """Both SKILL.md files should have the same preset values."""

    def test_preset_tables_match(self, solve_skill: str, derive_skill: str):
        solve_presets = _extract_preset_table(solve_skill)
        derive_presets = _extract_preset_table(derive_skill)

        assert len(solve_presets) > 0, "No preset rows found in solve/SKILL.md"
        assert len(derive_presets) > 0, "No preset rows found in derive/SKILL.md"
        assert len(solve_presets) == len(derive_presets), (
            f"Preset table row count differs: solve={len(solve_presets)}, "
            f"derive={len(derive_presets)}"
        )

        for s, d in zip(solve_presets, derive_presets, strict=True):
            assert s == d, (
                f"Preset mismatch: solve={s} vs derive={d}"
            )

    @pytest.mark.parametrize(
        "preset,iters,revs,threshold,budget",
        [
            ("quick", "2", "1", "0.85", "20"),
            ("default", "5", "3", "0.90", "50"),
            ("thorough", "8", "5", "0.95", "80"),
            ("extreme", "12", "5", "0.97", "120"),
        ],
    )
    def test_derive_preset_values(
        self, derive_skill: str, preset: str, iters: str, revs: str, threshold: str, budget: str
    ):
        presets = _extract_preset_table(derive_skill)
        row = next((p for p in presets if p["preset"] == preset), None)
        assert row is not None, f"Preset '{preset}' not found in derive/SKILL.md"
        assert row["iters"] == iters, f"{preset}: iters {row['iters']} != {iters}"
        assert row["revs"] == revs, f"{preset}: revs {row['revs']} != {revs}"
        assert row["threshold"] == threshold, (
            f"{preset}: threshold {row['threshold']} != {threshold}"
        )
        assert row["budget"] == budget, f"{preset}: budget {row['budget']} != {budget}"


# ── 11. Derive beautifier has physics document structure ─────────────


class TestDeriveBeautifierStructure:
    """alethic-derive beautifier should mention physics-specific document structure."""

    STRUCTURE_ELEMENTS = [
        "Setup",
        "Derivation",
        "Result",
        "Limiting cases",
    ]

    @pytest.mark.parametrize("element", STRUCTURE_ELEMENTS)
    def test_derive_beautifier_has_structure_element(self, derive_skill: str, element: str):
        beautifier = _extract_tag(derive_skill, "beautifier_prompt")
        assert element in beautifier, (
            f"derive beautifier missing document structure element: {element}"
        )

    def test_derive_beautifier_mentions_physical_system(self, derive_skill: str):
        beautifier = _extract_tag(derive_skill, "beautifier_prompt")
        assert "Physical system" in beautifier, (
            "derive beautifier should mention 'Physical system' in Setup"
        )

    def test_derive_beautifier_mentions_assumptions(self, derive_skill: str):
        beautifier = _extract_tag(derive_skill, "beautifier_prompt")
        # Should mention assumptions and approximations in Setup
        assert "assumptions" in beautifier.lower(), (
            "derive beautifier should mention 'assumptions' in Setup"
        )

    def test_derive_beautifier_mentions_approximations(self, derive_skill: str):
        beautifier = _extract_tag(derive_skill, "beautifier_prompt")
        assert "approximations" in beautifier.lower(), (
            "derive beautifier should mention 'approximations' in Setup"
        )


# ── 12. Solve beautifier has math document structure ─────────────────


class TestSolveBeautifierStructure:
    """alethic-solve beautifier should mention math-specific document structure."""

    def test_solve_beautifier_has_proof_strategy(self, solve_skill: str):
        beautifier = _extract_tag(solve_skill, "beautifier_prompt")
        assert "Proof strategy" in beautifier, (
            "solve beautifier should mention 'Proof strategy'"
        )

    def test_solve_beautifier_has_body(self, solve_skill: str):
        beautifier = _extract_tag(solve_skill, "beautifier_prompt")
        assert "Body" in beautifier, (
            "solve beautifier should mention 'Body'"
        )

    def test_solve_beautifier_has_conclusion_with_blacksquare(self, solve_skill: str):
        beautifier = _extract_tag(solve_skill, "beautifier_prompt")
        assert "blacksquare" in beautifier, (
            "solve beautifier should mention blacksquare in Conclusion"
        )
