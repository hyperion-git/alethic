# Gate Benchmark Suite — Design Spec

**Date:** 2026-03-17
**Purpose:** 100-problem benchmark for the v3.8 gate experiment (Option E vs Option F decision) and v4.0 regression baseline.
**Deliverables:** `gate-v38.json` benchmark file, `orchestrator.md` patch (error_category in verify events), `scripts/run_gate.py` driver + harvester for Claude Code subscription execution.

---

## 1. Motivation

The v3.7 eval harness produces atom metrics (Option E signal) and PUCT divergence (Option F signal), but the existing 20-problem suite is too small for statistical confidence:

- `puct_would_have_won ≥ 3/20` has a 95% CI width of ±18% — barely distinguishable from noise
- At 100 problems the same 15% threshold has CI width ±7% — solid

Three specialist agents (statistician, math curator, physics curator) were consulted for the optimal split. The statistician's information-theoretic analysis showed PUCT divergence is the binding constraint and physics problems produce ~2x more PUCT signal per problem due to multi-method approach diversity. However, competition-level math problems also produce strong PUCT signal through proof-strategy diversity. A 45/45/10 compromise was selected.

## 2. File Layout

```
data/benchmarks/
├── math-sample.json          # existing (10 problems, fast smoke test)
├── physics-sample.json       # existing (10 problems, fast smoke test)
└── gate-v38.json             # NEW: 100 problems, gate + regression suite
```

The existing sample files are retained for fast smoke tests. `gate-v38.json` is additive. All 20 existing problems are included verbatim (same `id`, same `problem` text).

## 3. Schema

Identical to existing benchmarks. Required: `id`, `domain`, `problem`, `expected_solvable`. Optional metadata: `tags` (array of strings), `difficulty` (string), `source` (string), `note` (string, for false claims).

```json
{
  "name": "gate-v38",
  "version": "1.0",
  "description": "100-problem gate benchmark for v3.8 E-vs-F decision and v4.0 regression baseline.",
  "problems": [ ... ]
}
```

## 4. Problem Distribution

### 4.1 Aggregate

|           | Genuine | False claims | Total |
|-----------|---------|-------------|-------|
| Math      | 40      | 5           | 45    |
| Physics   | 40      | 5           | 45    |
| **Total** | **80**  | **10**      | **100** |

### 4.2 Math (45 problems)

| Difficulty   | Count | Purpose |
|-------------|-------|---------|
| Easy        | 10    | Regression floor |
| Medium      | 17    | Core undergraduate reasoning |
| Hard        | 8     | Multi-iteration, occasional failure |
| Competition | 5     | Maximum PUCT signal |
| False claim | 5     | False-premise detection |

**Domain coverage:** number theory (10), analysis/calculus (12), algebra (7), linear algebra (4), combinatorics (5), probability (3), topology/set theory (3), ODEs (1).

### 4.3 Physics (45 problems)

| Difficulty   | Count | Purpose |
|-------------|-------|---------|
| Easy        | 10    | Regression floor + dimensional analysis |
| Medium      | 16    | Standard undergraduate derivations |
| Hard        | 9     | Advanced undergrad, multi-method |
| Graduate    | 5     | Ceiling test, maximum PUCT signal |
| False claim | 5     | False-premise detection |

**Domain coverage:** classical mechanics (7), thermo/stat mech (8), quantum mechanics (13), electromagnetism (5), relativity (5), optics (2), fluids (3), solid state (2).

**Multi-method tags (15 problems):** central-force orbit, Euler-Lagrange, coupled oscillators, Maxwell-Boltzmann, Biot-Savart, Larmor radiation, Fresnel equations, Bernoulli, Pauli matrices, WKB tunneling, Born approximation, variational helium, geodesic equation, Landau levels, Berry phase. These admit 2-4 genuinely different solution methods and are the most PUCT-informative problems.

## 5. Problem Lists

### 5.1 Math Problems

#### Easy (10)

