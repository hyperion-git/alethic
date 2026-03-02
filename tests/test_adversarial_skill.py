"""Adversarial tests validating /alethic-derive skill files against /alethic-solve skill files.

Checks structural consistency between the two skill variants: YAML frontmatter,
domain configuration, shared orchestrator structure, reference file content,
physics-specific prompts, preset tables, and document structure.

Architecture: Both skills use thin SKILL.md configurators that load a shared
orchestrator (skills/alethic-common/orchestrator.md) and domain-specific
reference files (skills/alethic-{solve,derive}/references/*.md).
"""

from __future__ import annotations

import os
import re

import pytest

# ── Paths ──────────────────────────────────────────────────────────────

BASE = os.path.join(os.path.dirname(__file__), os.pardir, "skills")
SOLVE_SKILL = os.path.join(BASE, "alethic-solve", "SKILL.md")
DERIVE_SKILL = os.path.join(BASE, "alethic-derive", "SKILL.md")
ORCHESTRATOR = os.path.join(BASE, "alethic-common", "orchestrator.md")
SOLVE_REFS = os.path.join(BASE, "alethic-solve", "references")
DERIVE_REFS = os.path.join(BASE, "alethic-derive", "references")

REF_FILES = ["generator.md", "verifier.md", "reviser.md", "beautifier.md"]
TEXTBOOK_REF_FILES = ["textbook_planner.md", "textbook_writer.md", "fidelity_verifier.md"]
ALL_REF_FILES = REF_FILES + TEXTBOOK_REF_FILES


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


def _extract_domain_config(text: str) -> dict:
    """Extract the Domain Configuration table from a thin SKILL.md."""
    result = {}
    lines = text.split("\n")
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("| Key"):
            in_table = True
            continue
        if in_table:
            if stripped.startswith("|---"):
                continue
            if stripped.startswith("|") and "|" in stripped[1:]:
                cells = [c.strip() for c in stripped.split("|")[1:-1]]
                if len(cells) >= 2:
                    result[cells[0]] = cells[1]
            else:
                break
    return result


def _extract_preset_table(text: str) -> list[dict]:
    """Parse the preset table from an orchestrator or SKILL.md file.

    Returns a list of dicts with keys: preset, iters, revs, threshold, budget.
    """
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
                rows.append(
                    {
                        "preset": cells[0],
                        "iters": cells[1],
                        "revs": cells[2],
                        "threshold": cells[3],
                        "budget": cells[4],
                    }
                )
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
def orchestrator() -> str:
    return _read(ORCHESTRATOR)


@pytest.fixture(scope="module")
def solve_frontmatter(solve_skill: str) -> dict:
    return _parse_frontmatter(solve_skill)


@pytest.fixture(scope="module")
def derive_frontmatter(derive_skill: str) -> dict:
    return _parse_frontmatter(derive_skill)


@pytest.fixture(scope="module")
def solve_config(solve_skill: str) -> dict:
    return _extract_domain_config(solve_skill)


@pytest.fixture(scope="module")
def derive_config(derive_skill: str) -> dict:
    return _extract_domain_config(derive_skill)


# ── 1. SKILL.md frontmatter ───────────────────────────────────────────


class TestFrontmatter:
    """Verify both SKILL.md files have valid YAML frontmatter with matching tools."""

    def test_derive_name(self, derive_frontmatter: dict):
        assert derive_frontmatter["name"] == "alethic-derive"

    def test_solve_name(self, solve_frontmatter: dict):
        assert solve_frontmatter["name"] == "alethic-solve"

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


# ── 2. Domain configuration ──────────────────────────────────────────


class TestDomainConfiguration:
    """Both thin SKILL.md files should define the same set of domain variables."""

    REQUIRED_KEYS = ["domain", "command", "noun", "verb", "agent_title", "session_skill"]

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_solve_has_key(self, solve_config: dict, key: str):
        assert key in solve_config, f"solve/SKILL.md missing domain key: {key}"

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_derive_has_key(self, derive_config: dict, key: str):
        assert key in derive_config, f"derive/SKILL.md missing domain key: {key}"

    def test_solve_domain_values(self, solve_config: dict):
        assert solve_config["domain"] == "math"
        assert solve_config["command"] == "solve"
        assert solve_config["noun"] == "solution"
        assert solve_config["verb"] == "solve"

    def test_derive_domain_values(self, derive_config: dict):
        assert derive_config["domain"] == "physics"
        assert derive_config["command"] == "derive"
        assert derive_config["noun"] == "derivation"
        assert derive_config["verb"] == "derive"

    def test_configs_have_same_keys(self, solve_config: dict, derive_config: dict):
        assert set(solve_config.keys()) == set(derive_config.keys()), (
            f"Domain config keys differ: solve={set(solve_config.keys())} "
            f"vs derive={set(derive_config.keys())}"
        )


