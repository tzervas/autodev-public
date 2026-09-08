# The decision table

The loop does not decide whether a run failed. It looks the outcome up in
`config/decision-table.json` and does what the entry says. Every decision is
either a measured value against a declared threshold, or an escalation.

**Anything not in the table stops and escalates.** That is the whole safety
property, and it is implemented as the table's *default* rather than as a
case, so a state nobody anticipated cannot fall through to "proceed".

- Table: `config/decision-table.json` (`CSD_DECISION_TABLE` overrides the path)
- Code: `src/csd_autodev/decisions.py`, `src/csd_autodev/escalations.py`
- API: `GET|POST /api/decisions`, `GET|POST /api/escalations`,
  `GET|PATCH /api/escalations/{id}`, `GET /api/escalations/stats`
- Tests: `tests/test_decision_table.py`

## The eight outcome states

The old status enum was `{running, succeeded, failed, halted}`, which
conflated outcomes demanding opposite responses. Each state below maps to
exactly one action.

| state | meaning | action | retryable |
|---|---|---|---|
| `running` | in flight | `wait` | — |
| `passed` | ran, gate cleared | `proceed` | — |
| `rejected` | ran cleanly, gate not cleared — a RESULT, not an error | `stop_record` | no |
| `inconclusive` | ran, gate could not be evaluated | `stop_escalate` | yes, after a fix |
| `crashed` | the job's own code failed | `stop_escalate` | maybe (`null`) |
| `infra_failed` | the environment died under it | `retry` | yes |
| `refused` | a fail-closed guard prevented start | `stop_record` | no |
| `halted` | operator stopped it | `stop` | yes |

Three of these carry the value the old enum destroyed.

**`rejected` vs `crashed`** separates *the experiment answered no* from *the
code broke*. Retrying a `rejected` re-runs a question that already has an
answer.

**`refused` never sits under failure.** This programme has many guards built
to fire. Filing a refusal as a failure trains the operator to ignore exactly
the signal guards exist to produce.

**`infra_failed` is earned by measurement.** `exitcode -1` and podman-socket
`context deadline exceeded` hit five job types across four PRs on 2026-09-07,
each needing a re-run rather than a code change.

## Structured detail, not a bare label

Each outcome carries the gate or guard by number, the measured value against
its threshold, the stage and step, and `retryable`. Those keys are in
`store._RUN_UPDATE_KEYS`, so they persist on the run record:

`gate`, `measured`, `threshold`, `margin`, `stage`, `step`, `retryable`,
`outcome_detail`.

### The margin is the severity

A gate missed by 0.001 and one missed by 10 points are different situations.
`decisions.margin_severity` bands `|margin| / spread`:

| band | ratio | meaning |
|---|---|---|
| `marginal` | ≤ 1.0 | inside one seed spread — the gate did not discriminate |
| `narrow` | ≤ 3.0 | a real but small miss |
| `wide` | > 3.0 | a real miss with room to spare |

A verdict inside its own spread is **not a result**: `decide()` overrides it
to `inconclusive` and escalates, whichever way it fell. That is the G35 shape
— a 0.00–10.94 point seed spread against a 5.00 point ceiling.

`decisions.margin_trend` reads the sequence, because a *shrinking* margin
across runs is progress that a bare verdict reports as another FAIL.

## Classifying evidence into a state

`decisions.classify_failure(text)` walks the ordered `classifiers.rules`;
first match wins. The needle lists live in the config so the loop, an
inspector agent and the tests all classify from one declared source.

| order | rule | outcome |
|---|---|---|
| 1 | `guard-refusal` | `refused` |
| 2 | `infra-hard` | `infra_failed` |
| 3 | `gate-not-evaluable` | `inconclusive` |
| 4 | `assertion-or-gate-miss` | `rejected` |
| 5 | `infra-soft` | `infra_failed` |
| — | fallback | `crashed` |

Hard infra signatures run **before** the result rules on purpose: a
conventions job died with `exitcode -1` *after* `cz check` had printed
success, so a log carrying both is infrastructure.

## Signals: what may drive an action

`decisions.signal_trust(name)` returns the trust class from
`SIGNAL-TRUST-TABLE-2026-09-07.md`. Every classification there is measured.

| class | action | example |
|---|---|---|
| `act` | consume | `runner.exitcode_minus_one` |
| `low_trust` | verify, then act | `recall_at_1`, a conventions lint failure |
| `audit_only` | record, cannot trigger | `loss_barrier`, `fisher_trace_peak` |
| `do_not_consume` | record only, escalate | `G29.verdict`, `G35.verdict` |
| unknown | escalate | anything else |

A `do_not_consume` gate cannot drive a decision at all: `decide(..., gate="G29")`
escalates even on `passed`, because G29 passes on 3 of 4 seeds in an arm whose
MI is exactly zero.

## Retry caps and evidence-based escalation

**Retry cap.** Two consecutive `infra_failed` at the same stage escalates,
because infrastructure that fails twice in the same place probably is not
infrastructure. The streak is per stage and must be consecutive.

**Escalate on evidence the setup is wrong**, not on a run count:

| trigger | threshold | route |
|---|---|---|
| no `passed` in K consecutive runs | K = 5 | orchestration |
| every arm passed | ≥ 2 arms | research |
| margin growing across runs | ≥ 3 points | experiment |

Every arm passing usually means the gate is not discriminating — exactly what
G29 does today.

## The escalation contract

**Route by kind, never broadcast.** Broadcasting wastes the cheaper resource
and delays the answer.

| question shape | route | goes to |
|---|---|---|
| is this true / what does the literature say | `research` | grok |
| what should we do, in what order, does this merge | `orchestration` | claude |
| broken and I cannot tell why | `experiment` | whoever can run it |
| irreversible or outward-facing | `operator` | operator |

An unrecognised kind lands on the default route and is flagged
`route_known: false`. It is never dropped.

**An escalation without its evidence is refused.** `open_escalation` raises
`EscalationContractError` when `receipt` or `log_tail` is missing, rather than
sending a round trip that will only come back asking for them. The HTTP route
returns 400.

**Every escalation is logged with its resolution.** Each one is evidence the
table is incomplete, so the table grows from real cases rather than
anticipated ones. `resolve_escalation` requires a non-empty `resolution` and
takes an optional `table_amendment`.

`GET /api/escalations/stats` reports the escalated-to-autonomous ratio, which
is the measurable signal of whether autonomy is improving. Call
`escalations.record_autonomous()` on each action taken from the table without
escalating.

## Acceptance cases

These are real diagnoses from 2026-09-07, each with a known correct action.
`tests/test_decision_table.py` asserts every one.

| evidence | state | action |
|---|---|---|
| podman socket `context deadline exceeded` | `infra_failed` | retry |
| `assert 1 == 0` with a named test | `rejected` | stop, do not retry |
| a guard refusing a budget over free VRAM | `refused` | stop, the guard worked |
| a gate whose spread exceeds its margin | `inconclusive` | stop, escalate |
| a CI status of "Has been cancelled" on a superseded sha | `infra_failed` | re-check the current head |
| an outcome that is not in the table | — | stop, escalate |

The cancelled-run row is the mechanism working: this PR's own first CI run was
cancelled by a concurrency `cancel-in-progress` when a second commit landed,
Forgejo filed it as a *failure*, and the table grew a rule from that case.
