# YentlGuard

**Mechanistic interpretability layer for clinical triage LLM bias.**

Built by [HARMONI Lab](https://harmonilab.org) on top of [YentlBench](https://github.com/harmonilab/yentlbench).

YentlGuard instruments Gemini 2.5 Pro and 3.1 Pro triage runs at the token level, capturing the exact mathematical moments where sex/gender labels shift a model's clinical reasoning — and testing whether corrective re-prompting recovers baseline confidence.

---

## What it measures

| Metric | What it captures |
|--------|-----------------|
| **ΔM** (Token Confidence Margin) | Logprob gap between the model's chosen ESI digit and the runner-up — the width of its commitment at the triage decision boundary |
| **TAR** (Thought Allocation Ratio) | `thoughts_token_count / candidates_token_count` — how much internal reasoning the model expended before generating the ESI digit |
| **CRR** (Confidence Recovery Rate) | Whether a corrective re-prompt foregrounding vital signs recovers ΔM to the `nb_ambiguous` baseline after a demographic-triggered confidence drop |

## Architecture

```
YentlBench vignette quintets
        ↓
YentlGuardRunner (google-genai SDK, response_logprobs=True, ThinkingConfig)
        ↓
OpenInference GoogleGenAIInstrumentor → Arize Phoenix (full span + metadata)
        ↓
Correction Gate: ΔM < threshold AND demographic token present?
        ↓ yes
Phoenix MCP Client → retrieve nb_ambiguous baseline ΔM for this vignette
        ↓
Pass 2: corrective re-prompt (vital signs foregrounded, demographic suppressed)
        ↓
CRR computed: did confidence recover?
        ↓
Agent Builder Evaluation Layer (structured eval scoring, experiment versioning)
```

## Installation

```bash
pip install yentlguard
```

Requires Python 3.11+. YentlBench is installed automatically as a dependency.

## Quick start

```bash
# 1. Populate Phoenix with nb_ambiguous baseline spans
yentlguard baseline --model gemini-2.5-pro --budget medium

# 2. Run two-pass mechanistic analysis for female variant, all thinking budgets
yentlguard run \
  --model gemini-3.1-pro \
  --budget low medium high \
  --variants female nb_label_only

# 3. Generate aggregate report across both model versions
yentlguard report \
  --model gemini-2.5-pro gemini-3.1-pro \
  --output results/
```

## Environment variables

```bash
PHOENIX_API_KEY=your_key
PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com/s/your-space
GOOGLE_API_KEY=your_gemini_key   # or configure ADC for Vertex AI
```

## Research hypotheses

**H1 — Reasoning Mitigation Effect**: Does scaling ThinkingConfig budget from low → high reduce PSS (Perturbation Sensitivity Score) in Gemini 3.1 Pro?

**H2 — Demographic Cognitive Friction**: Does a female chest-pain presentation produce higher TAR than the male baseline, indicating reconciliation cost?

**H3 — Mathematical Boundary Invariance**: Does Gemini 3.1 Pro maintain wider ΔM under demographic perturbation than 2.5 Pro at the safety-critical ESI 2↔3 boundary?

**H4 — Selective Surgery via CRR**: Does vital-sign-foregrounding corrective re-prompting recover `nb_ambiguous` ΔM, and does recovery rate vary by clinical category?

## Citation

If you use YentlGuard in your research, please cite:

```bibtex
@software{campo2026yentlguard,
  author    = {Campo, Inna},
  title     = {{YentlGuard}: Mechanistic Interpretability for Clinical Triage LLM Bias},
  year      = {2026},
  publisher = {HARMONI Lab},
  url       = {https://github.com/harmonilab/yentlguard}
}
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