# ── 3. Shared orchestrator structure ─────────────────────────────────


class TestOrchestratorStructure:
    """The shared orchestrator should have all required steps and sections."""

    def test_orchestrator_exists(self):
        assert os.path.isfile(ORCHESTRATOR), "Missing orchestrator.md"

    STEPS = [
        ("Step 1", "Setup"),
        ("Step 2", "Main Loop"),
        ("Step 3", "Failure Admission"),
        ("Step 4", "Format Output"),
        ("Step 5", "Present Results"),
        ("Step 6", "Session Finalization"),
    ]

    @pytest.mark.parametrize("step_num,step_name", STEPS)
    def test_has_step(self, orchestrator: str, step_num: str, step_name: str):
        pattern = rf"##\s+{step_num}.*{step_name}"
        assert re.search(pattern, orchestrator, re.IGNORECASE), (
            f"orchestrator.md missing '{step_num}: {step_name}'"
        )

    def test_has_domain_variables_section(self, orchestrator: str):
        assert "## Domain Variables" in orchestrator

    def test_has_argument_parsing(self, orchestrator: str):
        assert "## Argument Parsing" in orchestrator

    def test_uses_noun_placeholder(self, orchestrator: str):
        assert "{noun}" in orchestrator, "orchestrator should use {noun} placeholder"

    def test_uses_domain_placeholder(self, orchestrator: str):
        assert "{domain}" in orchestrator, "orchestrator should use {domain} placeholder"

    def test_uses_references_dir_placeholder(self, orchestrator: str):
        assert "{references_dir}" in orchestrator, (
            "orchestrator should use {references_dir} placeholder"
        )

    def test_no_hardcoded_math_in_instructions(self, orchestrator: str):
        """Orchestrator should not hardcode 'mathematical' in instructions (only in docs table)."""
        # Remove the domain variables table (first ~20 lines) to check only instructions
        lines = orchestrator.split("\n")
        # Allow "mathematical" only in the balanced addendum reference or examples
        occurrences = [
            i
            for i, line in enumerate(lines[20:], start=21)
            if "mathematical" in line.lower()
            and "balanced" not in line.lower()
            and "example" not in line.lower()
            and not line.strip().startswith("|")
        ]
        assert not occurrences, (
            f"orchestrator.md has hardcoded 'mathematical' at lines: {occurrences}"
        )


# ── 4. Orchestrator is domain-neutral ─────────────────────────────────


class TestOrchestratorDomainNeutral:
    """The orchestrator should use placeholders, not hardcoded domain terms."""

    def test_no_hardcoded_proof(self, orchestrator: str):
        """No 'proof' outside documentation/examples."""
        lines = orchestrator.split("\n")
        bad_lines = [
            i
            for i, line in enumerate(lines, start=1)
            if re.search(r"\bproof\b", line, re.IGNORECASE)
            and not line.strip().startswith("|")
            and not line.strip().startswith("-")
            and "example" not in line.lower()
            and "e.g." not in line.lower()
        ]
        assert not bad_lines, f"orchestrator.md has hardcoded 'proof' at lines: {bad_lines}"

    def test_no_hardcoded_derivation_in_instructions(self, orchestrator: str):
        """No 'derivation' outside documentation table."""
        lines = orchestrator.split("\n")
        bad_lines = [
            i
            for i, line in enumerate(lines, start=1)
            if re.search(r"\bderivation\b", line, re.IGNORECASE)
            and not line.strip().startswith("|")
            and "example" not in line.lower()
            and "e.g." not in line.lower()
        ]
        assert not bad_lines, f"orchestrator.md has hardcoded 'derivation' at lines: {bad_lines}"


# ── 5. Both SKILL.md files load the orchestrator ─────────────────────


