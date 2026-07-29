#!/usr/bin/env python3
"""Quick probe: run one problem through MathAgent and print raw events."""
from __future__ import annotations
import logging, os, sys

or_key = os.environ.get("OPENROUTER_API_KEY")
if not or_key:
    print("ERROR: OPENROUTER_API_KEY required", file=sys.stderr)
    sys.exit(1)

model = sys.argv[1] if len(sys.argv) > 1 else "stepfun/step-3.5-flash:free"

from alethic.client_factory import set_client_factory
from alethic.openrouter import OpenRouterClient
set_client_factory(lambda api_key: OpenRouterClient(api_key=or_key, model=model))

# Enable debug logging to see raw API responses
logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")

from alethic import AgentConfig, MathAgent

print(f"\n=== Probing {model} ===\n")

config = AgentConfig.from_preset("quick",
    model=model,
    max_iterations=1,
    best_of_n=1,
    variant_b=None,
    adversarial_breaker=False,
)
agent = MathAgent(api_key=or_key, config=config)
result = agent.solve("Prove that sqrt(2) is irrational.", create_session=False)

print(f"\n{'='*60}")
print(f"Verdict: {result.verdict.value}")
print(f"Confidence: {result.confidence}")
print(f"\nEvents:")
for e in result.events:
    etype = e.type.value if hasattr(e.type, "value") else str(e.type)
    print(f"  {etype}: {dict(e.data)}")
