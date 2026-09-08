# Autodev — current state

**As of 2026-09-08.** Autodev runs unattended against a trust-ladder repository,
implements a goal, runs that repository's tests, and opens a pull request. It has
landed real changes: a CI defect fix and a set of unit tests.

This document is what the system *is*, and the traps that cost time getting here.

**Also see:** `EVENT-DRIVEN.md` (what wakes it, and how it finds its own state),
`RUNBOOK.md` (operating it, and every failure signature),
`GOAL-DISCOVERY.md` (surveying a repo into goals, and goals into tasks),
`WRITING-GOALS.md` (goals it can act on, with worked examples),
`ONBOARDING-A-REPO.md` (pointing it at a new repository).

---

## Shape

```
  Forgejo webhook  ──►  csd-forge-events serve  ──►  a sentinel file
  (PR / status / push)   (:9119, verifies HMAC)      /var/lib/autodev/events
                                                            |
                        autodev-tick.path (systemd) ◄───────+
                                |
                                v
  csd-autodev-loop --once   (also: an hourly net, and `systemctl start` by hand)
        |
        +-- drain_events       consume the sentinels — which is what re-arms the
        |                      path unit. The event says WHEN; the forge is still
        |                      asked WHAT.
        +-- discover_inflight  open autodev/* PRs, from the FORGE. A lost
        |                      heartbeat used to mean a second PR beside the first.
        +-- sync_worktree      on a MERGE: default branch, ff, prune, close the
        |                      goal. While a PR is open: stay on its branch.
        +-- next_open_goal     from GOALS.md (a markdown table)
        +-- repo_context       the file listing, the files the goal names, the
        |                      tests that import them, the config fixtures, and
        |                      the repo's OWN conventions (discover_docs)
        +-- csd-gateway-complete --> LiteLLM gateway --> vLLM
        +-- apply_proposal     writes ONE file
        +-- run_repo_gates     the repo's own pytest. FAILS = no push.
        +-- csd-autodev-git    branch autodev/<goal-id>, commit, push
        +-- csd-autodev-forgejo  open the PR
        +-- csd-ci-scrutiny    CLEAN / SUSPECT / FAIL before any merge
```

## Identity and containment

Runs as **`svc-autodev`** (uid N). Verified by observation, not by reading the
unit:

| | |
|---|---|
| escalation | `sudo -n` → "a password is required" |
| operator vault | refused |
| the other agent's vault | refused |
| its own vault | `/var/lib/autodev/.secrets`, works |
| its own harness | **read-only** (`root:svc-autodev`, `drwxr-x---`) |
| the operator's repos | `git` denied on `tzervas/{mycelium,CogSynDelta,csd-autodev}` |
| sandbox | `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ReadWritePaths` = its home + the GPU request dir |
| login shell | `/usr/sbin/nologin` — no interactive session, on either agent account |

It develops in `cabal-collective/*` — its own copies — and may write anywhere in a
target repo **except `.git`**. Traversal and absolute paths are refused
everywhere.

Credentials reach it through `secret exec` from its own vault, so neither the
gateway key nor the Forgejo token is ever on a command line.

## Running it

```bash
sudo systemctl start autodev-tick.service      # one tick, watch it
sudo systemctl enable --now autodev-events.service autodev-tick.path \
                            autodev-tick.timer  # unattended
sudo systemctl stop autodev-tick.path autodev-tick.timer autodev-events.service
sudo journalctl -u autodev-tick -f             # what it is doing
```

**Events, not a clock.** The five-minute timer was wrong in both directions: it
idled for up to five minutes after a merge, and spent 287 of every 288 daily ticks
concluding nothing had changed. The timer survives at **one hour**, as a net for a
webhook delivery the forge gave up retrying — not as a schedule.

A **oneshot per event**, not a long-lived worker: each tick is a fresh process, so
a hang costs one tick rather than the loop, and stopping is stopping. The unit is
system-level with `User=svc-autodev` rather than a user unit, because the agent
has no lingering session — giving it one would let it start itself. The operator
owns the switch.

The receiver **never runs the loop**: it verifies an HMAC and writes a file, and
systemd decides what that means. A receiver that executes what it receives is
remote code execution with extra steps.

## Goals

`/var/lib/autodev/stateGOALS.md`, a markdown table. The picker matches a row
containing `| G-` and `| open |`; a bullet list is silently ignored.

**A goal must describe behaviour that exists.** The first `G-VAL-1` specified
schema validation that `validate_catalog` does not perform; the implementer
faithfully wrote tests for it and three failed. Read the source before writing the
goal.

**A goal must be specific enough to act on.** Given "add tests for untested
branches" the model declined and explained it could not know which branches were
untested. That was correct. Name the function and what the test must prove.

**A goal must carry what the call chain requires.** `validate_catalog` reaches
`catalog["trust"]` two calls down; without saying so, every generated fixture
raised `KeyError`.