class TestOrchestratorLoading:
    """Both thin SKILL.md files should have orchestrator loading instructions."""

    def test_solve_loads_orchestrator(self, solve_skill: str):
        assert "orchestrator.md" in solve_skill, "solve/SKILL.md should reference orchestrator.md"

    def test_derive_loads_orchestrator(self, derive_skill: str):
        assert "orchestrator.md" in derive_skill, "derive/SKILL.md should reference orchestrator.md"

    def test_solve_has_find_command(self, solve_skill: str):
        assert "find" in solve_skill, "solve/SKILL.md should use find for path resolution"

    def test_derive_has_find_command(self, derive_skill: str):
        assert "find" in derive_skill, "derive/SKILL.md should use find for path resolution"

    def test_solve_derives_ref_dir(self, solve_skill: str):
        assert "alethic-solve/references" in solve_skill

    def test_derive_derives_ref_dir(self, derive_skill: str):
        assert "alethic-derive/references" in derive_skill


# ── 6. Reference files exist ──────────────────────────────────────────


class TestReferenceFilesExist:
    """All reference files exist in both skills."""

    @pytest.mark.parametrize("ref_file", ALL_REF_FILES)
    def test_solve_reference_exists(self, ref_file: str):
        path = os.path.join(SOLVE_REFS, ref_file)
        assert os.path.isfile(path), f"Missing solve reference file: {path}"

    @pytest.mark.parametrize("ref_file", ALL_REF_FILES)
    def test_derive_reference_exists(self, ref_file: str):
        path = os.path.join(DERIVE_REFS, ref_file)
        assert os.path.isfile(path), f"Missing derive reference file: {path}"


# ── 7. Reference files have authoritative header ─────────────────────


class TestReferenceAuthorityNote:
    """Each reference file should reference the shared orchestrator."""

    @pytest.mark.parametrize("ref_file", ALL_REF_FILES)
    def test_solve_reference_has_authority_note(self, ref_file: str):
        path = os.path.join(SOLVE_REFS, ref_file)
        content = _read(path)
        header = content[:400].lower()
        assert "orchestrator" in header, (
            f"solve/{ref_file} should reference orchestrator in its opening, "
            f"but header is: {content[:200]!r}"
        )

    @pytest.mark.parametrize("ref_file", ALL_REF_FILES)
    def test_derive_reference_has_authority_note(self, ref_file: str):
        path = os.path.join(DERIVE_REFS, ref_file)
        content = _read(path)
        header = content[:400].lower()
        assert "orchestrator" in header, (
            f"derive/{ref_file} should reference orchestrator in its opening, "
            f"but header is: {content[:200]!r}"
        )


# ── 8. Physics-specific generator content ─────────────────────────────


class TestPhysicsSpecificity:
    """Derive reference prompts should use physics-specific language."""

    def test_generator_mentions_physics_or_derivation(self):
        gen = _read(os.path.join(DERIVE_REFS, "generator.md")).lower()
        assert "physics" in gen or "derivation" in gen, (
            "derive generator prompt should mention 'physics' or 'derivation'"
        )

    def test_verifier_mentions_physics(self):
        ver = _read(os.path.join(DERIVE_REFS, "verifier.md")).lower()
        assert "physics" in ver, "derive verifier prompt should mention 'physics'"

    def test_reviser_mentions_derivation_or_physics(self):
        rev = _read(os.path.join(DERIVE_REFS, "reviser.md")).lower()
        assert "derivation" in rev or "physics" in rev, (
            "derive reviser prompt should mention 'derivation' or 'physics'"
        )


# ── 9. Solve prompts say mathematical ─────────────────────────────────


class TestSolveMathLanguage:
    """Solve reference prompts should use math-specific language."""

    def test_solve_skill_says_mathematical(self, solve_skill: str):
        assert "mathematical" in solve_skill.lower(), "solve/SKILL.md should contain 'mathematical'"

    def test_solve_generator_not_physics(self):
        gen = _read(os.path.join(SOLVE_REFS, "generator.md"))
        first_sentence = gen.strip().split("\n")[0].lower()
        assert "physics" not in first_sentence, (
            "solve generator opening should NOT mention 'physics'"
        )


# ── 10. Beautifier physics LaTeX symbols ──────────────────────────────


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
    def test_derive_beautifier_has_symbol(self, symbol: str):
        beautifier = _read(os.path.join(DERIVE_REFS, "beautifier.md"))
        assert symbol in beautifier, f"derive beautifier prompt missing physics symbol: {symbol}"

    @pytest.mark.parametrize("symbol", PHYSICS_SYMBOLS)
    def test_solve_beautifier_lacks_symbol(self, symbol: str):
        beautifier = _read(os.path.join(SOLVE_REFS, "beautifier.md"))
        assert symbol not in beautifier, (
            f"solve beautifier prompt should NOT contain physics symbol: {symbol}"
        )