| id | problem | solvable | tags |
|----|---------|----------|------|
| `prime-17` | Prove that 17 is prime. | true | number-theory, elementary |
| `sqrt2-irrational` | Prove that sqrt(2) is irrational. | true | number-theory, proof-by-contradiction |
| `sum-arithmetic` | Prove that the sum of the first n positive integers is n(n+1)/2. | true | combinatorics, induction |
| `infinite-primes` | Prove that there are infinitely many prime numbers. | true | number-theory, euclid |
| `am-gm` | Prove the AM-GM inequality: for positive reals a and b, (a+b)/2 >= sqrt(ab). | true | inequalities, algebra |
| `geometric-series` | Derive the formula for the sum of a finite geometric series: sum_{k=0}^{n-1} r^k = (1-r^n)/(1-r) for r != 1. | true | series, algebra |
| `triangle-inequality` | Prove the triangle inequality: \|a + b\| <= \|a\| + \|b\| for real numbers a and b. | true | analysis, inequalities |
| `derivative-product-rule` | Derive the product rule for derivatives: d/dx[f(x)g(x)] = f'(x)g(x) + f(x)g'(x) from first principles. | true | calculus, derivatives |
| `bezout-identity-small` | Prove that gcd(12, 8) = 4 using the Euclidean algorithm, and find integers x, y such that 12x + 8y = 4. | true | number-theory, euclidean-algorithm |
| `binomial-theorem` | Prove the binomial theorem: (a+b)^n = sum_{k=0}^{n} C(n,k) a^{n-k} b^k for all non-negative integers n, by mathematical induction. | true | combinatorics, induction, algebra |

#### Medium (17)

| id | problem | solvable | tags |
|----|---------|----------|------|
| `cantor-diagonal` | Prove using Cantor's diagonal argument that the real numbers are uncountable. | true | set-theory, cardinality |
| `sqrt3-irrational` | Prove that sqrt(3) is irrational. | true | number-theory, proof-by-contradiction |
| `fermat-little-theorem` | Prove Fermat's Little Theorem: if p is a prime and a is an integer not divisible by p, then a^{p-1} ≡ 1 (mod p). | true | number-theory, modular-arithmetic |
| `euler-totient-multiplicative` | Prove that Euler's totient function φ is multiplicative: if gcd(m, n) = 1, then φ(mn) = φ(m)φ(n). | true | number-theory, multiplicative-functions |
| `unique-prime-factorization` | Prove the Fundamental Theorem of Arithmetic: every integer greater than 1 has a unique factorization into primes (up to ordering). | true | number-theory, strong-induction |
| `infinitude-primes-4k3` | Prove that there are infinitely many primes of the form 4k + 3. | true | number-theory, proof-by-contradiction |
| `cauchy-schwarz-rn` | Prove the Cauchy-Schwarz inequality in R^n: for vectors u, v in R^n, \|u · v\| <= \|\|u\|\| · \|\|v\|\|. | true | linear-algebra, inequalities |
| `rank-nullity` | Prove the Rank-Nullity Theorem: for a linear map T: V → W between finite-dimensional vector spaces, dim(V) = rank(T) + nullity(T). | true | linear-algebra, dimension |
| `ftc-part1` | Prove the First Fundamental Theorem of Calculus: if f is continuous on [a, b] and F(x) = ∫_a^x f(t) dt, then F'(x) = f(x) for all x in (a, b). | true | analysis, calculus, integration |
| `mvt` | Prove the Mean Value Theorem: if f is continuous on [a, b] and differentiable on (a, b), then there exists c in (a, b) such that f'(c) = (f(b) - f(a))/(b - a). | true | analysis, calculus |
| `bolzano-weierstrass` | Prove the Bolzano-Weierstrass theorem: every bounded sequence of real numbers has a convergent subsequence. | true | analysis, sequences, compactness |
| `e-irrational` | Prove that e is irrational. | true | analysis, proof-by-contradiction, series |
| `derangement-formula` | Derive the formula for the number of derangements D_n of {1, 2, ..., n}: D_n = n! · sum_{k=0}^{n} (-1)^k / k!. Prove this formula using inclusion-exclusion. | true | combinatorics, inclusion-exclusion |
| `catalan-number-formula` | Prove that the number of valid sequences of n pairs of balanced parentheses is the Catalan number C_n = (2n)! / ((n+1)! · n!). | true | combinatorics, catalan-numbers, bijection |
| `group-order-prime-cyclic` | Prove that every group of prime order is cyclic. | true | algebra, group-theory |
| `lagrange-theorem-groups` | Prove Lagrange's theorem: if H is a subgroup of a finite group G, then \|H\| divides \|G\|. | true | algebra, group-theory |
| `gambler-ruin-probability` | In the gambler's ruin problem, a gambler starts with i dollars and at each step wins $1 with probability p or loses $1 with probability q = 1-p. The game ends when the gambler reaches N dollars or 0 dollars. Find the probability of ruin as a function of i, p, and N. Consider p ≠ 1/2 and p = 1/2 separately. | true | probability, random-walks, difference-equations |

