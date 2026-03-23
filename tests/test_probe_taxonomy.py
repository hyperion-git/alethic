"""Probe G: Error Taxonomy Routing Completeness.

Systematically probes error_taxonomy.py and its integration in agent.py
for routing table gaps, edge cases, priority ordering, and physics-specific
keyword coverage.
"""

from __future__ import annotations

import pytest

from alethic.error_taxonomy import (
    REVISION_ADDENDA,
    InconsistencyResult,
    _KEYWORDS,
    _ORACLE_ROUTING,
    _TAXONOMY_KEYWORDS,
    _TREE,
    classify_errors,
    classify_errors_routed,
    classify_inconsistency,
    get_all_categories,
    get_revision_addendum,
)
from alethic.models import OracleType


# ---------------------------------------------------------------------------
# Probe 1: Routing table completeness
# ---------------------------------------------------------------------------

class TestRoutingTableCompleteness:
    """Every return value of classify_errors() must be a valid key in both
    REVISION_ADDENDA and _ORACLE_ROUTING."""

    def test_all_taxonomy_categories_in_revision_addenda(self):
        """Each keyword-based category has a revision addendum entry."""
        for category in _KEYWORDS:
            assert category in REVISION_ADDENDA, (
                f"Category '{category}' from _KEYWORDS missing in REVISION_ADDENDA"
            )

    def test_general_in_revision_addenda(self):
        """The fallback 'general' category has a revision addendum entry."""
        assert "general" in REVISION_ADDENDA

    def test_all_taxonomy_categories_in_oracle_routing(self):
        """Each keyword-based category has an oracle routing entry."""
        for category in _KEYWORDS:
            assert category in _ORACLE_ROUTING, (
                f"Category '{category}' from _KEYWORDS missing in _ORACLE_ROUTING"
            )

    def test_general_in_oracle_routing(self):
        """The fallback 'general' category has an oracle routing entry."""
        assert "general" in _ORACLE_ROUTING

    def test_oracle_routing_values_are_valid_oracle_types(self):
        """Every value in _ORACLE_ROUTING is a (OracleType, bool) tuple."""
        for category, (oracle, force_adv) in _ORACLE_ROUTING.items():
            assert isinstance(oracle, OracleType), (
                f"Category '{category}' has invalid oracle type: {oracle}"
            )
            assert isinstance(force_adv, bool), (
                f"Category '{category}' has non-bool force_adversarial: {force_adv}"
            )

    def test_no_orphan_addenda(self):
        """Every key in REVISION_ADDENDA is either a taxonomy category or 'general'."""
        valid_cats = set(_KEYWORDS.keys()) | {"general"}
        for category in REVISION_ADDENDA:
            assert category in valid_cats, (
                f"REVISION_ADDENDA has orphan key '{category}' not in taxonomy or 'general'"
            )

    def test_no_orphan_oracle_routes(self):
        """Every key in _ORACLE_ROUTING is either a taxonomy category or 'general'."""
        valid_cats = set(_KEYWORDS.keys()) | {"general"}
        for category in _ORACLE_ROUTING:
            assert category in valid_cats, (
                f"_ORACLE_ROUTING has orphan key '{category}' not in taxonomy or 'general'"
            )

    def test_classify_errors_return_values_exhaustive(self):
        """classify_errors can only return taxonomy keys or 'general'.
        Verify by checking the function logic directly."""
        # All keyword-match paths return a _KEYWORDS key
        possible_returns = set(_KEYWORDS.keys()) | {"general"}
        # Generate a critique for each category and verify return is in the set
        for category, keywords in _KEYWORDS.items():
            for kw in keywords:
                result = classify_errors(f"This has a {kw} problem")
                assert result in possible_returns, (
                    f"classify_errors returned '{result}' which is not in valid set"
                )
        # Fallback
        assert classify_errors("nothing matches here xyz 12345") in possible_returns

    def test_dynamic_n_category_coverage(self):
        """_compute_dynamic_n in agent.py uses hardcoded category sets.
        Verify all taxonomy categories are accounted for in either the escalate
        set or the revise-first set (or fall through to confidence-based)."""
        # These sets are from agent.py _compute_dynamic_n
        escalate_categories = {"logic", "missing_case", "interpretation", "units", "counterexample", "false_premise", "wrong_method"}
        revise_first_categories = {"algebra", "citation"}
        all_taxonomy_cats = set(_KEYWORDS.keys())

        covered = escalate_categories | revise_first_categories
        uncovered = all_taxonomy_cats - covered
        # Any uncovered categories fall through to confidence-based routing,
        # which is fine. Just document what falls through.
        assert uncovered == set(), (
            f"Categories {uncovered} not explicitly handled in _compute_dynamic_n; "
            f"they fall through to confidence-based routing"
        )