# ── 11. Verifier physics-specific error checklist ─────────────────────


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
    def test_derive_verifier_has_physics_error(self, error_type: str):
        verifier = _read(os.path.join(DERIVE_REFS, "verifier.md")).lower()
        assert error_type in verifier, (
            f"derive verifier prompt missing physics error type: {error_type}"
        )


# ── 12. Preset table in orchestrator ──────────────────────────────────


class TestPresetTable:
    """The orchestrator should have a correct preset table."""

    @pytest.mark.parametrize(
        "preset,iters,revs,threshold,budget",
        [
            ("quick", "2", "1", "0.85", "20"),
            ("default", "5", "3", "0.90", "50"),
            ("thorough", "8", "5", "0.95", "80"),
            ("extreme", "12", "5", "0.97", "120"),
        ],
    )
    def test_preset_values(
        self, orchestrator: str, preset: str, iters: str, revs: str, threshold: str, budget: str
    ):
        presets = _extract_preset_table(orchestrator)
        row = next((p for p in presets if p["preset"] == preset), None)
        assert row is not None, f"Preset '{preset}' not found in orchestrator.md"
        assert row["iters"] == iters, f"{preset}: iters {row['iters']} != {iters}"
        assert row["revs"] == revs, f"{preset}: revs {row['revs']} != {revs}"
        assert row["threshold"] == threshold, (
            f"{preset}: threshold {row['threshold']} != {threshold}"
        )
        assert row["budget"] == budget, f"{preset}: budget {row['budget']} != {budget}"


# ── 13. Derive beautifier has physics document structure ──────────────


class TestDeriveBeautifierStructure:
    """alethic-derive beautifier should mention physics-specific document structure."""

    STRUCTURE_ELEMENTS = [
        "Setup",
        "Derivation",
        "Result",
        "Limiting cases",
    ]

    @pytest.mark.parametrize("element", STRUCTURE_ELEMENTS)
    def test_derive_beautifier_has_structure_element(self, element: str):
        beautifier = _read(os.path.join(DERIVE_REFS, "beautifier.md"))
        assert element in beautifier, (
            f"derive beautifier missing document structure element: {element}"
        )

    def test_derive_beautifier_mentions_physical_system(self):
        beautifier = _read(os.path.join(DERIVE_REFS, "beautifier.md"))
        assert "Physical system" in beautifier, (
            "derive beautifier should mention 'Physical system' in Setup"
        )

    def test_derive_beautifier_mentions_assumptions(self):
        beautifier = _read(os.path.join(DERIVE_REFS, "beautifier.md"))
        assert "assumptions" in beautifier.lower(), (
            "derive beautifier should mention 'assumptions' in Setup"
        )

    def test_derive_beautifier_mentions_approximations(self):
        beautifier = _read(os.path.join(DERIVE_REFS, "beautifier.md"))
        assert "approximations" in beautifier.lower(), (
            "derive beautifier should mention 'approximations' in Setup"
        )


# ── 14. Solve beautifier has math document structure ──────────────────


class TestSolveBeautifierStructure:
    """alethic-solve beautifier should mention math-specific document structure."""

    def test_solve_beautifier_has_proof_strategy(self):
        beautifier = _read(os.path.join(SOLVE_REFS, "beautifier.md"))
        assert "Proof strategy" in beautifier, "solve beautifier should mention 'Proof strategy'"

    def test_solve_beautifier_has_body(self):
        beautifier = _read(os.path.join(SOLVE_REFS, "beautifier.md"))
        assert "Body" in beautifier, "solve beautifier should mention 'Body'"

    def test_solve_beautifier_has_conclusion_with_blacksquare(self):
        beautifier = _read(os.path.join(SOLVE_REFS, "beautifier.md"))
        assert "blacksquare" in beautifier, (
            "solve beautifier should mention blacksquare in Conclusion"
        )


# ── 15. Verifier extended return line ─────────────────────────────────


class TestVerifierExtendedReturn:
    """Both verifiers should include HAS_CRITICAL and TOP_ISSUE in the return line."""

    @pytest.mark.parametrize("skill", ["alethic-solve", "alethic-derive"])
    def test_verifier_has_critical_field(self, skill: str):
        verifier = _read(os.path.join(BASE, skill, "references", "verifier.md"))
        assert "HAS_CRITICAL" in verifier, (
            f"{skill}/verifier.md missing HAS_CRITICAL in return line"
        )

    @pytest.mark.parametrize("skill", ["alethic-solve", "alethic-derive"])
    def test_verifier_has_top_issue_field(self, skill: str):
        verifier = _read(os.path.join(BASE, skill, "references", "verifier.md"))
        assert "TOP_ISSUE" in verifier, f"{skill}/verifier.md missing TOP_ISSUE in return line"

    @pytest.mark.parametrize("skill", ["alethic-solve", "alethic-derive"])
    def test_verifier_has_severity_tags(self, skill: str):
        verifier = _read(os.path.join(BASE, skill, "references", "verifier.md"))
        assert "[CRITICAL]" in verifier
        assert "[MAJOR]" in verifier
        assert "[MINOR]" in verifier


