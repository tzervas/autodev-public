# Building the autodev A2A agent — with the self-hosted models doing the work

**Plan, 2026-09-07.** Build the A2A agent that the gateway fronts, using the local
tier to write it. Mocked first, then live with a single agent.

## Is that actually feasible? Measured, yes

A real slice of this work — JSON-RPC dispatch with task state — was given to the
local tier against an acceptance test written before the model saw it.

| arm | tokens | reasoning | result |
|---|---|---|---|
| `coder-deep`, unbounded | 32768 | 32768 (100%) | **no content at all**, 492s |
| `coder-deep`, `effort=low` | 2437 | 1850 | **8/8 pass**, 35s |
| `coder-fast` (4B), `effort=low` | 1544 | 811 | **8/8 pass**, 16s |

Two conclusions that shape the plan:

1. **The 4B is enough for test-backed unit work**, at 16 seconds a module. Reserve
   the 9B for work that needs more context, not more capability.
2. **The generated code had a real semantic bug the test did not catch** — it
   returned `None` for a JSON-RPC notification *before* dispatching, so the
   notification's side effect never happened. Tests written from a contract catch
   structure, not intent. Review is not optional.

## The decomposition

Each unit is one module, one contract, one acceptance test written by the operator
side **before** the model sees it. That granularity is chosen to match what the
tier demonstrably does well.

| # | module | contract |
|---|---|---|
| 1 | `a2a/dispatch.py` | JSON-RPC envelope validation, method dispatch, error codes |
| 2 | `a2a/tasks.py` | task lifecycle: submitted → working → completed/failed, artifacts |
| 3 | `a2a/server.py` | HTTP surface: `POST /`, agent card at `/.well-known/agent-card.json` |
| 4 | `tools/worktree.py` | create, isolate, clean up an ephemeral git worktree |
| 5 | `tools/gates.py` | run the target repo's own tests/linters, parse pass/fail |
| 6 | `tools/forge.py` | branch, commit, push, open PR, read CI status, merge when green |
| 7 | `agent/runner.py` | goal → tool calls → task artifacts, via the gateway |

1–3 are pure logic with no I/O: the tier's strongest ground. 4–6 touch the world
and are where mocks matter most. 7 is the integration and is the operator's to
own.

## Phases

### Phase 0 — scaffolding (operator side)
Repo layout, the seven contracts, acceptance tests for 1–6, and the **project
management package** the agents read: goals table in the format the picker
actually parses (`| G-… | … | open |`), a definition of done, and the house rules
(no `git add -A`, branch and PR, never weaken a gate to make it pass).

### Phase 1 — mocked implementation (self-hosted tier)
One module per task, `reasoning_effort=low`, `coder-fast` first and `coder-deep`
only on failure. Verdict is pytest, never plausibility. Every module ships with
fakes for git, the forge and the model, so the whole suite runs with no network
and no GPU.

### Phase 2 — mocked end-to-end
Wire 1–7 against the fakes. A full goal → PR cycle must pass with nothing real
touched. This is where the notification-style bugs surface — a passing unit suite
does not imply a working workflow.

### Phase 3 — live, ONE agent
Single agent, real gateway, real Forgejo, rung 0 (`cabal-collective/abliterate-harness`)
only. One agent because two writing to the same forge while the workflow is
unproven turns every failure into a question of which one did it.

Live entry criteria: Phase 2 green; the agent registered and confirmed via
`GET /v1/agents` (a malformed entry is skipped **silently** at startup); its
virtual key scoped; and a kill switch that works — pause honoured, and the unit
stoppable without leaving a half-open PR.

## Known gap before live review

`reviewer` routes to `nemotron-9b`, which is not currently resident on the 3090 Ti
(`qwen3.5-9b` is). Review therefore fails loudly by design — `reviewer` has no
fallback, deliberately, because a reviewer that degrades to the implementer's own
model returns a correlated review labelled independent. Mutual review needs a
`fleet-gpu-mode` swap, and Phase 3 should run without it rather than pretend.