# ---------------------------------------------------------------------------
# Probe 2: Empty and edge-case critique handling
# ---------------------------------------------------------------------------

class TestEmptyCritiqueHandling:
    """classify_errors and classify_errors_routed must handle empty/unusual input."""

    def test_empty_string_returns_general(self):
        """Empty critique must return 'general', not crash."""
        assert classify_errors("") == "general"

    def test_empty_string_routed_returns_general(self):
        """classify_errors_routed('') must not crash and must return general."""
        category, oracle, force_adv = classify_errors_routed("")
        assert category == "general"
        assert oracle == OracleType.LAYER3_LLM
        assert force_adv is False

    def test_whitespace_only_returns_general(self):
        """Whitespace-only critique returns 'general'."""
        assert classify_errors("   \n\t  ") == "general"

    def test_none_would_crash(self):
        """classify_errors(None) should raise (str methods on NoneType)."""
        with pytest.raises(AttributeError):
            classify_errors(None)

    def test_very_long_critique(self):
        """Long critique with no keywords returns 'general'."""
        long_text = "This solution has problems. " * 10000
        assert classify_errors(long_text) == "general"

    def test_community_no_longer_false_positive(self):
        """'unit' was removed from units keywords to avoid false positives.
        'community' no longer triggers units."""
        result = classify_errors("The community of mathematicians agrees this is wrong")
        assert result == "general", (
            "'community' should no longer match units (bare 'unit' keyword removed)"
        )

    def test_get_revision_addendum_empty_critique_category(self):
        """get_revision_addendum for 'general' (from empty critique) returns empty string."""
        cat = classify_errors("")
        addendum = get_revision_addendum(cat)
        assert addendum == ""

    def test_get_revision_addendum_none_key(self):
        """get_revision_addendum with None key doesn't crash (returns '')."""
        assert get_revision_addendum(None) == ""


# ---------------------------------------------------------------------------
# Probe 3: Multi-category critique priority ordering
# ---------------------------------------------------------------------------