# ── 16. Orchestrator verification features ────────────────────────────


class TestOrchestratorVerificationFeatures:
    """The orchestrator should include CRITICAL-blocks-acceptance logic."""

    def test_critical_blocks_acceptance(self, orchestrator: str):
        assert "HAS_CRITICAL" in orchestrator, (
            "orchestrator should extract HAS_CRITICAL from verifier return"
        )

    def test_events_jsonl_logging(self, orchestrator: str):
        assert "events.jsonl" in orchestrator, "orchestrator should log events to events.jsonl"

    def test_failed_approaches_tracking(self, orchestrator: str):
        assert "failed_approaches" in orchestrator, "orchestrator should track failed_approaches"

    def test_elapsed_seconds(self, orchestrator: str):
        assert "elapsed_seconds" in orchestrator, "orchestrator should track elapsed_seconds"


# ── 17. Orchestrator CLI flags ────────────────────────────────────────


class TestOrchestratorCLIFlags:
    """The orchestrator should support all planned CLI flags."""

    FLAGS = ["--no-balanced", "--file", "--quiet", "--json", "--model"]

    @pytest.mark.parametrize("flag", FLAGS)
    def test_has_flag(self, orchestrator: str, flag: str):
        assert flag in orchestrator, f"orchestrator.md missing CLI flag: {flag}"


# ── 18. Balanced addendum in thin SKILL.md ────────────────────────────


class TestBalancedAddendum:
    """Both SKILL.md files should include a balanced approach addendum."""

    def test_solve_has_balanced_addendum(self, solve_skill: str):
        assert "Balanced Approach Addendum" in solve_skill

    def test_derive_has_balanced_addendum(self, derive_skill: str):
        assert "Balanced Approach Addendum" in derive_skill

    def test_solve_balanced_mentions_counterexamples(self, solve_skill: str):
        assert "counterexample" in solve_skill.lower(), (
            "solve balanced addendum should mention counterexamples"
        )

    def test_derive_balanced_mentions_limiting_cases(self, derive_skill: str):
        assert "limiting case" in derive_skill.lower(), (
            "derive balanced addendum should mention limiting cases"
        )


# ── 19. SymPy guidance in reference files ────────────────────────────


TOOL_OVERLAY_FILES = [
    "sympy-generator.md",
    "sympy-verifier.md",
    "numpy-generator.md",
    "numpy-verifier.md",
]