#### Hard (8)

| id | problem | solvable | tags |
|----|---------|----------|------|
| `cayley-hamilton` | Prove the Cayley-Hamilton theorem: every square matrix over a field satisfies its own characteristic polynomial. | true | linear-algebra, characteristic-polynomial |
| `basel-problem` | Prove that sum_{n=1}^{∞} 1/n² = π²/6. | true | analysis, series |
| `stirling-approximation` | Prove Stirling's approximation: n! ~ √(2πn) · (n/e)^n as n → ∞. | true | analysis, asymptotics |
| `irrationality-of-pi` | Prove that π is irrational. | true | analysis, proof-by-contradiction, integration |
| `sylow-first-theorem` | Prove Sylow's first theorem: if G is a finite group and p^k divides \|G\| for some prime p, then G contains a subgroup of order p^k. | true | algebra, group-theory, sylow-theorems |
| `fundamental-theorem-algebra` | Prove the Fundamental Theorem of Algebra: every non-constant polynomial with complex coefficients has at least one root in C. You may use Liouville's theorem. | true | algebra, complex-analysis |
| `ode-picard-lindelof` | State and prove the Picard-Lindelöf theorem for the IVP y' = f(t, y), y(t_0) = y_0 when f is Lipschitz in y. | true | differential-equations, fixed-point, contraction-mapping |
| `central-limit-theorem` | Prove the Central Limit Theorem for i.i.d. random variables with finite variance: (S_n - nμ)/(σ√n) converges in distribution to N(0,1). You may use characteristic functions. | true | probability, limit-theorems, characteristic-functions |

#### Competition (5)

| id | problem | solvable | tags | source |
|----|---------|----------|------|--------|
| `putnam-2003-a1` | Let n be a fixed positive integer. How many ways are there to write n as a sum of positive integers n = a_1 + a_2 + ... + a_k with k arbitrary and a_1 ≤ a_2 ≤ ... ≤ a_k ≤ a_1 + 1? Find a closed-form formula and prove it. | true | combinatorics, competition | Putnam 2003 A1 |
| `putnam-2010-a4` | Prove that for each positive integer n, the number 10^{10^{10^n}} + 10^{10^n} + 10^n - 1 is not prime. | true | number-theory, divisibility, competition | Putnam 2010 A4 |
| `putnam-1985-b1` | Let f(n) denote the number of 1's in the binary expansion of n. Prove that sum_{k=1}^{n} (-1)^{f(k)} ≥ 0 for all positive integers n, and determine all n for which equality holds. | true | number-theory, binary-representation, competition | Putnam 1985 B1 |
| `imo-1988-p6` | Let a and b be positive integers such that ab + 1 divides a² + b². Prove that (a² + b²)/(ab + 1) is a perfect square. | true | number-theory, vieta-jumping, competition | IMO 1988 P6 |
| `aime-style-divisors` | Let S be the set of all positive integer divisors of 100,000. How many elements of S are divisible by exactly 2 of the prime numbers 2, 3, 5, 7? Prove your answer. | true | number-theory, combinatorics, competition | AIME-style |

#### Math False Claims (5)

| id | problem | note |
|----|---------|------|
| `false-claim-even-odd` | Prove that every even number greater than 2 is the sum of exactly three primes. | False: 4 = 2+2 is only two primes. |
| `false-n2-n-41-prime` | Prove that n² + n + 41 is prime for every positive integer n. | False at n=40: 40² + 40 + 41 = 41² = 1681. |
| `false-all-norms-equivalent` | Prove that all norms on an infinite-dimensional Banach space are equivalent. | True only in finite dimensions. On C[0,1], L¹ and sup norms are not equivalent. |
| `false-every-matrix-diagonalizable` | Prove that every square matrix over the complex numbers is diagonalizable. | The nilpotent Jordan block [[0,1],[0,0]] is not diagonalizable. |
| `false-continuous-implies-differentiable` | Prove that every continuous function f: R → R is differentiable at at least one point. | Weierstrass function (1872) is continuous everywhere, differentiable nowhere. |

### 5.2 Physics Problems

#### Easy (10)

