"""Domain auto-detection for verify/check commands."""
from __future__ import annotations

import json
import re
from importlib import resources

_TIER_WEIGHTS = {"strong": 3, "moderate": 2, "weak": 1}
_KEYWORDS: dict | None = None


def _load_keywords() -> dict:
    global _KEYWORDS
    if _KEYWORDS is None:
        ref = resources.files("alethic.data").joinpath("domain-keywords.json")
        _KEYWORDS = json.loads(ref.read_text(encoding="utf-8"))
    return _KEYWORDS


def detect_domain(text: str, *, override: str | None = None) -> str:
    """Detect whether text is math or physics.

    Args:
        text: The solution/derivation text to classify.
        override: If set, skip detection and return this value.

    Returns:
        "math" or "physics".
    """
    if override is not None:
        return override

    if not text.strip():
        return "math"

    keywords = _load_keywords()
    text_lower = text.lower()

    scores: dict[str, float] = {}
    for domain, tiers in keywords.items():
        score = 0.0
        for tier_name, terms in tiers.items():
            weight = _TIER_WEIGHTS.get(tier_name, 1)
            for term in terms:
                # Word-boundary match, case-insensitive
                if re.search(r"\b" + re.escape(term.lower()) + r"\b", text_lower):
                    score += weight
        scores[domain] = score

    if scores.get("physics", 0) > scores.get("math", 0):
        return "physics"
    return "math"  # tie or math wins -> default to math
