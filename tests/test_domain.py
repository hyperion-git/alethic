"""Tests for domain auto-detection."""

from __future__ import annotations

from alethic.domain import detect_domain


class TestDetectDomain:
    def test_physics_strong_signal(self):
        text = "We begin with the Hamiltonian H = p²/2m + V(x) and solve the Schrödinger equation."
        assert detect_domain(text) == "physics"

    def test_math_strong_signal(self):
        text = "Theorem: For all primes p, Fermat's little theorem states a^p ≡ a (mod p). Proof by induction."
        assert detect_domain(text) == "math"

    def test_ambiguous_defaults_to_math(self):
        text = "Consider the function f(x) = x² + 1."
        assert detect_domain(text) == "math"

    def test_empty_defaults_to_math(self):
        assert detect_domain("") == "math"

    def test_physics_moderate_signals(self):
        text = "The energy of the system is conserved. The momentum transfer during the collision is calculated using force and impulse."
        assert detect_domain(text) == "physics"

    def test_math_moderate_signals(self):
        text = "The polynomial has degree 5. By the fundamental theorem of algebra, it has 5 roots counting multiplicity. We check convergence of the series."
        assert detect_domain(text) == "math"

    def test_override_respected(self):
        """detect_domain with explicit override should return that override."""
        text = "The Hamiltonian is H = T + V"  # physics signal
        assert detect_domain(text, override="math") == "math"
        assert detect_domain(text, override="physics") == "physics"
