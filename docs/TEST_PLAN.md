# Test plan

This file records what was actually run, not what we hoped would pass. Every
"Actual" and "Status" below comes from an executed command on the current
`main` commit. To reproduce:

```bash
python -m unittest discover -s tests -v
python -m eval.run
```

Last run: 364 unit tests OK; intent-parser regression 68/68 (100.0%).

## 1. Unit / integration test cases

Grouped by module. The "Actual" column is the observed result of running that
module; the full suite was also run once as a whole (364 tests, OK).

| # | Test case | Expected behavior | Actual behavior | Status |
|---|---|---|---|---|
| 1 | NLU — device & action resolution (`test_nlu`, 38 tests) | A home command resolves the right device and actions; unknown devices are named back, not guessed | 38/38 passed | PASS |
| 2 | NLU — heat-risk wording regression | "kitchen heat risk" / "dryer adding heat" must NOT become a heating command; "heat the bedroom" must | 3 added regression tests passed | PASS |
| 3 | Phase 2 safety (FR-4/5/6) (`test_fr456`, 15 tests) | Per-action approval, revalidation, cooktop guards, append-only audit | 15/15 passed | PASS |
| 4 | Phase 2 end-to-end scenarios (`test_phase2`, 41 tests) | Two separate Tier-2 cards, expiry, quiet period, dryer restart is reminder-only | 41/41 passed | PASS |
| 5 | Chat / command split (`test_chat`, 28 tests) | Greetings get a text-only reply with no tools and no device write; commands stay on the rule path | 28/28 passed | PASS |
| 6 | Agent core (`test_agent`, 53 tests) | Intent handling, whitelist, audit, fallback | 53/53 passed | PASS |
| 7 | Agent loop (`test_agent_loop`, 53 tests) | Sensors → risk → policy → act/ask loop stays deterministic | 53/53 passed | PASS |
| 8 | Cascade vs Agent mode (`test_agent_mode`, 21 tests) | Same sentence answered by both modes; rules decide when model is absent | 21/21 passed | PASS |
| 9 | Food / scene helpers (`test_food` 35, `test_scenes` 15) | Scene and helper logic returns expected structures | 50/50 passed | PASS |
| 10 | Demo password gate (`test_auth`, 11 tests) | Off by default; wrong password rejected; signed cookie; rotation invalidates sessions; cookie flags set | 11/11 passed | PASS |
| 11 | Live notifications (`test_events`, 8 tests) | Event fan-out, unsubscribe, bounded queue, late subscriber, SSE framing | 8/8 passed | PASS |
| 12 | Intent-parser regression (`eval.run`) | ≥95% intent accuracy | 68/68 = 100.0% | PASS |
| 13 | Savings report (`test_savings`) | Energy / cost / CO2 estimates use sourced figures | Passed after merge | PASS |
| 14 | Heat & energy indices (`test_heat_energy`) | HSI / PLI indices score as specified | Passed after merge | PASS |

## 2. Manual / demo test cases (run by a human)

These cannot be honestly reported as "pass" from this environment: they need a
browser or a phone. Status is recorded as **NOT RUN** until someone actually
executes them. Do not mark them PASS without evidence.

| # | Test case | Expected behavior | Actual behavior | Status |
|---|---|---|---|---|
| 15 | Live demo opens over HTTPS | `https://fsd-home-demo.duckdns.org/?ui=v6` loads the dark v6 console | Verified reachable earlier (HTTP 200); re-check before presenting | NOT RUN (this run) |
| 16 | Password gate on public demo | With `DEMO_PASSWORD` set, an anonymous visitor sees the sign-in page; wrong password is rejected; correct password opens the app | End-to-end HTTP flow verified locally (8/8 steps); cloud config not yet applied | NOT RUN (cloud) |
| 17 | Two approval cards, oven scenario | `--scenario oven` shows two independent cards with different `approval_id`; answering one leaves the other pending | Verified in earlier walkthrough; not part of this automated run | NOT RUN (this run) |
| 18 | Voice input on phone | Tap mic, speak, stop → text appears in the input for review, not auto-sent; re-tapping keeps the draft | Fixed earlier; needs a real phone browser to re-confirm | NOT RUN |
| 19 | Push notification on phone | With alerts enabled and the page open, a Tier-2 approval shows a system notification | SSE delivery verified end-to-end locally; browser notification needs a real device | NOT RUN |
| 20 | UI version switch | `?ui=v6` loads Mission Console; switching to v5 and back works | Implemented; needs a browser to re-confirm after the latest changes | NOT RUN |

## 3. What is proven vs what is claimed

- **Proven by commands in this file:** all 364 unit tests, the 68-case intent
  regression, the auth HTTP flow, and the SSE delivery.
- **Implemented but needs a device/browser to demo:** password gate on the
  cloud host, system notifications, phone voice, UI switching.