| id | problem | solvable | tags |
|----|---------|----------|------|
| `kepler-third-law` | Derive Kepler's third law (T² ∝ a³) from Newton's law of universal gravitation for a planet in a circular orbit of radius a around a star of mass M. | true | classical-mechanics, orbital-mechanics |
| `simple-pendulum-period` | Derive the period of a simple pendulum of length L for small-angle oscillations. Start from Newton's second law applied to the bob and show that T = 2π√(L/g). | true | classical-mechanics, oscillations |
| `ideal-gas-kinetic-theory` | Derive the ideal gas law PV = NkT from the kinetic theory of gases. Consider N identical molecules of mass m in a cubic container of side L, and compute the average pressure from molecular collisions with the walls. | true | thermodynamics, kinetic-theory |
| `carnot-efficiency` | Derive the efficiency of a Carnot engine operating between a hot reservoir at temperature T_H and a cold reservoir at temperature T_C. Show that η = 1 - T_C/T_H using the properties of reversible isothermal and adiabatic processes. | true | thermodynamics, heat-engines |
| `infinite-square-well` | Solve the time-independent Schrödinger equation for a particle of mass m in an infinite square well of width a (V=0 for 0<x<a, V=∞ otherwise). Find the normalized energy eigenfunctions and show that E_n = n²π²ℏ²/(2ma²). | true | quantum-mechanics, bound-states |
| `escape-velocity` | Derive the escape velocity from the surface of a spherical body of mass M and radius R. Use conservation of energy with U(r) = -GMm/r. Show that v_esc = √(2GM/R). | true | classical-mechanics, gravitation |
| `projectile-range` | Derive the range formula for a projectile launched from ground level with initial speed v_0 at angle θ in uniform gravity g (no air resistance). Show R = v_0² sin(2θ)/g and maximum range at θ = 45°. | true | classical-mechanics, kinematics |
| `rigid-body-parallel-axis` | Derive the parallel axis theorem: I = I_cm + Md² from the definition I = ∫ r_⊥² dm. | true | classical-mechanics, rigid-body |
| `em-wave-equation` | Derive the electromagnetic wave equation in vacuum from Maxwell's equations. Show that E and B satisfy ∇²F = (1/c²)∂²F/∂t² and identify c = 1/√(μ₀ε₀). | true | electromagnetism, waves |
| `thin-lens-equation` | Derive the thin lens equation 1/s + 1/s' = 1/f from the lensmaker's equation. Show 1/f = (n-1)(1/R₁ - 1/R₂). | true | optics, geometrical-optics |

#### Medium (16)

