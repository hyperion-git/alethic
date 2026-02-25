"""
Alethic — A reasoning agent for mathematics and physics inspired by Google
DeepMind's Aletheia.

Built on Claude (Opus 4.6) with a Generate → Verify → Revise architecture
that decouples reasoning from verification for robust mathematical proofs
and physics derivations.

Architecture:
    Generator  →  produces candidate solutions with extended reasoning
    Verifier   →  independently evaluates solutions (without thinking traces)
    Reviser    →  incorporates feedback to improve solutions

Key design principles:
    - Decoupled verification: Verifier sees only final output, not intermediate reasoning
    - Domain-neutral orchestrator: MathAgent and PhysicsAgent share the same loop
    - Strategic failure admission: Agent can declare "unsolved" rather than hallucinate
    - Tool integration: Python code execution for computational verification
    - Configurable iteration limits for compute budget control

Usage:
    from alethic import MathAgent, PhysicsAgent, AgentConfig

    agent = MathAgent()  # uses ANTHROPIC_API_KEY env var
    result = agent.solve("Prove that sqrt(2) is irrational.")

    agent = PhysicsAgent()
    result = agent.solve("Derive the energy levels of the quantum harmonic oscillator.")
"""

from alethic.agent import MathAgent
from alethic.models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    ConsensusIssue,
    ConsensusResult,
    EventType,
    Issue,
    IssueSeverity,
    Revision,
    SectionConfidence,
    Solution,
    Verdict,
    VerificationResult,
    VerifierConfig,
)
from alethic.physics_agent import PhysicsAgent
from alethic.verifier_agent import CheckerAgent, VerifierAgent

__all__ = [
    "CheckerAgent",
    "MathAgent",
    "PhysicsAgent",
    "VerifierAgent",
    "AgentConfig",
    "AgentEvent",
    "AgentResult",
    "ConsensusIssue",
    "ConsensusResult",
    "EventType",
    "Issue",
    "IssueSeverity",
    "Revision",
    "SectionConfidence",
    "Solution",
    "Verdict",
    "VerificationResult",
    "VerifierConfig",
]

__version__ = "2.0.0"
