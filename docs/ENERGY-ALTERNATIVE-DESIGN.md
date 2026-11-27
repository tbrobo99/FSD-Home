# Alternative design for FR-8 (branch `wip/energy-llm-design`)

**Purpose: side-by-side comparison only. Do not merge until the team picks one.**

`main` and this branch both answer FR-8 (monthly energy budget), with opposite
stances on *who makes the decision*:

| | `main` — `app/budget.py` | this branch — `app/energy.py` |
|---|---|---|
| Decision | `can_start()` returns ALLOW / BLOCK / DELAY | LLM proposes ALLOW / BLOCK / DELAY / OPTIMIZE |
| Who decides | deterministic rules only | model proposes, rules validate admissibility |
| Model's role | none in the budget path | decides *what*, never touches a device or a timer |
| Call sites | proactive loop, chat, control API, scheduler (re-checked at execution) | designed for the same four paths; see note below |
| Test surface | `tests/test_heat_energy.py` | `tests/test_energy.py` |

## Status of this branch

- **Module only.** This branch adds `app/energy.py`, its web page (`app/web/energy/`),
  the design note (`docs/FSD-ENERGY.md`) and its tests. It does **not** wire the module
  into `server.py` / `proactive.py` / `agent.py`, so `tests/test_energy.py` is written
  against the integration draft described below.
- The fully wired draft lives on branch `local/energy-wip` (commit `776017e`), which is
  based on the 2026-11-24 tree (`7e9583e`). That base predates 91 commits on `main`,
  including the negation/unsafe-target guard and the Korean command layer — **do not
  merge that branch; it would revert them.**

## Why it exists

After the 25 Nov review the open question was "which algorithm makes the energy
decision, and how does the model participate". `main` answers it with a deterministic
gate. This branch answers it with a model-in-the-loop proposal plus deterministic
validation. The two are comparable only if read side by side.

To reproduce the wired version for a local demo:

```bash
git checkout local/energy-wip   # based on 7e9583e, not on main
python run.py                   # /energy page
```

Decision needed from the team: keep `budget.py` as the single gate, or move to the
proposal/validate split. Nothing in this branch changes `main` behaviour.
