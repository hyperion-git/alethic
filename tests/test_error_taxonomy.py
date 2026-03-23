"""Tests for error taxonomy-driven revision (feature 1.2)."""

from __future__ import annotations


class TestClassifyErrors:
    """classify_errors() must map critique text to known categories."""

    def test_algebra_keywords(self):
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("There is a sign error in step 3") == "algebra"
        assert classify_errors("The arithmetic is wrong: 2+2=5") == "algebra"
        assert classify_errors("Incorrect simplification in the expansion") == "algebra"

    def test_logic_keywords(self):
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("This implication does not follow from the premises") == "logic"
        assert classify_errors("The reasoning contains a circular argument") == "logic"
        assert classify_errors("There is a gap in the logical chain") == "logic"

    def test_citation_keywords(self):
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("Vague appeal: 'it is well known' without citation") == "citation"
        assert classify_errors("The referenced theorem is cited by vague description only") == "citation"
        assert classify_errors("No source is given for this standard result") == "citation"

    def test_interpretation_keywords(self):
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("The solution misinterprets the problem statement") == "interpretation"
        assert classify_errors("The problem premise was misread") == "interpretation"

    def test_units_keywords(self):
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("Dimensional inconsistency: units do not balance") == "units"
        assert classify_errors("The SI unit conversion is wrong") == "units"

    def test_missing_case_keywords(self):
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("The edge case n=0 is not handled") == "missing_case"
        assert classify_errors("Missing case: when x is negative") == "missing_case"
        assert classify_errors("The boundary case is not considered") == "missing_case"

    def test_general_fallback(self):
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("The solution is incomplete.") == "general"
        assert classify_errors("") == "general"

    def test_case_insensitive(self):
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("SIGN ERROR in the expansion") == "algebra"


class TestGetRevisionAddendum:
    """get_revision_addendum() must return non-empty strings for known categories."""

    def test_algebra_addendum_nonempty(self):
        from alethic.error_taxonomy import get_revision_addendum

        assert len(get_revision_addendum("algebra")) > 20

    def test_logic_addendum_nonempty(self):
        from alethic.error_taxonomy import get_revision_addendum

        assert len(get_revision_addendum("logic")) > 20

    def test_general_addendum_is_empty(self):
        from alethic.error_taxonomy import get_revision_addendum

        # General category adds nothing — reviser gets standard prompt
        assert get_revision_addendum("general") == ""

    def test_unknown_category_returns_empty(self):
        from alethic.error_taxonomy import get_revision_addendum

        assert get_revision_addendum("nonexistent") == ""

    def test_all_known_categories_have_addenda(self):
        from alethic.error_taxonomy import REVISION_ADDENDA

        for category, addendum in REVISION_ADDENDA.items():
            if category != "general":
                assert len(addendum) > 20, f"Category '{category}' has empty addendum"


class TestClassifyErrorsRouted:
    """classify_errors_routed() must return (category, OracleType, force_adversarial)."""

    def test_routed_algebra_error(self):
        from alethic.error_taxonomy import classify_errors_routed
        from alethic.models import OracleType

        category, oracle, force_adv = classify_errors_routed("sign error in step 3")
        assert category == "algebra"
        assert oracle == OracleType.LAYER2_CONSISTENCY
        assert force_adv is False

    def test_routed_logic_error(self):
        from alethic.error_taxonomy import classify_errors_routed
        from alethic.models import OracleType

        category, oracle, force_adv = classify_errors_routed("logical gap in the proof")
        assert category == "logic"
        assert oracle == OracleType.LAYER3_LLM_ADVERSARIAL
        assert force_adv is True

    def test_routed_units_error(self):
        from alethic.error_taxonomy import classify_errors_routed
        from alethic.models import OracleType

        category, oracle, force_adv = classify_errors_routed("dimensional mismatch found")
        assert category == "units"
        assert oracle == OracleType.LAYER0_STRUCTURAL
        assert force_adv is False

    def test_routed_citation_error(self):
        from alethic.error_taxonomy import classify_errors_routed
        from alethic.models import OracleType

        category, oracle, force_adv = classify_errors_routed("no citation for this result")
        assert category == "citation"
        assert oracle == OracleType.LAYER3_LLM
        assert force_adv is False

    def test_routed_missing_case(self):
        from alethic.error_taxonomy import classify_errors_routed
        from alethic.models import OracleType

        category, oracle, force_adv = classify_errors_routed("missing edge case n=0")
        assert category == "missing_case"
        assert oracle == OracleType.LAYER1_BEHAVIORAL
        assert force_adv is False

    def test_routed_general(self):
        from alethic.error_taxonomy import classify_errors_routed
        from alethic.models import OracleType

        category, oracle, force_adv = classify_errors_routed("unclear solution")
        assert category == "general"
        assert oracle == OracleType.LAYER3_LLM
        assert force_adv is False


def test_counterexample_category():
    from alethic.error_taxonomy import classify_errors, get_revision_addendum

    assert classify_errors("A specific counterexample was found at n=0.") == "counterexample"
    assert classify_errors("The breaker found a flaw in atom 3.") == "counterexample"
    assert classify_errors("This is a regime failure for v > c.") == "counterexample"
    addendum = get_revision_addendum("counterexample")
    assert "counterexample" in addendum.lower() or "flaw" in addendum.lower()


def test_counterexample_oracle_routing():
    from alethic.error_taxonomy import classify_errors_routed
    from alethic.models import OracleType

    cat, oracle, force_adv = classify_errors_routed("A counterexample was found.")
    assert cat == "counterexample"
    assert oracle == OracleType.LAYER1_BEHAVIORAL


def test_wrong_method_classification():
    from alethic.error_taxonomy import classify_errors

    assert classify_errors("The approach is inapplicable") == "wrong_method"


def test_wrong_method_routed():
    from alethic.error_taxonomy import classify_errors_routed
    from alethic.models import OracleType

    cat, oracle, force = classify_errors_routed("wrong approach to this problem")
    assert cat == "wrong_method"
    assert oracle == OracleType.LAYER3_LLM_ADVERSARIAL
    assert force is True


def test_wrong_method_addendum():
    from alethic.error_taxonomy import get_revision_addendum

    addendum = get_revision_addendum("wrong_method")
    assert addendum  # non-empty
    assert "approach" in addendum.lower() or "method" in addendum.lower()