| id | problem | solvable | tags |
|----|---------|----------|------|
| `qho-energy-levels` | Derive the energy eigenvalues of the one-dimensional quantum harmonic oscillator with potential V(x) = ½mω²x² using the algebraic (ladder operator) method. Show that E_n = ℏω(n + ½) for n = 0, 1, 2, ... | true | quantum-mechanics, harmonic-oscillator |
| `hydrogen-energy-spectrum` | Derive the energy spectrum of the hydrogen atom by solving the radial Schrödinger equation for the Coulomb potential V(r) = -e²/(4πε₀r). Show that the bound-state energies are E_n = -13.6 eV/n² for n = 1, 2, 3, ... | true | quantum-mechanics, hydrogen-atom |
| `lorentz-transformation` | Derive the Lorentz transformation equations from Einstein's two postulates of special relativity: (1) the laws of physics are the same in all inertial frames, and (2) the speed of light is the same in all inertial frames. Consider a frame S' moving with velocity v along the x-axis of frame S. | true | relativity, special-relativity |
| `gauss-law-from-coulomb` | Derive Gauss's law (∮ E·dA = Q_enc/ε₀) from Coulomb's law for a point charge. Show that it holds for an arbitrary closed surface enclosing the charge by computing the flux through the surface. | true | electromagnetism, gauss-law |
| `central-force-orbit` | Derive the orbit equation r(θ) = L²/(mk) · 1/(1 + e cos θ) for a particle of mass m in an inverse-square central force F = -k/r² using the Binet substitution u = 1/r. Identify the eccentricity e in terms of energy and angular momentum. | true | classical-mechanics, orbital-mechanics, multi-method |
| `euler-lagrange` | Derive the Euler-Lagrange equation d/dt(∂L/∂q̇) - ∂L/∂q = 0 from Hamilton's principle δS = δ∫L dt = 0 using the calculus of variations. | true | classical-mechanics, lagrangian-mechanics, multi-method |
| `coupled-oscillators` | Two identical masses m are connected by three identical springs of constant k in the configuration wall-spring-mass-spring-mass-spring-wall. Derive the normal mode frequencies ω₁² = k/m and ω₂² = 3k/m and their corresponding mode shapes. | true | classical-mechanics, oscillations, multi-method |
| `noether-energy` | Prove Noether's theorem for time translation invariance: if the Lagrangian has no explicit time dependence (∂L/∂t = 0), then the Hamiltonian H = q̇(∂L/∂q̇) - L is conserved. | true | classical-mechanics, lagrangian-mechanics, symmetry |
| `maxwell-boltzmann` | Derive the Maxwell-Boltzmann speed distribution f(v) = 4πn(m/(2πkT))^{3/2} v² exp(-mv²/(2kT)). Find the most probable, mean, and rms speeds. | true | thermodynamics, statistical-mechanics, multi-method |
| `stefan-boltzmann` | Integrate the Planck distribution to derive the Stefan-Boltzmann law. Show that the total energy density is u = aT⁴ and the radiant exitance is j = σT⁴, expressing σ = 2π⁵k⁴/(15c²h³). | true | thermodynamics, statistical-mechanics |
| `clausius-clapeyron` | Derive the Clausius-Clapeyron equation dP/dT = L/(TΔv) from the equality of Gibbs free energies along a phase coexistence curve. | true | thermodynamics, phase-transitions |
| `spin-half-pauli` | Derive the 2×2 Pauli matrix representations of S_x, S_y, S_z from the angular momentum commutation relations [S_i, S_j] = iℏε_{ijk}S_k and the eigenvalue condition S² = s(s+1)ℏ² with s = 1/2. | true | quantum-mechanics, angular-momentum, multi-method |
| `density-of-states-3d` | Derive the density of states g(E) = (V/(2π²))(2m/ℏ²)^{3/2} √E for a free electron gas in 3D. | true | quantum-mechanics, solid-state, multi-method |
| `relativistic-energy-momentum` | Derive E² = (pc)² + (mc²)² from the four-momentum. Verify the non-relativistic limit E → mc² + mv²/2. | true | relativity, special-relativity |
| `biot-savart` | Derive the Biot-Savart law from the magnetostatic vector potential A = (μ₀/4π)∫ J/r dV'. Reduce to the wire integral form and compute B = ∇ × A. | true | electromagnetism, magnetostatics, multi-method |
| `partition-function-two-level` | Derive the partition function Z, mean energy ⟨E⟩, heat capacity C, and entropy S for a two-level system with energies 0 and ε. Show the Schottky anomaly peak at kT ≈ 0.42ε. | true | thermodynamics, statistical-mechanics |

#### Hard (9)

| id | problem | solvable | tags |
|----|---------|----------|------|
| `sackur-tetrode` | Derive the Sackur-Tetrode equation for the entropy of an ideal monatomic gas from the microcanonical ensemble, using the volume of a 3N-dimensional hypersphere and Stirling's approximation. | true | thermodynamics, statistical-mechanics |
| `debye-model-low-t` | Derive the low-temperature heat capacity C_V = (12/5)π⁴Nk(T/Θ_D)³ from the Debye density of states and Bose-Einstein statistics. | true | solid-state, statistical-mechanics |
| `wkb-tunneling` | Derive the WKB tunneling probability T ≈ exp(-2∫κ dx) where κ = √(2m(V-E))/ℏ. Apply to a rectangular barrier of height V₀ and width a. | true | quantum-mechanics, semiclassical, multi-method |
| `hydrogen-selection-rules` | Derive the electric dipole selection rules Δl = ±1, Δm = 0, ±1 for hydrogen atom transitions from the transition matrix element ⟨f|r|i⟩ using spherical harmonic orthogonality properties. | true | quantum-mechanics, angular-momentum |
| `fermi-golden-rule` | Derive Fermi's golden rule Γ = (2π/ℏ)|⟨f|V|i⟩|² ρ(E_f) from first-order time-dependent perturbation theory. | true | quantum-mechanics, perturbation-theory |
| `born-approximation` | Derive the first Born approximation for the scattering amplitude f(θ). Apply to the Yukawa potential V(r) = -βe^{-μr}/r and show f = -2mβ/(ℏ²(μ² + q²)). | true | quantum-mechanics, scattering, multi-method |
| `aharonov-bohm` | Derive the Aharonov-Bohm phase shift Δφ = qΦ/ℏ from the canonical momentum and the path integral of the vector potential around a solenoid of magnetic flux Φ. | true | quantum-mechanics, topology |
| `larmor-radiation` | Derive the Larmor radiation formula P = q²a²/(6πε₀c³) for a non-relativistic accelerating charge from retarded potentials and Poynting vector integration. | true | electromagnetism, radiation, multi-method |
| `variational-helium` | Use the variational method with a screened-charge trial wavefunction ψ = (Z_eff³/πa₀³)e^{-Z_eff r/a₀} for each electron to estimate the ground state energy of helium. Find Z_eff = 27/16 and compare the result to the experimental -79.0 eV. | true | quantum-mechanics, variational-method, multi-method |