class TestMultiCategoryPriority:
    """When a critique mentions keywords from multiple categories, the hierarchical
    tree structure determines the winner: first firing level wins, then within a
    level the category with the most hits (ties broken by list position)."""

    def test_tree_structure_is_deterministic(self):
        """_TREE level ordering is severity-descending:
        problem, approach, structural, mechanical, presentation."""
        expected_levels = ["problem", "approach", "structural", "mechanical", "presentation"]
        actual_levels = [level for level, _ in _TREE]
        assert actual_levels == expected_levels

    def test_tree_categories_complete(self):
        """Every category in _KEYWORDS appears in exactly one _TREE level."""
        tree_cats = {cat for _, cats in _TREE for cat in cats}
        assert tree_cats == set(_KEYWORDS.keys())

    def test_false_premise_beats_algebra(self):
        """false_premise (most severe) beats algebra (least severe)."""
        critique = "There is a sign error but the claim is false"
        assert classify_errors(critique) == "false_premise"

    def test_false_premise_beats_logic(self):
        """false_premise beats logic."""
        critique = "The inference does not follow because the claim is false"
        assert classify_errors(critique) == "false_premise"

    def test_counterexample_beats_logic(self):
        """counterexample beats logic."""
        critique = "A counterexample was found and the logic does not follow"
        assert classify_errors(critique) == "counterexample"

    def test_algebra_beats_interpretation(self):
        """algebra (mechanical level) beats interpretation (presentation level)."""
        critique = "The solution misinterprets the problem and has a sign error"
        assert classify_errors(critique) == "algebra"

    def test_missing_case_beats_algebra(self):
        """missing_case (structural level) beats algebra (mechanical level)."""
        critique = "There is a sign error and a missing case for n=0"
        assert classify_errors(critique) == "missing_case"

    def test_logic_beats_citation(self):
        """Logic (structural) has higher severity than citation (presentation)."""
        critique = "The conclusion does not follow from the cited theorem"
        assert classify_errors(critique) == "logic"

    def test_logic_beats_algebra(self):
        """Logic (structural) beats algebra (mechanical) in severity order."""
        critique = "There is a sign error and the inference does not follow"
        assert classify_errors(critique) == "logic"

    def test_algebra_beats_units(self):
        """Within mechanical level, algebra comes first in the list, so it wins ties.
        But if only units fires, units wins. Test with both present."""
        critique = "There is a sign error and a dimensional mismatch"
        assert classify_errors(critique) == "algebra"

    def test_algebra_beats_citation(self):
        """Algebra (mechanical) beats citation (presentation)."""
        critique = "There is a sign error and no citation is given"
        assert classify_errors(critique) == "algebra"

    def test_all_categories_present_false_premise_wins(self):
        """When all categories are present, false_premise (highest severity) wins."""
        critique = (
            "The claim is false, sign error, does not follow, no citation, "
            "misinterprets the problem, dimensional issue, missing case for n=0"
        )
        assert classify_errors(critique) == "false_premise"


# ---------------------------------------------------------------------------
# Probe 4: Category -> revision addendum content verification
# ---------------------------------------------------------------------------

class TestAddendumContentVerification:
    """Each category's addendum must be non-empty (except general), distinct,
    and actually contain revision-relevant guidance."""

    def test_all_non_general_addenda_are_nonempty(self):
        """Every non-general category produces a non-empty addendum."""
        for category in _KEYWORDS:
            addendum = get_revision_addendum(category)
            assert addendum, f"Category '{category}' has empty addendum"
            assert len(addendum) > 20, f"Category '{category}' addendum suspiciously short"

    def test_general_addendum_is_empty_string(self):
        """The 'general' addendum is explicitly empty string (not None)."""
        addendum = get_revision_addendum("general")
        assert addendum == ""
        assert addendum is not None

    def test_all_addenda_are_distinct(self):
        """No two non-general categories produce identical addenda."""
        non_general = {
            cat: addendum
            for cat, addendum in REVISION_ADDENDA.items()
            if cat != "general"
        }
        seen = {}
        for category, addendum in non_general.items():
            for prev_cat, prev_addendum in seen.items():
                assert addendum != prev_addendum, (
                    f"Categories '{category}' and '{prev_cat}' have identical addenda"
                )
            seen[category] = addendum

    def test_algebra_addendum_mentions_signs(self):
        """Algebra addendum should reference sign-related errors."""
        addendum = get_revision_addendum("algebra")
        assert "sign" in addendum.lower()

    def test_logic_addendum_mentions_justification(self):
        """Logic addendum should reference logical justification."""
        addendum = get_revision_addendum("logic")
        assert "justif" in addendum.lower() or "rigor" in addendum.lower()

    def test_citation_addendum_mentions_theorem_names(self):
        """Citation addendum should mention citing by name."""
        addendum = get_revision_addendum("citation")
        assert "name" in addendum.lower() or "cite" in addendum.lower()

    def test_units_addendum_mentions_dimensions(self):
        """Units addendum should mention dimensional checking."""
        addendum = get_revision_addendum("units")
        assert "dimension" in addendum.lower() or "unit" in addendum.lower()

    def test_missing_case_addendum_mentions_cases(self):
        """Missing case addendum should mention case enumeration."""
        addendum = get_revision_addendum("missing_case")
        assert "case" in addendum.lower()

    def test_interpretation_addendum_mentions_problem(self):
        """Interpretation addendum should mention re-reading the problem."""
        addendum = get_revision_addendum("interpretation")
        assert "problem" in addendum.lower()

    def test_wrong_method_addendum_mentions_approach(self):
        """wrong_method addendum should mention changing approach."""
        addendum = get_revision_addendum("wrong_method")
        assert addendum, "wrong_method has empty addendum"
        assert "approach" in addendum.lower() or "method" in addendum.lower()

    def test_addendum_or_none_conversion_in_agent(self):
        """In agent.py, revision_addendum is converted via `revision_addendum or None`.
        Verify that empty string from 'general' becomes None."""
        addendum = get_revision_addendum("general")
        assert (addendum or None) is None, (
            "'general' addendum should convert to None via `or None`"
        )

    def test_nonempty_addenda_survive_or_none(self):
        """Non-general addenda should NOT be converted to None by `or None`."""
        for category in _KEYWORDS:
            addendum = get_revision_addendum(category)
            assert (addendum or None) is not None, (
                f"Category '{category}' addendum is falsy — would become None in agent.py"
            )


