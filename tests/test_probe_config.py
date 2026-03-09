"""Probe E: Configuration & Preset Consistency tests.

Naming convention: test_probe_e{N}_{description}

Probe points:
    E1 - Cross-field validation gaps (adaptive_compute + best_of_n, stall_reset + stall_window, variant_b keys)
    E2 - CLI flag override precedence (explicit flags must override presets)
    E3 - from_preset() with unknown preset (must raise ValueError)
    E4 - VALID_TOOL_GUIDANCE consistency across AgentConfig and VerifierConfig
    E5 - Preset field completeness and type correctness
    E6 - Variant-B validation: variant_b keys must be valid AgentConfig field names
    E7 - VerifierConfig from_preset() mirrors AgentConfig behavior
    E8 - CLI _build_config edge cases
"""
from __future__ import annotations

import argparse
from dataclasses import fields as dataclass_fields
from typing import Any

import pytest

from alethic.models import (
    VALID_TOOL_GUIDANCE,
    AgentConfig,
    VerifierConfig,
)


# ===================================================================
# E1 — Cross-field validation gaps
# ===================================================================


class TestE1CrossFieldValidation:
    """Probe: does __post_init__ catch logically invalid field combinations?"""

    def test_probe_e1a_adaptive_compute_with_best_of_n_1(self):
        """adaptive_compute=True with best_of_n=1 is semantically a no-op.

        The dynamic N logic in _compute_dynamic_n uses config.best_of_n as the
        escalation ceiling. If best_of_n=1, escalation returns 1 — the feature
        does nothing. This should ideally be validated, but currently is NOT.
        We document this as accepted behavior (no crash, just a silent no-op).
        """
        # Should not raise — currently no validation for this combination
        cfg = AgentConfig(adaptive_compute=True, best_of_n=1)
        assert cfg.adaptive_compute is True
        assert cfg.best_of_n == 1
        # Document: the feature is silently ineffective

    def test_probe_e1b_stall_reset_true_stall_window_minimum(self):
        """stall_window is independently validated (>= 1), regardless of stall_reset."""
        with pytest.raises(ValueError, match="stall_window must be >= 1"):
            AgentConfig(stall_window=0, stall_reset=True)

        with pytest.raises(ValueError, match="stall_window must be >= 1"):
            AgentConfig(stall_window=0, stall_reset=False)

    def test_probe_e1c_stall_reset_false_stall_window_still_validated(self):
        """Even with stall_reset=False, stall_window must be >= 1."""
        with pytest.raises(ValueError, match="stall_window"):
            AgentConfig(stall_window=-1, stall_reset=False)

    def test_probe_e1d_best_of_n_zero_raises(self):
        """best_of_n must be >= 1."""
        with pytest.raises(ValueError, match="best_of_n must be >= 1"):
            AgentConfig(best_of_n=0)

    def test_probe_e1e_best_of_n_negative_raises(self):
        """best_of_n must be >= 1."""
        with pytest.raises(ValueError, match="best_of_n must be >= 1"):
            AgentConfig(best_of_n=-3)

    def test_probe_e1f_max_iterations_zero_raises(self):
        """max_iterations must be >= 1."""
        with pytest.raises(ValueError, match="max_iterations must be >= 1"):
            AgentConfig(max_iterations=0)

    def test_probe_e1g_max_revisions_per_cycle_negative_raises(self):
        """max_revisions_per_cycle must be >= 0."""
        with pytest.raises(ValueError, match="max_revisions_per_cycle must be >= 0"):
            AgentConfig(max_revisions_per_cycle=-1)

    def test_probe_e1h_confidence_threshold_out_of_range(self):
        """confidence_threshold must be in [0.0, 1.0]."""
        with pytest.raises(ValueError, match="confidence_threshold"):
            AgentConfig(confidence_threshold=1.5)
        with pytest.raises(ValueError, match="confidence_threshold"):
            AgentConfig(confidence_threshold=-0.1)

    def test_probe_e1i_context_threshold_zero_raises(self):
        """context_threshold must be in (0.0, 1.0]."""
        with pytest.raises(ValueError, match="context_threshold"):
            AgentConfig(context_threshold=0.0)
        # 1.0 should be valid (boundary)
        cfg = AgentConfig(context_threshold=1.0)
        assert cfg.context_threshold == 1.0

    def test_probe_e1j_context_threshold_above_1_raises(self):
        """context_threshold must be <= 1.0."""
        with pytest.raises(ValueError, match="context_threshold"):
            AgentConfig(context_threshold=1.01)

    def test_probe_e1k_adaptive_budget_cap_zero_raises(self):
        """adaptive_budget_cap must be >= 1 if set."""
        with pytest.raises(ValueError, match="adaptive_budget_cap must be >= 1"):
            AgentConfig(adaptive_budget_cap=0)

    def test_probe_e1l_adaptive_budget_cap_none_ok(self):
        """adaptive_budget_cap=None means unlimited."""
        cfg = AgentConfig(adaptive_budget_cap=None)
        assert cfg.adaptive_budget_cap is None

    def test_probe_e1m_negative_temperatures_raise(self):
        """All temperature fields must be >= 0."""
        with pytest.raises(ValueError, match="temperature_generator"):
            AgentConfig(temperature_generator=-0.1)
        with pytest.raises(ValueError, match="temperature_verifier"):
            AgentConfig(temperature_verifier=-1)
        with pytest.raises(ValueError, match="temperature_reviser"):
            AgentConfig(temperature_reviser=-0.5)

    def test_probe_e1n_max_tokens_zero_raises(self):
        """max_tokens must be >= 1."""
        with pytest.raises(ValueError, match="max_tokens must be >= 1"):
            AgentConfig(max_tokens=0)

    def test_probe_e1o_thinking_budget_negative_raises(self):
        """thinking_budget must be >= 0."""
        with pytest.raises(ValueError, match="thinking_budget must be >= 0"):
            AgentConfig(thinking_budget=-1)

    def test_probe_e1p_reset_n_boost_negative_raises(self):
        """reset_n_boost must be >= 0."""
        with pytest.raises(ValueError, match="reset_n_boost must be >= 0"):
            AgentConfig(reset_n_boost=-1)

    def test_probe_e1q_stall_epsilon_negative_raises(self):
        """stall_epsilon must be >= 0."""
        with pytest.raises(ValueError, match="stall_epsilon must be >= 0"):
            AgentConfig(stall_epsilon=-0.01)