## The gates, and why each exists

| gate | exists because |
|---|---|
| HMAC on every delivery | port 9119 is reachable by anything on the LAN, and a sentinel starts an agent that pushes code |
| `run_repo_gates` before push | PR #1 was opened with **three failing tests** — "applied" meant "the file was written" |
| `csd-ci-scrutiny` before merge | a PR once carried a green tick with 8 status contexts where trunk PRs had 19; the test job had not run |
| protected-ref push refusal | the standing branch-and-PR rule, enforced by the tool rather than by remembering |
| path traversal refusal | a storage or patch sweep is exactly what eats something important by accident |

`csd-ci-scrutiny` returns **CLEAN / SUSPECT / FAIL**. It compares what *ran*
against what the repository *declares* — comparing runs to runs can never reveal a
workflow that never dispatched. Only CLEAN merges; SUSPECT surfaces the reason
rather than defaulting either way, because a skipped check sometimes should be
skipped and that is a judgement.

## Traps that cost real time

- **Reasoning models emit nothing when truncated.** `coder-deep` spent all 32768
  tokens reasoning and returned zero characters. `reasoning_effort` is set
  per-role at the gateway: `none` for implementation, `high` with a `max_tokens`
  floor for deliberation. Thinking must be off, or have room to *finish*.
- **`_run` capped output at 8000 chars.** Fine for git; it truncated every
  non-trivial patch mid-string. The model call now passes 400000.
- **A whole file inside a JSON string is fragile.** One bad escape in 10 KB
  invalidates everything. The reply format is `PATH:` plus a fenced block; JSON is
  still accepted.
- **Truncated JSON fixtures are worse than none** — they look like a complete
  object with fewer keys. Oversized ones are abridged structurally, keeping every
  top-level key.
- **A deleted remote branch strands the worktree.** `delete-on-merge` removes it
  on every landed PR; the next tick merged a ref that no longer existed.
  `sync_worktree` prunes first.
- **`default_delete_branch_after_merge` does not fire on an API merge.** It sets
  the UI checkbox default. An API merge must pass `delete_branch_after_merge`.

## Runner topology

CPU work runs on an **instance-level** runner on homelab — every repo on the
forge, so a new repo is covered on creation. GPU work stays at the GPU host
(`host-gpu-b-gpu`, `host-host-gpu-b`).

A workflow asking for a label combination no runner offers is queued forever and
reports as `skipped`, which reads as success. `abliterate.yml` did exactly that
(`gpu` + `host-homelab`, which cannot coexist); autodev fixed it in PR #4.

## What it has landed unattended

Rung 0 (`cabal-collective/abliterate-harness`), 2026-09-08, event-driven end to
end. Every merge below was gated CLEAN by `csd-ci-scrutiny` on genuinely green
checks, verified against the forge rather than the goals file:

| PR | goal | what landed |
|---|---|---|
| #4 | G-CI-1 | the impossible `runs-on` label set |
| #8 | G-FIT-1 | tests for `kv_gib`, `total_gib`, `fits_host` |
| #9 | G-TRUST-1 | tests for `license_ok` and `trust_reason` |
| #10 | G-SCHEMA-1 | `validate_catalog` now enforces `CATALOG_SCHEMA` |
| #11 | G-SCHEMA-2 | the tests for that enforcement |

The repo's suite went from **11 tests to 26**, all passing on main.

**The caveat that produced #11, recorded rather than glossed.** G-SCHEMA-1 asked
for a behaviour change *and* its tests. `apply_proposal` writes one file per
tick, so only the library changed — and the goal still closed, because the half
that landed passed CI. Nothing failed and nothing said a word. Split into
G-SCHEMA-2, autodev then landed the missing tests in about forty seconds. The
rule is now written down: "One goal, one file" in `WRITING-GOALS.md`.

G-TRUST-1 is the other one worth reading: its implementer assumed `license_ok`
returned a tuple when it returns a bool. The gate refused the push three times,
each refusal emitted a `retry` event, and the fourth attempt landed — which is
the self-correcting loop working, not a fluke.

## Known open

- The other four ladder repos have no goal sets.
- `csd-ci-scrutiny` does not know which EVENT a commit was scrutinised for, so
  pointed at a merge commit on main it reports a `pull_request`-only workflow as
  never having run. Harmless in the loop, which only ever scrutinises a PR head,
  but misleading by hand.
- Observability is `journalctl`, the heartbeat JSON, the receiver's
  `events/received.jsonl`, and the lab console on `:9118`. Not yet wired into a
  WebUI.
- `reviewer` routes to `nemotron-9b`, which is not resident on the 3090 Ti. It has
  no fallback **by design** — a reviewer that degrades to the implementer's own
  model returns a correlated review labelled independent.
