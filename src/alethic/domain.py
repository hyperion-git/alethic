"""Domain auto-detection for verify/check commands."""

from __future__ import annotations

import json
import re
from importlib import resources

_TIER_WEIGHTS = {"strong": 3, "moderate": 2, "weak": 1}

# Cached compiled patterns: list of (domain, weight, compiled_regex) per keyword.
_PATTERNS: list[tuple[str, int, re.Pattern[str]]] | None = None


def _load_patterns() -> list[tuple[str, int, re.Pattern[str]]]:
    """Load domain keywords and compile word-boundary regexes (once)."""
    global _PATTERNS
    if _PATTERNS is not None:
        return _PATTERNS

    ref = resources.files("alethic.data").joinpath("domain-keywords.json")
    keywords = json.loads(ref.read_text(encoding="utf-8"))

    patterns: list[tuple[str, int, re.Pattern[str]]] = []
    for domain, tiers in keywords.items():
        for tier_name, terms in tiers.items():
            weight = _TIER_WEIGHTS.get(tier_name, 1)
            for term in terms:
                pattern = re.compile(r"\b" + re.escape(term.lower()) + r"\b")
                patterns.append((domain, weight, pattern))
    _PATTERNS = patterns
    return _PATTERNS


def detect_domain(text: str, *, override: str | None = None) -> str:
    """Detect whether text is math or physics.

    Args:
        text: The solution/derivation text to classify.
        override: If set, skip detection and return this value.

    Returns:
        "math" or "physics".
    """
    if override is not None:
        if override not in ("math", "physics"):
            raise ValueError(f"override must be 'math' or 'physics', got {override!r}")
        return override

    if not text.strip():
        return "math"

    text_lower = text.lower()
    scores: dict[str, float] = {"math": 0.0, "physics": 0.0}
    for domain, weight, pattern in _load_patterns():
        if pattern.search(text_lower):
            scores[domain] += weight

    if scores["physics"] > scores["math"]:
        return "physics"
    return "math"  # tie or math wins -> default to math
