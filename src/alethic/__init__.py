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

from alethic.agent import MathAgent, rank_candidates
from alethic.autopsy import generate_autopsy
from alethic.calibration import calibrate
from alethic.error_taxonomy import classify_errors
from alethic.exceptions import (
    AlethicError,
    CheckpointError,
    ContextExhaustedError,
    TruncatedResponseError,
)
from alethic.models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    AtomConfidence,
    BreakerVerdict,
    ConsensusIssue,
    ConsensusResult,
    EventType,
    Issue,
    IssueSeverity,
    Revision,
    SectionConfidence,
    Solution,
    TokenLedger,
    Verdict,
    VerificationResult,
    VerifierConfig,
)
from alethic.oracle_router import OracleRouter, RoutingDecision
from alethic.physics_agent import PhysicsAgent
from alethic.verifier_agent import CheckerAgent, VerifierAgent

__all__ = [
    "AgentConfig",
    "AgentEvent",
    "AgentResult",
    "AlethicError",
    "AtomConfidence",
    "BreakerVerdict",
    "calibrate",
    "CheckerAgent",
    "CheckpointError",
    "classify_errors",
    "ConsensusIssue",
    "ConsensusResult",
    "ContextExhaustedError",
    "EventType",
    "Issue",
    "IssueSeverity",
    "MathAgent",
    "OracleRouter",
    "PhysicsAgent",
    "rank_candidates",
    "RoutingDecision",
    "Revision",
    "SectionConfidence",
    "Solution",
    "TokenLedger",
    "TruncatedResponseError",
    "Verdict",
    "VerificationResult",
    "VerifierAgent",
    "VerifierConfig",
    "generate_autopsy",
]

__version__ = "3.7.0"