#### Graduate (5)

| id | problem | solvable | tags |
|----|---------|----------|------|
| `ising-1d-transfer-matrix` | Solve the 1D Ising model with N spins in an external field h via the transfer matrix method. Find the eigenvalues λ_± and show there is no finite-temperature phase transition in the thermodynamic limit. | true | statistical-mechanics, exactly-solvable, multi-method |
| `dirac-nr-limit` | Derive the Pauli equation from the Dirac equation by separating large and small spinor components in the non-relativistic limit. Identify the electron g-factor g = 2. | true | quantum-mechanics, relativity, multi-method |
| `casimir-effect` | Derive the Casimir force per unit area F/A = -π²ℏc/(240d⁴) between two perfectly conducting parallel plates separated by distance d, using zeta-function regularization of the zero-point energy sum. | true | quantum-field-theory, multi-method |
| `hawking-temperature` | Derive the Hawking temperature T_H = ℏc³/(8πGMk_B) via Euclidean path integral: Wick-rotate the Schwarzschild metric t → -iτ, require regularity at the horizon to fix the periodicity β = 8πGM/c³, identify T = 1/(k_B β). | true | general-relativity, quantum-field-theory |
| `berry-phase-spin-half` | Derive the Berry phase γ = -Ω/2 acquired by a spin-1/2 particle in a magnetic field that slowly rotates tracing a solid angle Ω on the unit sphere. Verify γ = -π for an equatorial loop. | true | quantum-mechanics, topology, multi-method |

#### Physics False Claims (5)

| id | problem | note |
|----|---------|------|
| `false-drude-lorenz-number` | Prove that the classical Drude model of metallic conduction predicts the exact Lorenz number L₀ = π²k²_B/(3e²) from the Wiedemann-Franz law. | Drude gives L₀ = 3k²_B/(2e²); the quantum Sommerfeld result π²k²_B/(3e²) differs by factor 2π²/9 ≈ 2.19. |
| `false-equipartition-all-temps` | Prove that the classical equipartition theorem ⟨E⟩ = (1/2)kT per quadratic degree of freedom holds at all temperatures for a quantum harmonic oscillator. | Fails for kT ≪ ℏω; QHO freezes out to zero-point energy E₀ = ℏω/2. |
| `false-maxwell-monopoles` | Prove that Maxwell's equations in vacuum predict the existence of magnetic monopoles. | Maxwell's equations give ∇·B = 0; monopoles require a modified div B = ρ_m term. |
| `false-rayleigh-jeans-full` | Prove that the Rayleigh-Jeans law u(ν) = 8πν²kT/c³ correctly describes the full blackbody spectrum at all frequencies. | Diverges as ν → ∞ (ultraviolet catastrophe); Planck quantization is required. |
| `false-bell-classical` | Prove that quantum mechanics satisfies Bell's inequality (the CHSH bound S ≤ 2) for all entangled states. | QM violates CHSH with S_max = 2√2 ≈ 2.83 (Tsirelson's bound). |

## 6. Skill Parity Patch: error_category in Verify Events

### 6.1 Problem

The skill orchestrator (`skills/alethic-common/orchestrator.md`) does not emit `error_category` in verify events. The PUCT metric `compute_puct_comparison()` requires this field to classify approach types.

### 6.2 Solution

After parsing the verifier's CRITIQUE in the orchestrator, classify the critique text using the same keyword heuristic as `classify_errors()` in `error_taxonomy.py`. Add the result to the verify event.

The orchestrator already parses VERDICT, CONFIDENCE, and CRITIQUE from the verifier output. The classification is a deterministic keyword match — no LLM call needed. The orchestrator implements this inline (not by importing from the Python library, which skills cannot do).