class TestToolOverlays:
    """Verify tool overlay files exist, contain expected content, and base files are clean."""

    # --- Overlay files exist ---
    @pytest.mark.parametrize("overlay", TOOL_OVERLAY_FILES)
    def test_solve_overlay_exists(self, overlay: str):
        path = os.path.join(SOLVE_REFS, "tools", overlay)
        assert os.path.isfile(path), f"Missing solve tool overlay: {path}"

    @pytest.mark.parametrize("overlay", TOOL_OVERLAY_FILES)
    def test_derive_overlay_exists(self, overlay: str):
        path = os.path.join(DERIVE_REFS, "tools", overlay)
        assert os.path.isfile(path), f"Missing derive tool overlay: {path}"

    # --- SymPy overlays mention SymPy ---
    @pytest.mark.parametrize("skill_refs", [SOLVE_REFS, DERIVE_REFS])
    def test_sympy_generator_mentions_sympy(self, skill_refs: str):
        content = _read(os.path.join(skill_refs, "tools", "sympy-generator.md"))
        assert "SymPy" in content

    @pytest.mark.parametrize("skill_refs", [SOLVE_REFS, DERIVE_REFS])
    def test_sympy_verifier_mentions_sympy(self, skill_refs: str):
        content = _read(os.path.join(skill_refs, "tools", "sympy-verifier.md"))
        assert "SymPy" in content

    # --- NumPy overlays mention NumPy ---
    @pytest.mark.parametrize("skill_refs", [SOLVE_REFS, DERIVE_REFS])
    def test_numpy_generator_mentions_numpy(self, skill_refs: str):
        content = _read(os.path.join(skill_refs, "tools", "numpy-generator.md"))
        assert "NumPy" in content

    @pytest.mark.parametrize("skill_refs", [SOLVE_REFS, DERIVE_REFS])
    def test_numpy_verifier_mentions_numpy(self, skill_refs: str):
        content = _read(os.path.join(skill_refs, "tools", "numpy-verifier.md"))
        assert "NumPy" in content

    # --- Verifier overlays contain RED FLAG ---
    @pytest.mark.parametrize("skill_refs", [SOLVE_REFS, DERIVE_REFS])
    def test_sympy_verifier_red_flag(self, skill_refs: str):
        content = _read(os.path.join(skill_refs, "tools", "sympy-verifier.md"))
        assert "RED FLAG" in content

    @pytest.mark.parametrize("skill_refs", [SOLVE_REFS, DERIVE_REFS])
    def test_numpy_verifier_red_flag(self, skill_refs: str):
        content = _read(os.path.join(skill_refs, "tools", "numpy-verifier.md"))
        assert "RED FLAG" in content

    # --- Physics overlays mention physics-specific modules ---
    def test_physics_sympy_generator_mentions_physics_modules(self):
        content = _read(os.path.join(DERIVE_REFS, "tools", "sympy-generator.md"))
        assert "sympy.physics" in content

    def test_physics_sympy_verifier_mentions_physics_modules(self):
        content = _read(os.path.join(DERIVE_REFS, "tools", "sympy-verifier.md"))
        assert "sympy.physics" in content

    def test_physics_numpy_generator_mentions_scipy_constants(self):
        content = _read(os.path.join(DERIVE_REFS, "tools", "numpy-generator.md"))
        assert "scipy.constants" in content

    def test_physics_numpy_verifier_mentions_scipy_constants(self):
        content = _read(os.path.join(DERIVE_REFS, "tools", "numpy-verifier.md"))
        assert "scipy.constants" in content

    # --- Math overlays do NOT mention physics modules ---
    def test_math_sympy_generator_no_physics_modules(self):
        content = _read(os.path.join(SOLVE_REFS, "tools", "sympy-generator.md"))
        assert "sympy.physics" not in content

    def test_math_sympy_verifier_no_physics_modules(self):
        content = _read(os.path.join(SOLVE_REFS, "tools", "sympy-verifier.md"))
        assert "sympy.physics" not in content

    # --- Base reference files no longer contain inline SymPy sections ---
    def test_solve_generator_no_inline_sympy_section(self):
        content = _read(os.path.join(SOLVE_REFS, "generator.md"))
        assert "### SymPy Verification Toolkit" not in content

    def test_solve_verifier_no_inline_sympy_section(self):
        content = _read(os.path.join(SOLVE_REFS, "verifier.md"))
        assert "### Mandatory SymPy Re-derivation" not in content

    def test_derive_generator_no_inline_sympy_section(self):
        content = _read(os.path.join(DERIVE_REFS, "generator.md"))
        assert "### SymPy Verification Toolkit" not in content

    def test_derive_verifier_no_inline_sympy_section(self):
        content = _read(os.path.join(DERIVE_REFS, "verifier.md"))
        assert "### Mandatory SymPy Re-derivation" not in content

    # --- Orchestrator mentions --tools flag ---
    def test_orchestrator_mentions_tools_flag(self):
        content = _read(ORCHESTRATOR)
        assert "--tools" in content

    # --- All SymPy overlays mention sp is pre-imported ---
    @pytest.mark.parametrize("skill_refs", [SOLVE_REFS, DERIVE_REFS])
    def test_sympy_overlays_mention_sp_preimport(self, skill_refs: str):
        for f in ["sympy-generator.md", "sympy-verifier.md"]:
            content = _read(os.path.join(skill_refs, "tools", f))
            assert "pre-imported as `sp`" in content, (
                f"{skill_refs}/tools/{f} missing sp pre-import note"
            )

    # --- All NumPy overlays mention np is pre-imported ---
    @pytest.mark.parametrize("skill_refs", [SOLVE_REFS, DERIVE_REFS])
    def test_numpy_overlays_mention_np_preimport(self, skill_refs: str):
        for f in ["numpy-generator.md", "numpy-verifier.md"]:
            content = _read(os.path.join(skill_refs, "tools", f))
            assert "pre-imported as `np`" in content, (
                f"{skill_refs}/tools/{f} missing np pre-import note"
            )
