#!/usr/bin/env python3
"""Smoke-test for PR #9 (physics-intern patches) via NVIDIA NIM.

Verifies the four new patches fire end-to-end against a non-Claude model:
  1. CHECKS PERFORMED block in verifier output    (patch d93c076)
  2. ISSUE TRIAGE block in reviser output         (patch a821646)
  3. Saturation awareness injection on repeat     (patch 98059ac)
  4. Pre-flight surveyor seeds gen + verifier     (patch a8ea1b6)

Routes Anthropic-shaped calls through NVIDIA NIM's OpenAI-compatible endpoint
using the existing OpenRouterClient adapter with a custom base_url.

Usage:
    NVIDIA_API_KEY=nvapi-... python scripts/smoke_test_pr9.py [model]

Models:
    nvidia/nemotron-3-nano-30b-a3b      (default, has reasoning mode)
    deepseek-ai/deepseek-v4-pro
    moonshotai/kimi-k2.6
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-30b-a3b"
PROBLEM = (
    "In quantum mechanics with canonical commutator [x, p] = i*hbar, derive each of the following:\n"
    "(a) the commutator [x, p^2],\n"
    "(b) the commutator [x^2, p^2],\n"
    "(c) the commutator [x, H] where H = p^2/(2m) + V(x).\n"
    "Show every step explicitly with correct factors of hbar and i. Express all results in SI units "
    "and verify dimensional consistency for each."
)


def main() -> int:
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("ERROR: NVIDIA_API_KEY environment variable required", file=sys.stderr)
        return 1

    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL

    # Wire NVIDIA NIM via the OpenRouter adapter (it accepts a custom base_url).
    # Tee every model response to a dump file so we can grep for patch markers
    # (the agent throws away raw text after parsing).
    from alethic.client_factory import set_client_factory
    from alethic.openrouter import OpenRouterClient

    dump_path = Path("/tmp") / f"smoke-pr9-responses-{os.getpid()}.txt"
    dump_path.write_text("")  # truncate
    print(f"Raw response dump: {dump_path}")

    def make_client(_ignored):
        client = OpenRouterClient(
            api_key=api_key,
            model=model,
            base_url=NVIDIA_BASE,
            request_interval=2.0,
        )
        orig = client.messages.create

        def wrapped(**kwargs):
            resp = orig(**kwargs)
            text = "".join(
                getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
            )
            # Capture the full SYSTEM prompt — needed to detect saturation block injection
            # (patch #3 modifies extra_system which gets concatenated into Anthropic's system kwarg)
            system_prompt = kwargs.get("system", "")
            if isinstance(system_prompt, list):
                system_prompt = "\n".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in system_prompt
                )
            sys_first = ""
            for m in kwargs.get("messages", []):
                if m.get("role") == "user":
                    c = m.get("content", "")
                    if isinstance(c, str):
                        sys_first = c[:80]
                    break
            with dump_path.open("a") as fh:
                fh.write(f"\n{'='*70}\n")
                fh.write(f"PROMPT-PREVIEW: {sys_first!r}\n")
                fh.write(f"SYSTEM ({len(system_prompt)} chars):\n")
                fh.write(system_prompt)
                fh.write(f"\n--- RESPONSE ({len(text)} chars) ---\n")
                fh.write(text)
                fh.write("\n")
            return resp

        client.messages.create = wrapped
        return client

    set_client_factory(make_client)

    from alethic import AgentConfig
    from alethic.physics_agent import PhysicsAgent

    # 'thorough' preset enables surveyor + thinking. Cap iterations and disable
    # Sonnet-dependent extras (variant_b, breaker) — both would silently require
    # an Anthropic key.
    # confidence_threshold raised to 0.99 to force at least one revision so
    # patches #2 (ISSUE TRIAGE) and #3 (saturation) can actually fire.
    # max_iterations=4 gives room for: gen → verify → revise → re-verify → revise.
    config = AgentConfig.from_preset(
        "thorough",
        model=model,
        max_iterations=4,
        best_of_n=1,
        variant_b=None,
        adversarial_breaker=False,
        confidence_threshold=0.99,
    )

    print(f"\n=== Smoke test: PR #9 patches via {model} ===")
    print(
        f"Preset=thorough  Surveyor={config.enable_surveyor}  "
        f"Iters<= {config.max_iterations}  N={config.best_of_n}  "
        f"Thinking={config.extended_thinking}"
    )
    print()

    agent = PhysicsAgent(api_key=api_key, config=config)
    result = agent.solve(PROBLEM)

    session_dir = getattr(result, "session_dir", None)
    if session_dir is None:
        # Fallback: scan .alethic/ for the most recent dir with our slug
        for parent in (Path(".alethic"), Path("/tmp")):
            if not parent.exists():
                continue
            candidates = sorted(parent.glob("derive*"), key=lambda p: p.stat().st_mtime)
            if candidates:
                session_dir = str(candidates[-1])
                break

    print(f"\n=== Result ===")
    print(f"Verdict:     {result.verdict.value}")
    print(f"Confidence:  {result.confidence:.3f}")
    print(f"Iterations:  {len(result.events)} events")
    print(f"Session dir: {session_dir}")

    # The library doesn't write per-iteration worklog files (the SKILL does).
    # Dump in-memory events to JSONL so we can inspect what actually fired.
    if session_dir:
        worklog = Path(session_dir) / "worklog"
        worklog.mkdir(exist_ok=True)
        events_path = worklog / "events.jsonl"
        with events_path.open("w") as fh:
            for ev in result.events:
                t = ev.type.value if hasattr(ev.type, "value") else str(ev.type)
                fh.write(json.dumps({
                    "type": t,
                    "iteration": ev.iteration,
                    "data": _jsonable(ev.data),
                }) + "\n")
        # Also dump the best solution so we can inspect it
        if result.solution:
            (Path(session_dir) / "output.md").write_text(result.solution)
        print(f"\nDumped {len(result.events)} events → {events_path}")
        scan_events(result.events)

    # Saturation requires inspecting captured SYSTEM prompts in the dump file
    # (not in result.events). Look for the addendum's distinctive heading.
    print("\n=== Saturation marker scan (raw SYSTEM prompts) ===")
    dump_text = dump_path.read_text() if dump_path.exists() else ""
    for marker in ["Loop Saturation Awareness", "<critique-category-history>", "saturation_resolution:"]:
        n = dump_text.count(marker)
        print(f"  {n:3d} × {marker!r}  {'✅ FIRED' if n > 0 else ''}")

    return 0


def _jsonable(obj):
    """Best-effort conversion of arbitrary objects to JSON-safe primitives."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if hasattr(obj, "value"):  # Enum
        return obj.value
    if hasattr(obj, "__dict__"):
        return {k: _jsonable(v) for k, v in obj.__dict__.items()}
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def scan_events(events) -> None:
    """Inspect in-memory events for evidence each PR #9 patch fired."""
    print("\n=== Patch fire-check (in-memory events) ===")
    haystack = ""
    type_counts: dict[str, int] = {}
    for ev in events:
        t = ev.type.value if hasattr(ev.type, "value") else str(ev.type)
        type_counts[t] = type_counts.get(t, 0) + 1
        # flatten the data dict into the haystack
        haystack += "\n" + json.dumps(_jsonable(ev.data))

    markers = {
        "CHECKS PERFORMED block (patch #1)": "CHECKS PERFORMED" in haystack,
        "ISSUE TRIAGE block (patch #2)":     "ISSUE TRIAGE" in haystack,
        "Saturation awareness (patch #3)":   "SATURATION" in haystack.upper() or "saturat" in haystack.lower(),
        "Surveyor pitfalls (patch #4)":      "PITFALL" in haystack.upper() or "surveyor" in haystack.lower(),
    }
    for label, found in markers.items():
        print(f"  {'✓' if found else '✗'} {label}")

    print("\n=== Event types observed ===")
    for t, n in sorted(type_counts.items()):
        print(f"  {n:3d}  {t}")


if __name__ == "__main__":
    sys.exit(main())