# ---------------------------------------------------------------------------
# Probe 5: Physics-specific error patterns
# ---------------------------------------------------------------------------

class TestPhysicsErrorPatterns:
    """Many physics-specific error descriptions should map to existing categories
    but may fall through to 'general' due to missing keywords."""

    # --- Patterns that SHOULD match existing categories ---

    def test_dimensional_mismatch_maps_to_units(self):
        """'dimensional mismatch' should match units."""
        assert classify_errors("The result has a dimensional mismatch") == "units"

    def test_dimensional_keyword_maps_to_units(self):
        """'dimensional' is a units keyword."""
        assert classify_errors("Energy equation has dimensional inconsistency") == "units"

    def test_si_unit_maps_to_units(self):
        """'si unit' is a units keyword."""
        assert classify_errors("The SI unit for energy should be Joules") == "units"

    # --- Patterns that fall through to general (potential gaps) ---

    def test_gauge_invariance_violated(self):
        """'gauge invariance violated' is a physics error — check what it maps to.
        Currently no keyword covers this; it falls to 'general'."""
        result = classify_errors("The result violates gauge invariance")
        # No keyword match: falls through to general
        assert result == "general", (
            f"Unexpectedly matched '{result}' — good if intentional"
        )

    def test_non_hermitian_hamiltonian(self):
        """'non-Hermitian Hamiltonian' is a physics error — no keyword coverage."""
        result = classify_errors("The Hamiltonian is not Hermitian")
        assert result == "general"

    def test_normalization_wrong(self):
        """'normalization is wrong' contains no taxonomy keyword."""
        result = classify_errors("The wavefunction normalization is incorrect")
        # "incorrect" does not match any keyword (no "incorrect" alone)
        assert result == "general"

    def test_commutator_wrong(self):
        """'commutator is wrong' has no keyword coverage."""
        result = classify_errors("The commutator relation [x, p] is computed incorrectly")
        assert result == "general"

    def test_lorentz_covariance_broken(self):
        """'Lorentz covariance broken' is a physics error — no keyword."""
        result = classify_errors("The result is not Lorentz covariant")
        assert result == "general"

    def test_causality_violated(self):
        """'causality violated' is a physics constraint — no keyword."""
        result = classify_errors("The solution violates causality")
        assert result == "general"

    def test_conservation_law_violated(self):
        """'conservation law violated' could be a physics error — no keyword."""
        result = classify_errors("The solution violates conservation of energy")
        assert result == "general"

    def test_boundary_condition_wrong(self):
        """'boundary condition' IS a keyword for missing_case."""
        result = classify_errors("The boundary condition at r=0 is incorrectly applied")
        assert result == "missing_case"

    def test_wrong_limit(self):
        """'limit' is not a keyword; physics limiting case errors fall through."""
        result = classify_errors("The classical limit does not reduce to Newton's law")
        assert result == "general"

    def test_symmetry_broken(self):
        """'symmetry broken' has no keyword coverage."""
        result = classify_errors("The solution breaks the rotational symmetry of the problem")
        assert result == "general"

    def test_sign_error_in_physics(self):
        """'sign error' should still match algebra even in physics context."""
        result = classify_errors("There is a sign error in the metric tensor component g_tt")
        assert result == "algebra"

    def test_calculation_error_in_physics(self):
        """'calculation error' should match algebra in physics context."""
        result = classify_errors("Calculation error in the partition function integral")
        assert result == "algebra"

    # --- Subtle substring false positives ---

    def test_magnitude_no_longer_maps_to_units(self):
        """'magnitude' was removed from units keywords to avoid false positives."""
        result = classify_errors("The magnitude of the vector is large")
        assert result == "general", (
            "'magnitude' removed from units keywords — no longer triggers"
        )

    def test_expand_in_expanding_still_matches_algebra(self):
        """'expand' substring in 'expanding' still triggers algebra."""
        result = classify_errors("The proof needs expanding on this point")
        assert result == "algebra", (
            "'expand' substring in 'expanding' triggers algebra"
        )

    def test_conversion_no_longer_maps_to_units(self):
        """'conversion' was removed from units keywords to avoid false positives."""
        result = classify_errors("The Fourier conversion step is wrong")
        assert result == "general", (
            "'conversion' removed from units keywords — no longer triggers"
        )