# ===================================================================
# E2 — CLI flag override precedence
# ===================================================================


class TestE2CLIOverridePrecedence:
    """Probe: explicit CLI flags must override preset defaults."""

    def _make_args(self, **kwargs: Any) -> argparse.Namespace:
        """Build a Namespace that mimics parsed CLI args.

        Defaults match the argparse defaults (None for optional, False for booleans).
        """
        defaults = {
            "preset": None,
            "model": None,
            "iterations": None,
            "revisions": None,
            "confidence_threshold": None,
            "temperature_generator": None,
            "temperature_verifier": None,
            "temperature_reviser": None,
            "max_tokens": None,
            "thinking": False,
            "thinking_budget": None,
            "best_of_n": None,
            "tools": "sympy,numpy",
            "no_code": False,
            "no_balanced": False,
            "quiet": False,
            "json_output": False,
            "no_stall_reset": False,
            "stall_window": None,
            "stall_epsilon": None,
            "variant_b_model": None,
            "no_variant_b": False,
            "context_threshold": None,
            "resume": None,
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_probe_e2a_best_of_1_overrides_thorough_preset(self):
        """--best-of 1 --preset thorough => best_of_n=1 (not 3)."""
        from alethic.cli import _build_config

        args = self._make_args(preset="thorough", best_of_n=1)
        cfg = _build_config(args)
        assert cfg.best_of_n == 1

    def test_probe_e2b_best_of_5_overrides_quick_preset(self):
        """--best-of 5 --preset quick => best_of_n=5."""
        from alethic.cli import _build_config

        args = self._make_args(preset="quick", best_of_n=5)
        cfg = _build_config(args)
        assert cfg.best_of_n == 5

    def test_probe_e2c_no_variant_b_overrides_extreme_preset(self):
        """--no-variant-b --preset extreme => variant_b=None."""
        from alethic.cli import _build_config

        args = self._make_args(preset="extreme", no_variant_b=True)
        cfg = _build_config(args)
        assert cfg.variant_b is None

    def test_probe_e2d_no_variant_b_and_variant_b_model_conflict(self, capsys):
        """--no-variant-b --variant-b-model X => --no-variant-b wins (with warning)."""
        from alethic.cli import _build_config

        args = self._make_args(
            preset="thorough",
            no_variant_b=True,
            variant_b_model="claude-sonnet-4-6",
        )
        cfg = _build_config(args)
        assert cfg.variant_b is None
        captured = capsys.readouterr()
        assert "no-variant-b" in captured.err

    def test_probe_e2e_variant_b_model_overrides_preset(self):
        """--variant-b-model X --preset thorough => variant_b uses X."""
        from alethic.cli import _build_config

        args = self._make_args(preset="thorough", variant_b_model="claude-haiku-4-5-20251001")
        cfg = _build_config(args)
        assert cfg.variant_b == {"model": "claude-haiku-4-5-20251001"}

    def test_probe_e2f_iterations_override_preset(self):
        """--iterations 3 --preset extreme => max_iterations=3 (not 12)."""
        from alethic.cli import _build_config

        args = self._make_args(preset="extreme", iterations=3)
        cfg = _build_config(args)
        assert cfg.max_iterations == 3

    def test_probe_e2g_confidence_threshold_override(self):
        """--confidence-threshold 0.80 --preset thorough => 0.80 (not 0.95)."""
        from alethic.cli import _build_config

        args = self._make_args(preset="thorough", confidence_threshold=0.80)
        cfg = _build_config(args)
        assert cfg.confidence_threshold == 0.80

    def test_probe_e2h_no_stall_reset_overrides_preset(self):
        """--no-stall-reset --preset default => stall_reset=False."""
        from alethic.cli import _build_config

        args = self._make_args(preset="default", no_stall_reset=True)
        cfg = _build_config(args)
        assert cfg.stall_reset is False

    def test_probe_e2i_stall_window_override(self):
        """--stall-window 5 --preset default => stall_window=5 (not 2)."""
        from alethic.cli import _build_config

        args = self._make_args(preset="default", stall_window=5)
        cfg = _build_config(args)
        assert cfg.stall_window == 5

    def test_probe_e2j_stall_epsilon_override(self):
        """--stall-epsilon 0.1 --preset thorough => stall_epsilon=0.1 (not 0.02)."""
        from alethic.cli import _build_config

        args = self._make_args(preset="thorough", stall_epsilon=0.1)
        cfg = _build_config(args)
        assert cfg.stall_epsilon == 0.1

    def test_probe_e2k_model_override(self):
        """--model claude-sonnet-4-6 --preset default => model override."""
        from alethic.cli import _build_config

        args = self._make_args(preset="default", model="claude-sonnet-4-6")
        cfg = _build_config(args)
        assert cfg.model == "claude-sonnet-4-6"

    def test_probe_e2l_context_threshold_override(self):
        """--context-threshold 0.9 --preset extreme => 0.9 (not 0.75)."""
        from alethic.cli import _build_config

        args = self._make_args(preset="extreme", context_threshold=0.9)
        cfg = _build_config(args)
        assert cfg.context_threshold == 0.9

    def test_probe_e2m_thinking_flag_activates_thinking(self):
        """--thinking --preset default => extended_thinking=True."""
        from alethic.cli import _build_config

        args = self._make_args(preset="default", thinking=True)
        cfg = _build_config(args)
        assert cfg.extended_thinking is True

    def test_probe_e2n_thinking_budget_override(self):
        """--thinking-budget 5000 --preset thorough => thinking_budget=5000."""
        from alethic.cli import _build_config

        args = self._make_args(preset="thorough", thinking_budget=5000)
        cfg = _build_config(args)
        assert cfg.thinking_budget == 5000

    def test_probe_e2o_revisions_override(self):
        """--revisions 1 --preset extreme => max_revisions_per_cycle=1."""
        from alethic.cli import _build_config

        args = self._make_args(preset="extreme", revisions=1)
        cfg = _build_config(args)
        assert cfg.max_revisions_per_cycle == 1

    def test_probe_e2p_max_tokens_override(self):
        """--max-tokens 8192 --preset thorough => max_tokens=8192."""
        from alethic.cli import _build_config

        args = self._make_args(preset="thorough", max_tokens=8192)
        cfg = _build_config(args)
        assert cfg.max_tokens == 8192

    def test_probe_e2q_no_preset_uses_defaults_with_overrides(self):
        """Without --preset, explicit flags are applied to AgentConfig defaults."""
        from alethic.cli import _build_config

        args = self._make_args(iterations=10, best_of_n=3)
        cfg = _build_config(args)
        assert cfg.max_iterations == 10
        assert cfg.best_of_n == 3
        # Other fields should have AgentConfig defaults
        assert cfg.confidence_threshold == 0.90
        assert cfg.model == "claude-opus-4-6"

    def test_probe_e2r_preset_without_overrides_matches_preset(self):
        """--preset thorough alone should exactly match the preset values."""
        from alethic.cli import _build_config

        args = self._make_args(preset="thorough")
        cfg = _build_config(args)
        assert cfg.max_iterations == 8
        assert cfg.max_revisions_per_cycle == 5
        assert cfg.confidence_threshold == 0.95
        assert cfg.extended_thinking is True
        assert cfg.thinking_budget == 15000
        assert cfg.best_of_n == 3
        assert cfg.stall_window == 3
        assert cfg.stall_epsilon == 0.02
        assert cfg.stall_reset is True
        assert cfg.reset_n_boost == 1
        assert cfg.context_threshold == 0.8
        assert cfg.variant_b == {"model": "claude-sonnet-4-6"}
        assert cfg.adversarial_self_correction is True
        assert cfg.adaptive_compute is True


# ===================================================================
# E3 — from_preset() with unknown preset
# ===================================================================


class TestE3UnknownPreset:
    """Probe: from_preset() must raise ValueError for unknown presets."""

    def test_probe_e3a_agent_config_unknown_preset(self):
        """AgentConfig.from_preset('nonexistent') must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown preset 'nonexistent'"):
            AgentConfig.from_preset("nonexistent")

    def test_probe_e3b_verifier_config_unknown_preset(self):
        """VerifierConfig.from_preset('nonexistent') must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown preset 'nonexistent'"):
            VerifierConfig.from_preset("nonexistent")

    def test_probe_e3c_empty_string_preset(self):
        """Empty string is not a valid preset name."""
        with pytest.raises(ValueError, match="Unknown preset ''"):
            AgentConfig.from_preset("")

    def test_probe_e3d_case_sensitive_preset(self):
        """Preset names are case-sensitive: 'Quick' != 'quick'."""
        with pytest.raises(ValueError, match="Unknown preset 'Quick'"):
            AgentConfig.from_preset("Quick")


# ===================================================================
# E4 — VALID_TOOL_GUIDANCE consistency
# ===================================================================


class TestE4ValidToolGuidance:
    """Probe: VALID_TOOL_GUIDANCE is used consistently in both configs."""

    def test_probe_e4a_valid_tool_guidance_contains_expected(self):
        """VALID_TOOL_GUIDANCE should contain exactly {sympy, numpy, scipy, matplotlib}."""
        assert VALID_TOOL_GUIDANCE == frozenset({"sympy", "numpy", "scipy", "matplotlib"})

    def test_probe_e4b_agent_config_rejects_invalid_tool(self):
        """AgentConfig rejects tool_guidance values not in VALID_TOOL_GUIDANCE."""
        with pytest.raises(ValueError, match="Unknown tool_guidance"):
            AgentConfig(tool_guidance=frozenset({"sympy", "wolfram"}))

    def test_probe_e4c_verifier_config_rejects_invalid_tool(self):
        """VerifierConfig rejects tool_guidance values not in VALID_TOOL_GUIDANCE."""
        with pytest.raises(ValueError, match="Unknown tool_guidance"):
            VerifierConfig(tool_guidance=frozenset({"numpy", "wolfram"}))

    def test_probe_e4d_agent_config_accepts_valid_tools(self):
        """AgentConfig accepts any subset of VALID_TOOL_GUIDANCE."""
        cfg = AgentConfig(tool_guidance=frozenset({"sympy"}))
        assert cfg.tool_guidance == frozenset({"sympy"})

        cfg2 = AgentConfig(tool_guidance=VALID_TOOL_GUIDANCE)
        assert cfg2.tool_guidance == VALID_TOOL_GUIDANCE

    def test_probe_e4e_verifier_config_accepts_valid_tools(self):
        """VerifierConfig accepts any subset of VALID_TOOL_GUIDANCE."""
        cfg = VerifierConfig(tool_guidance=frozenset({"numpy", "scipy"}))
        assert cfg.tool_guidance == frozenset({"numpy", "scipy"})

    def test_probe_e4f_empty_tool_guidance_accepted(self):
        """Empty tool_guidance (--tools none) is valid for both configs."""
        cfg_a = AgentConfig(tool_guidance=frozenset())
        assert cfg_a.tool_guidance == frozenset()

        cfg_v = VerifierConfig(tool_guidance=frozenset())
        assert cfg_v.tool_guidance == frozenset()

    def test_probe_e4g_agent_default_tool_guidance(self):
        """AgentConfig default tool_guidance is {sympy, numpy}."""
        cfg = AgentConfig()
        assert cfg.tool_guidance == frozenset({"sympy", "numpy"})

    def test_probe_e4h_verifier_default_tool_guidance(self):
        """VerifierConfig default tool_guidance is {sympy, numpy, scipy, matplotlib}."""
        cfg = VerifierConfig()
        assert cfg.tool_guidance == frozenset({"sympy", "numpy", "scipy", "matplotlib"})


# ===================================================================
# E5 — Preset field completeness and type correctness
# ===================================================================


class TestE5PresetCompleteness:
    """Probe: all preset fields must be valid AgentConfig/VerifierConfig fields with correct types."""

    def test_probe_e5a_agent_preset_keys_are_valid_fields(self):
        """Every key in AgentConfig.PRESETS must be a valid AgentConfig field name."""
        valid_fields = {f.name for f in dataclass_fields(AgentConfig)}
        for preset_name, preset_dict in AgentConfig.PRESETS.items():
            for key in preset_dict:
                assert key in valid_fields, (
                    f"AgentConfig.PRESETS['{preset_name}'] contains unknown field '{key}'. "
                    f"Valid fields: {sorted(valid_fields)}"
                )

    def test_probe_e5b_verifier_preset_keys_are_valid_fields(self):
        """Every key in VerifierConfig.PRESETS must be a valid VerifierConfig field name."""
        valid_fields = {f.name for f in dataclass_fields(VerifierConfig)}
        for preset_name, preset_dict in VerifierConfig.PRESETS.items():
            for key in preset_dict:
                assert key in valid_fields, (
                    f"VerifierConfig.PRESETS['{preset_name}'] contains unknown field '{key}'. "
                    f"Valid fields: {sorted(valid_fields)}"
                )

    def test_probe_e5c_agent_presets_constructible(self):
        """Every AgentConfig preset must produce a valid AgentConfig."""
        for preset_name in AgentConfig.PRESETS:
            cfg = AgentConfig.from_preset(preset_name)
            assert isinstance(cfg, AgentConfig), f"Preset '{preset_name}' failed construction"

    def test_probe_e5d_verifier_presets_constructible(self):
        """Every VerifierConfig preset must produce a valid VerifierConfig."""
        for preset_name in VerifierConfig.PRESETS:
            cfg = VerifierConfig.from_preset(preset_name)
            assert isinstance(cfg, VerifierConfig), f"Preset '{preset_name}' failed construction"

    def test_probe_e5e_agent_preset_same_keys(self):
        """Agent presets should have the same set of presets: quick, default, thorough, extreme."""
        expected = {"quick", "default", "thorough", "extreme"}
        assert set(AgentConfig.PRESETS.keys()) == expected

    def test_probe_e5f_verifier_preset_same_keys(self):
        """Verifier presets should have the same set of presets."""
        expected = {"quick", "default", "thorough", "extreme"}
        assert set(VerifierConfig.PRESETS.keys()) == expected

    def test_probe_e5g_agent_preset_types_match_field_types(self):
        """Preset values should match the field types of AgentConfig."""
        field_types = {}
        for f in dataclass_fields(AgentConfig):
            # Handle optional/union types by extracting the base type
            # For simple cases, this is just f.type evaluated at runtime
            field_types[f.name] = f.type

        for preset_name, preset_dict in AgentConfig.PRESETS.items():
            for key, value in preset_dict.items():
                # Check that the value type is plausible
                if key == "variant_b":
                    assert value is None or isinstance(value, dict), (
                        f"Preset '{preset_name}' field '{key}' has value {value!r} "
                        f"of type {type(value).__name__}, expected dict or None"
                    )
                elif key in ("max_iterations", "max_revisions_per_cycle", "max_tokens",
                             "best_of_n", "stall_window", "reset_n_boost", "thinking_budget"):
                    assert isinstance(value, int), (
                        f"Preset '{preset_name}' field '{key}' has value {value!r} "
                        f"of type {type(value).__name__}, expected int"
                    )
                elif key in ("confidence_threshold", "stall_epsilon", "context_threshold"):
                    assert isinstance(value, (int, float)), (
                        f"Preset '{preset_name}' field '{key}' has value {value!r} "
                        f"of type {type(value).__name__}, expected float"
                    )
                elif key in ("extended_thinking", "stall_reset", "adversarial_self_correction",
                             "adaptive_compute", "adaptive_revision_budget"):
                    assert isinstance(value, bool), (
                        f"Preset '{preset_name}' field '{key}' has value {value!r} "
                        f"of type {type(value).__name__}, expected bool"
                    )

    def test_probe_e5h_agent_preset_reasonable_values(self):
        """Preset values should be within reasonable ranges."""
        for preset_name, preset_dict in AgentConfig.PRESETS.items():
            if "max_iterations" in preset_dict:
                assert 1 <= preset_dict["max_iterations"] <= 100, (
                    f"Preset '{preset_name}' max_iterations={preset_dict['max_iterations']} unreasonable"
                )
            if "confidence_threshold" in preset_dict:
                assert 0.0 <= preset_dict["confidence_threshold"] <= 1.0, (
                    f"Preset '{preset_name}' confidence_threshold={preset_dict['confidence_threshold']} OOB"
                )
            if "best_of_n" in preset_dict:
                assert 1 <= preset_dict["best_of_n"] <= 20, (
                    f"Preset '{preset_name}' best_of_n={preset_dict['best_of_n']} unreasonable"
                )
            if "stall_window" in preset_dict:
                assert 1 <= preset_dict["stall_window"] <= 20, (
                    f"Preset '{preset_name}' stall_window={preset_dict['stall_window']} unreasonable"
                )

    def test_probe_e5i_preset_difficulty_monotonic(self):
        """Presets should be monotonically increasing in difficulty: quick < default < thorough < extreme."""
        ordered = ["quick", "default", "thorough", "extreme"]
        cfgs = [AgentConfig.from_preset(name) for name in ordered]

        for i in range(len(cfgs) - 1):
            # max_iterations should be non-decreasing
            assert cfgs[i].max_iterations <= cfgs[i + 1].max_iterations, (
                f"max_iterations: {ordered[i]} ({cfgs[i].max_iterations}) > "
                f"{ordered[i+1]} ({cfgs[i+1].max_iterations})"
            )
            # confidence_threshold should be non-decreasing
            assert cfgs[i].confidence_threshold <= cfgs[i + 1].confidence_threshold, (
                f"confidence_threshold: {ordered[i]} ({cfgs[i].confidence_threshold}) > "
                f"{ordered[i+1]} ({cfgs[i+1].confidence_threshold})"
            )
            # best_of_n should be non-decreasing
            assert cfgs[i].best_of_n <= cfgs[i + 1].best_of_n, (
                f"best_of_n: {ordered[i]} ({cfgs[i].best_of_n}) > "
                f"{ordered[i+1]} ({cfgs[i+1].best_of_n})"
            )
            # max_tokens should be non-decreasing
            assert cfgs[i].max_tokens <= cfgs[i + 1].max_tokens, (
                f"max_tokens: {ordered[i]} ({cfgs[i].max_tokens}) > "
                f"{ordered[i+1]} ({cfgs[i+1].max_tokens})"
            )


# ===================================================================
# E6 — Variant-B key validation
# ===================================================================


class TestE6VariantBValidation:
    """Probe: variant_b keys must be valid AgentConfig field names."""

    def test_probe_e6a_valid_variant_b_keys(self):
        """variant_b={'model': 'X'} is valid."""
        cfg = AgentConfig(variant_b={"model": "claude-sonnet-4-6"})
        assert cfg.variant_b == {"model": "claude-sonnet-4-6"}

    def test_probe_e6b_invalid_variant_b_key_raises(self):
        """variant_b={'nonexistent_field': 'X'} must raise ValueError."""
        with pytest.raises(ValueError, match="variant_b contains unknown keys"):
            AgentConfig(variant_b={"nonexistent_field": "value"})

    def test_probe_e6c_variant_b_with_multiple_valid_keys(self):
        """variant_b with multiple valid keys is accepted."""
        cfg = AgentConfig(variant_b={"model": "claude-sonnet-4-6", "temperature_generator": 0.8})
        assert cfg.variant_b["model"] == "claude-sonnet-4-6"
        assert cfg.variant_b["temperature_generator"] == 0.8

    def test_probe_e6d_variant_b_mixed_valid_invalid_raises(self):
        """variant_b with one invalid key (among valid ones) must raise."""
        with pytest.raises(ValueError, match="variant_b contains unknown keys"):
            AgentConfig(variant_b={"model": "claude-sonnet-4-6", "bogus": True})

    def test_probe_e6e_variant_b_empty_dict_ok(self):
        """variant_b={} is technically valid (no overrides, no-op)."""
        cfg = AgentConfig(variant_b={})
        assert cfg.variant_b == {}

    def test_probe_e6f_build_variant_b_config(self):
        """build_variant_b_config() produces a valid config with overrides applied."""
        cfg = AgentConfig(
            model="claude-opus-4-6",
            temperature_generator=1.0,
            variant_b={"model": "claude-sonnet-4-6", "temperature_generator": 0.5},
        )
        b = cfg.build_variant_b_config()
        assert b.model == "claude-sonnet-4-6"
        assert b.temperature_generator == 0.5
        assert b.variant_b is None  # variant_b is cleared on variant B config

    def test_probe_e6g_build_variant_b_none_raises(self):
        """build_variant_b_config() with variant_b=None must raise ValueError."""
        cfg = AgentConfig(variant_b=None)
        with pytest.raises(ValueError, match="variant_b is None"):
            cfg.build_variant_b_config()


# ===================================================================
# E7 — VerifierConfig from_preset() mirrors AgentConfig behavior
# ===================================================================


class TestE7VerifierPresets:
    """Probe: VerifierConfig.from_preset() follows the same pattern as AgentConfig."""

    def test_probe_e7a_from_preset_with_override(self):
        """VerifierConfig.from_preset('quick', num_verifiers=5) => K=5."""
        cfg = VerifierConfig.from_preset("quick", num_verifiers=5)
        assert cfg.num_verifiers == 5

    def test_probe_e7b_from_preset_unknown_raises(self):
        """VerifierConfig.from_preset('bogus') must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown preset"):
            VerifierConfig.from_preset("bogus")

    def test_probe_e7c_num_verifiers_zero_raises(self):
        """num_verifiers must be >= 1."""
        with pytest.raises(ValueError, match="num_verifiers must be >= 1"):
            VerifierConfig(num_verifiers=0)

    def test_probe_e7d_verifier_quick_preset_values(self):
        """Quick preset should have K=2, no extended thinking."""
        cfg = VerifierConfig.from_preset("quick")
        assert cfg.num_verifiers == 2
        assert cfg.extended_thinking is False

    def test_probe_e7e_verifier_extreme_preset_values(self):
        """Extreme preset should have K=7, extended thinking, large budget."""
        cfg = VerifierConfig.from_preset("extreme")
        assert cfg.num_verifiers == 7
        assert cfg.extended_thinking is True
        assert cfg.thinking_budget == 40000
        assert cfg.max_tokens == 65536

    def test_probe_e7f_verifier_preset_monotonic_k(self):
        """Verifier preset K should be monotonically non-decreasing."""
        ordered = ["quick", "default", "thorough", "extreme"]
        cfgs = [VerifierConfig.from_preset(name) for name in ordered]
        for i in range(len(cfgs) - 1):
            assert cfgs[i].num_verifiers <= cfgs[i + 1].num_verifiers, (
                f"num_verifiers: {ordered[i]} ({cfgs[i].num_verifiers}) > "
                f"{ordered[i+1]} ({cfgs[i+1].num_verifiers})"
            )

    def test_probe_e7g_verifier_temperature_validation(self):
        """VerifierConfig should reject negative temperature."""
        with pytest.raises(ValueError, match="temperature must be >= 0"):
            VerifierConfig(temperature=-0.1)

    def test_probe_e7h_verifier_max_tokens_validation(self):
        """VerifierConfig should reject max_tokens=0."""
        with pytest.raises(ValueError, match="max_tokens must be >= 1"):
            VerifierConfig(max_tokens=0)

    def test_probe_e7i_verifier_thinking_budget_validation(self):
        """VerifierConfig should reject negative thinking_budget."""
        with pytest.raises(ValueError, match="thinking_budget must be >= 0"):
            VerifierConfig(thinking_budget=-1)


# ===================================================================
# E8 — CLI _build_config edge cases
# ===================================================================


class TestE8CLIBuildConfig:
    """Probe: edge cases in the CLI config builder."""

    def _make_args(self, **kwargs: Any) -> argparse.Namespace:
        """Build a Namespace with CLI defaults."""
        defaults = {
            "preset": None,
            "model": None,
            "iterations": None,
            "revisions": None,
            "confidence_threshold": None,
            "temperature_generator": None,
            "temperature_verifier": None,
            "temperature_reviser": None,
            "max_tokens": None,
            "thinking": False,
            "thinking_budget": None,
            "best_of_n": None,
            "tools": "sympy,numpy",
            "no_code": False,
            "no_balanced": False,
            "quiet": False,
            "json_output": False,
            "no_stall_reset": False,
            "stall_window": None,
            "stall_epsilon": None,
            "variant_b_model": None,
            "no_variant_b": False,
            "context_threshold": None,
            "resume": None,
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_probe_e8a_tools_none_produces_empty_frozenset(self):
        """--tools none => tool_guidance=frozenset()."""
        from alethic.cli import _build_config

        args = self._make_args(tools="none")
        cfg = _build_config(args)
        assert cfg.tool_guidance == frozenset()

    def test_probe_e8b_tools_sympy_only(self):
        """--tools sympy => tool_guidance=frozenset({'sympy'})."""
        from alethic.cli import _build_config

        args = self._make_args(tools="sympy")
        cfg = _build_config(args)
        assert cfg.tool_guidance == frozenset({"sympy"})

    def test_probe_e8c_tools_invalid_raises(self):
        """--tools bogus => ValueError from AgentConfig validation."""
        from alethic.cli import _build_config

        args = self._make_args(tools="bogus")
        with pytest.raises(ValueError, match="Unknown tool_guidance"):
            _build_config(args)

    def test_probe_e8d_thinking_auto_bumps_max_tokens(self):
        """--thinking without --max-tokens should auto-bump max_tokens when needed.

        The auto-bump logic: if extended_thinking is on and max_tokens not explicitly set,
        ensure max_tokens >= thinking_budget + 8192.
        """
        from alethic.cli import _build_config

        # Default thinking_budget=10000, so min_tokens = 10000 + 8192 = 18192
        # Default max_tokens=16384 < 18192, so it should be bumped
        args = self._make_args(thinking=True)
        cfg = _build_config(args)
        assert cfg.extended_thinking is True
        assert cfg.max_tokens >= cfg.thinking_budget + 8192

    def test_probe_e8e_thinking_no_bump_when_max_tokens_explicit(self):
        """--thinking --max-tokens 8192 should NOT auto-bump (explicit takes priority)."""
        from alethic.cli import _build_config

        args = self._make_args(thinking=True, max_tokens=8192)
        cfg = _build_config(args)
        assert cfg.max_tokens == 8192

    def test_probe_e8f_preset_thinking_auto_bumps(self):
        """--thinking --preset default should auto-bump if preset max_tokens too low.

        default preset: max_tokens=16384, thinking_budget=10000 (default)
        min_tokens = 10000 + 8192 = 18192 > 16384 => bump
        """
        from alethic.cli import _build_config

        args = self._make_args(thinking=True, preset="default")
        cfg = _build_config(args)
        assert cfg.max_tokens >= 18192

    def test_probe_e8g_no_code_disables_code_execution(self):
        """--no-code => enable_code_execution=False."""
        from alethic.cli import _build_config

        args = self._make_args(no_code=True)
        cfg = _build_config(args)
        assert cfg.enable_code_execution is False

    def test_probe_e8h_quiet_disables_verbose(self):
        """--quiet => verbose=False."""
        from alethic.cli import _build_config

        args = self._make_args(quiet=True)
        cfg = _build_config(args)
        assert cfg.verbose is False

    def test_probe_e8i_tools_always_overrides_even_without_flag(self):
        """The --tools arg has a default of 'sympy,numpy', so it ALWAYS provides
        a tool_guidance override. This means preset tool_guidance values would be
        overridden even when the user didn't explicitly pass --tools.

        Currently, no AgentConfig preset sets tool_guidance, so this isn't a
        functional bug. We document this as a design quirk.
        """
        from alethic.cli import _build_config

        # If a preset were to set tool_guidance to frozenset({"scipy"}),
        # the CLI default --tools=sympy,numpy would override it.
        # Currently no preset does this, so we just verify the behavior.
        args = self._make_args(preset="thorough")
        cfg = _build_config(args)
        # tool_guidance comes from --tools default, not from preset
        assert cfg.tool_guidance == frozenset({"sympy", "numpy"})

    def test_probe_e8j_verifier_config_tools_expansion(self):
        """For verify/check, default --tools=sympy,numpy should expand to the
        full set {sympy, numpy, scipy, matplotlib}.
        """
        from alethic.cli import _build_verifier_config

        args = self._make_args(tools="sympy,numpy")
        vcfg = _build_verifier_config(args)
        assert vcfg.tool_guidance == frozenset({"sympy", "numpy", "scipy", "matplotlib"})

    def test_probe_e8k_verifier_config_tools_explicit_no_expansion(self):
        """For verify/check, --tools=sympy should NOT expand (user was explicit)."""
        from alethic.cli import _build_verifier_config

        args = self._make_args(tools="sympy")
        vcfg = _build_verifier_config(args)
        assert vcfg.tool_guidance == frozenset({"sympy"})

    def test_probe_e8l_verifier_config_tools_none(self):
        """For verify/check, --tools=none => empty frozenset."""
        from alethic.cli import _build_verifier_config

        args = self._make_args(tools="none")
        vcfg = _build_verifier_config(args)
        assert vcfg.tool_guidance == frozenset()

    def test_probe_e8m_verifier_config_preset_override(self):
        """For verify/check, --verifiers 10 --preset thorough => K=10."""
        from alethic.cli import _build_verifier_config

        args = self._make_args(preset="thorough", verifiers=10)
        vcfg = _build_verifier_config(args)
        assert vcfg.num_verifiers == 10


# ===================================================================
# E9 — _detect_subcommand edge cases
# ===================================================================


class TestE9SubcommandDetection:
    """Probe: _detect_subcommand handles flag-value pairs correctly."""

    def test_probe_e9a_solve_detected(self):
        """solve as first positional arg is detected."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["solve", "problem text"])
        assert cmd == "solve"
        assert rest == ["problem text"]

    def test_probe_e9b_derive_detected(self):
        """derive as first positional arg is detected."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["derive", "problem text"])
        assert cmd == "derive"
        assert rest == ["problem text"]

    def test_probe_e9c_preset_derive_not_confused(self):
        """--preset derive should NOT be confused with 'derive' subcommand."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["--preset", "derive", "problem text"])
        # "derive" is consumed as the value of --preset, not as a subcommand
        assert cmd is None
        assert rest == ["--preset", "derive", "problem text"]

    def test_probe_e9d_preset_equals_syntax(self):
        """--preset=derive should not be confused as subcommand."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["--preset=derive", "problem text"])
        assert cmd is None
        assert rest == ["--preset=derive", "problem text"]

    def test_probe_e9e_verify_detected(self):
        """verify as subcommand is detected."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["verify", "solution.md", "--problem-text", "x"])
        assert cmd == "verify"

    def test_probe_e9f_check_detected(self):
        """check as subcommand is detected."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["check", "solution.md"])
        assert cmd == "check"

    def test_probe_e9g_eval_detected(self):
        """eval as subcommand is detected."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["eval", "run", "bench.json"])
        assert cmd == "eval"

    def test_probe_e9h_no_subcommand(self):
        """Regular problem text without subcommand returns None."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["Prove that 2 is irrational"])
        assert cmd is None
        assert rest == ["Prove that 2 is irrational"]

    def test_probe_e9i_flags_before_subcommand(self):
        """Flags before subcommand should work: --preset quick solve 'problem'."""
        from alethic.cli import _detect_subcommand

        cmd, rest = _detect_subcommand(["--preset", "quick", "solve", "problem"])
        assert cmd == "solve"
        assert rest == ["--preset", "quick", "problem"]


# ===================================================================
# E10 — AgentConfig frozen invariant
# ===================================================================


class TestE10FrozenInvariant:
    """Probe: AgentConfig is frozen (immutable after creation)."""

    def test_probe_e10a_cannot_modify_field(self):
        """AgentConfig fields cannot be modified after creation."""
        cfg = AgentConfig()
        with pytest.raises(AttributeError):
            cfg.max_iterations = 99  # type: ignore[misc]

    def test_probe_e10b_cannot_modify_verifier_field(self):
        """VerifierConfig fields cannot be modified after creation."""
        cfg = VerifierConfig()
        with pytest.raises(AttributeError):
            cfg.num_verifiers = 99  # type: ignore[misc]


# ===================================================================
# E11 — _FLAG_TO_CONFIG completeness
# ===================================================================


class TestE11FlagMapping:
    """Probe: _FLAG_TO_CONFIG covers all expected CLI-to-config mappings."""

    def test_probe_e11a_all_mapped_values_are_valid_fields(self):
        """Every config_name in _FLAG_TO_CONFIG must be a valid AgentConfig field."""
        from alethic.cli import _FLAG_TO_CONFIG

        valid_fields = {f.name for f in dataclass_fields(AgentConfig)}
        for arg_name, config_name in _FLAG_TO_CONFIG.items():
            assert config_name in valid_fields, (
                f"_FLAG_TO_CONFIG['{arg_name}'] = '{config_name}' is not a valid AgentConfig field"
            )

    def test_probe_e11b_verifier_flag_map_valid(self):
        """Every config_name in _VERIFIER_FLAG_TO_CONFIG must be a valid VerifierConfig field."""
        from alethic.cli import _VERIFIER_FLAG_TO_CONFIG

        valid_fields = {f.name for f in dataclass_fields(VerifierConfig)}
        for arg_name, config_name in _VERIFIER_FLAG_TO_CONFIG.items():
            assert config_name in valid_fields, (
                f"_VERIFIER_FLAG_TO_CONFIG['{arg_name}'] = '{config_name}' "
                f"is not a valid VerifierConfig field"
            )