Categories (checked in priority order, first match wins): `algebra`, `logic`, `citation`, `interpretation`, `units`, `counterexample`, `missing_case`, `general`.

### 6.3 Scope

- Add `error_category` field to verify events in `events.jsonl`
- Applies to: initial verify, re-verify after revision, re-verify after FIXABLE shortcut
- Does NOT change any behavior — purely observational

## 7. Subscription Runner: scripts/run_gate.py

### 7.1 Purpose

Run the 100-problem gate benchmark through Claude Code skills (`/alethic-solve`, `/alethic-derive`) instead of the Python library's `anthropic` SDK. Uses the user's Claude Code subscription — no API key or per-token billing.

### 7.2 Architecture

```
run_gate.py
├── driver()           — iterate problems, shell out to `claude -p`
├── harvest()          — read .alethic/ session dirs, compute metrics
└── report()           — format gate decision output
```

### 7.3 Driver

```python
for problem in benchmark["problems"]:
    domain = problem["domain"]
    skill = "/alethic-solve" if domain == "math" else "/alethic-derive"
    cmd = f'claude -p "{skill} -p default \'{problem["problem"]}\'" --no-input'
    subprocess.run(cmd, shell=True)
```

Key behaviors:
- Sequential execution (one problem at a time)
- Writes progress to stdout (problem N/100, elapsed time)
- Tolerates failures (logs and continues)
- Resumes from where it left off (skips problems with existing session dirs)
- Estimated runtime: 5-8 hours on default preset

### 7.4 Harvester

Reads `.alethic/` session directories and computes gate metrics:

1. **Session discovery**: scan `.alethic/` for session dirs, match to benchmark problem IDs via `session.json` problem text
2. **Atom metrics**: read `worklog/candidate_N.md` files (full solution text, not truncated `solution_preview`), parse atoms via `parse_atoms()`, compute `annotation_rate` and `mean_atom_count`
3. **PUCT metrics**: read `worklog/events.jsonl`, extract VERIFY events with `error_category` field (requires §6 patch), compute UCB1 divergence via same algorithm as `compute_puct_comparison()`
4. **Solve rate**: read `session.json` status field (`solved`/`unsolved`)

### 7.5 Output

Same gate decision format as the library harness:

```json
{
  "benchmark": "gate-v38",
  "preset": "default",
  "total": 100,
  "solved": N,
  "solve_rate": 0.XX,
  "mean_annotation_rate": 0.XX,
  "mean_atom_count": X.X,
  "mean_puct_divergence": 0.XX,
  "gate_decision": "Option E / Option F / Neither",
  "results": [ ... per-problem details ... ]
}
```

### 7.6 Dependencies

- `claude` CLI installed and authenticated (Claude Code subscription)
- Python 3.13 with `alethic` package installed (for `parse_atoms()`, `classify_errors()`)
- The orchestrator patch from §6 deployed

## 8. Testing

1. `test_gate_benchmark_loads()` — validates `load_benchmark("data/benchmarks/gate-v38.json")` succeeds, all 100 problems have required fields, counts are 45 math + 45 physics
2. `test_error_category_classification()` — unit tests for the inline keyword classifier in the orchestrator (if applicable; may already be covered by existing `test_error_taxonomy.py`)
3. `test_harvest_session()` — mock `.alethic/` session dir, verify harvester correctly extracts metrics

## 9. Gate Thresholds (unchanged)

| Metric | Threshold | Signal |
|--------|-----------|--------|
| `mean_annotation_rate` | ≥ 0.50 | Atoms produced often enough for Option E |
| `mean_puct_divergence` | ≥ 0.20 | PUCT reorders candidates meaningfully for Option F |
| `puct_would_have_won` | ≥ 15% of problems | PUCT selection would have solved problems that confidence missed |

Combined decision rule:
- `annotation_rate ≥ 0.50` + `atom_improvement ≥ 10%` → Option E
- `puct_signal ≥ 15%` → Option F
- Neither → ship v3.7 as v4.0 baseline, revisit architecture

## 10. Estimated Cost

| Method | Cost | Runtime |
|--------|------|---------|
| Library API (`alethic eval run --preset default`) | ~$290 | ~3 hours |
| Subscription runner (`scripts/run_gate.py`) | $0 (subscription) | ~5-8 hours |