# ---------------------------------------------------------------------------
# Probe 1 supplement: classify_errors_routed never raises KeyError
# ---------------------------------------------------------------------------

class TestRoutedNeverRaises:
    """classify_errors_routed must never raise KeyError for any classify_errors output."""

    @pytest.mark.parametrize("critique", [
        "",
        "   ",
        "This is fine.",
        "sign error",
        "does not follow",
        "no citation",
        "misinterprets the problem",
        "dimensional inconsistency",
        "missing case",
        "The gauge invariance is violated",
        "The Hamiltonian is not Hermitian",
        "No issues found, solution is correct",
        "x" * 100000,
    ])
    def test_routed_never_raises(self, critique: str):
        """classify_errors_routed should never raise for any string input."""
        category, oracle, force_adv = classify_errors_routed(critique)
        assert isinstance(category, str)
        assert isinstance(oracle, OracleType)
        assert isinstance(force_adv, bool)

    @pytest.mark.parametrize("category", list(_KEYWORDS.keys()) + ["general"])
    def test_all_categories_have_oracle_route(self, category: str):
        """Every possible category from classify_errors has an oracle route."""
        assert category in _ORACLE_ROUTING, (
            f"Category '{category}' has no oracle route — would raise KeyError"
        )


# ---------------------------------------------------------------------------
# Probe 4 supplement: keyword coverage per category
# ---------------------------------------------------------------------------

