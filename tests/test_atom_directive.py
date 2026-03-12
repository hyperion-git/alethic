"""Tests for _combine() and (later) _build_atom_focus_directive()."""

import pytest
from alethic.agent import _combine


class TestCombine:
    def test_both_none(self):
        assert _combine(None, None) is None

    def test_a_only(self):
        assert _combine("foo", None) == "foo"

    def test_b_only(self):
        assert _combine(None, "bar") == "bar"

    def test_both_present(self):
        assert _combine("foo", "bar") == "foo\n\nbar"

    def test_strips_leading_newline_a(self):
        assert _combine("\nfoo", "bar") == "foo\n\nbar"

    def test_strips_trailing_newline_a(self):
        assert _combine("foo\n", "bar") == "foo\n\nbar"

    def test_strips_leading_newline_b(self):
        assert _combine("foo", "\nbar") == "foo\n\nbar"

    def test_strips_trailing_newline_b(self):
        assert _combine("foo", "bar\n") == "foo\n\nbar"

    def test_empty_string_a_treated_as_falsy(self):
        assert _combine("", "bar") == "bar"

    def test_empty_string_b_treated_as_falsy(self):
        assert _combine("foo", "") == "foo"

    def test_newlines_only_a_treated_as_falsy(self):
        assert _combine("\n", "bar") == "bar"
