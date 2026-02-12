"""
Alethic — A mathematical reasoning agent inspired by Google DeepMind's Aletheia.

Built on Claude (Opus 4.6) with a Generate → Verify → Revise architecture
that decouples reasoning from verification for robust mathematical problem solving.

Architecture:
    Generator  →  produces candidate solutions with extended reasoning
    Verifier   →  independently evaluates solutions (without thinking traces)
    Reviser    →  incorporates feedback to improve solutions

Key design principles:
    - Decoupled verification: Verifier sees only final output, not intermediate reasoning
    - Strategic failure admission: Agent can declare "unsolved" rather than hallucinate
    - Tool integration: Python code execution for computational verification
    - Configurable iteration limits for compute budget control

Usage:
    from alethic import MathAgent, AgentConfig

    agent = MathAgent()  # uses ANTHROPIC_API_KEY env var
    result = agent.solve("Prove that sqrt(2) is irrational.")
    print(result)
"""

from alethic.models import (
    AgentConfig,
    AgentResult,
    Revision,
    Solution,
    VerificationResult,
    Verdict,
)
from alethic.agent import MathAgent

__all__ = [
    "MathAgent",
    "AgentConfig",
    "AgentResult",
    "Solution",
    "VerificationResult",
    "Revision",
    "Verdict",
]

__version__ = "0.1.0"