class TestKeywordCoverage:
    """Each category should have multiple keywords and no empty keyword lists."""

    def test_no_empty_keyword_lists(self):
        """Every category in _KEYWORDS has at least one keyword."""
        for category, keywords in _KEYWORDS.items():
            assert len(keywords) > 0, f"Category '{category}' has no keywords"

    def test_no_empty_keywords(self):
        """No keyword is an empty string (would match everything)."""
        for category, keywords in _KEYWORDS.items():
            for kw in keywords:
                assert kw, f"Category '{category}' has an empty keyword"
                assert kw.strip(), f"Category '{category}' has a whitespace-only keyword"

    def test_keywords_are_lowercase(self):
        """All keywords must be lowercase (matching is done on .lower() input)."""
        for category, keywords in _KEYWORDS.items():
            for kw in keywords:
                assert kw == kw.lower(), (
                    f"Category '{category}' has non-lowercase keyword: '{kw}'"
                )

    def test_no_duplicate_keywords_within_category(self):
        """No category has duplicate keywords."""
        for category, keywords in _KEYWORDS.items():
            seen = set()
            for kw in keywords:
                assert kw not in seen, (
                    f"Category '{category}' has duplicate keyword: '{kw}'"
                )
                seen.add(kw)

    def test_no_duplicate_keywords_across_categories(self):
        """No keyword appears in multiple categories (would be shadowed by priority)."""
        seen: dict[str, str] = {}
        for category, keywords in _KEYWORDS.items():
            for kw in keywords:
                if kw in seen:
                    # Not necessarily a bug — priority handles it — but worth flagging
                    pytest.fail(
                        f"Keyword '{kw}' appears in both '{seen[kw]}' and '{category}'. "
                        f"The '{seen[kw]}' category shadows '{category}' for this keyword."
                    )
                seen[kw] = category


# ---------------------------------------------------------------------------
# Integration: verify classify_errors -> get_revision_addendum roundtrip
# ---------------------------------------------------------------------------

class TestClassifyAndAddendumRoundtrip:
    """The full path classify_errors() -> get_revision_addendum() must always
    produce a valid result for any input."""

    @pytest.mark.parametrize("critique", [
        "",
        "sign error in step 2",
        "circular argument detected",
        "no citation given for this identity",
        "misinterprets the boundary conditions",
        "dimensional mismatch in the exponent",
        "missing case for n=0",
        "The solution is vaguely wrong",
        "gauge invariance violated",
    ])
    def test_roundtrip_never_crashes(self, critique: str):
        """classify_errors -> get_revision_addendum never crashes."""
        category = classify_errors(critique)
        addendum = get_revision_addendum(category)
        assert isinstance(addendum, str)
        # Verify the agent.py `addendum or None` pattern works
        converted = addendum or None
        assert converted is None or isinstance(converted, str)

    @pytest.mark.parametrize("critique,expected_nonempty", [
        ("sign error", True),
        ("does not follow", True),
        ("well known", True),
        ("misread the problem", True),
        ("dimensional error", True),
        ("edge case not handled", True),
        ("unclear solution", False),  # general -> empty addendum
        ("", False),
    ])
    def test_roundtrip_produces_expected_content(self, critique: str, expected_nonempty: bool):
        """Verify the roundtrip produces non-empty addendum for known categories
        and empty for general."""
        category = classify_errors(critique)
        addendum = get_revision_addendum(category)
        if expected_nonempty:
            assert len(addendum) > 0, (
                f"Expected non-empty addendum for critique '{critique}' (cat={category})"
            )
        else:
            assert addendum == "", (
                f"Expected empty addendum for critique '{critique}' (cat={category})"
            )


# ---------------------------------------------------------------------------
# Hierarchical classify_inconsistency() tests
# ---------------------------------------------------------------------------

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
        # Algebra has 3 hits but logic fires first at level 2 (structural)
        result = classify_inconsistency(
            "sign error, wrong sign, arithmetic — also a logical gap"
        )
        assert result.level == "structural"
        assert result.primary == "logic"
        assert result.all_matches["algebra"] == 3

    def test_within_level_tiebreak_by_position(self):
        result = classify_inconsistency("missing case and does not follow")
        assert result.level == "structural"
        assert result.primary == "missing_case"  # first in level

    def test_no_match_returns_general(self):
        result = classify_inconsistency("The proof is beautiful but wrong")
        assert result.level == "none"  # NOTE: "none", not "unknown"
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
        assert result.level == "none"
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
