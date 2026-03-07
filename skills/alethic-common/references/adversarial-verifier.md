# Adversarial Self-Correction Protocol

After completing your initial assessment, you MUST work through all self-correction rounds explicitly before outputting your final verdict.

**Round 2 — Hallucination check:**
Ask yourself: "Did I accept any proof step without actually verifying it? Did I hallucinate a valid derivation where none exists? Did I skim over a gap and implicitly fill it in?" List every step you accepted without independent verification.

**Round 3 — Revised assessment:**
Based on Round 2: revise your confidence down for each step you identified as accepted-without-verification. Update your critique to reflect these gaps.

**Round 4 — Completeness check:**
Ask yourself: "Are there remaining unverified steps? Does every logical inference in the solution have explicit justification? Are all cases covered? Are all cited theorems confirmed by name or proved inline?"

**Round 5 — Final output:**
Conclude with one of these two tags on its own line:

- `COMPLETE PROOF`: every step was independently verified by you, no gaps remain
- `STRUCTURED PARTIAL PROGRESS`: valid framework present, with explicit gaps listed

Your VERDICT and CONFIDENCE in the required output block must reflect your Round 5 assessment, not Round 1. Be strict: a COMPLETE PROOF requires that YOU personally verified every step — not just that it looks plausible.
