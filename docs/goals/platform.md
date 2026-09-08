# Platform goals — the long track

These are about the machine that builds, not about any one repository. They are
deliberately larger than a rung goal: each is a real outcome, decomposed into
tasks sized for one PR each. Work them when a ladder repo is idle.

Every one of these came from something that actually broke or was measured
today. None is speculative.

## L1 — The reviewer is genuinely independent

**Why.** `reviewer` currently resolves to qwen3.5-4b on host-gpu-b while the
implementer is qwen3.5-9b on host-a. Different weights, size and host — but
the same family and training lineage, so a shared blind spot is plausible. The
independence claim is real but weak, and it is the only thing standing between a
green suite and an unattended merge.

| id | task | state |
|---|---|---|
| L1-1 | Measure the disagreement rate. Run `csd-autodev-review` over the last 10 merged PRs with the goal each was written for, record every verdict, and report how often the reviewer disagrees with the merge that happened. A reviewer that approves everything is not a gate; a number is the only way to know which we have. | open |
| L1-2 | Quantize nemotron-9b to AWQ INT4 so it fits beside qwen3.5-9b on the 3090 Ti (~4.5 GiB free today), and point `reviewer` back at it. Different lineage is the real fix. | open |
| L1-3 | Make the review prompt ask for the ONE most likely defect rather than a four-part checklist, and compare verdict quality against L1-1's baseline on the same PRs. Same seed, same PRs — one change measured. | open |

## L2 — Goals come from evidence, without a person in the loop

**Why.** Every goal so far was written by hand from a survey. `csd-autodev-intake`
turns a finding into a checked goal row, but nothing yet turns a SURVEY into
findings. That is the step that makes onboarding a repo self-serve.

| id | task | state |
|---|---|---|
| L2-1 | Have `csd-repo-survey --json` output feed the planner to produce candidate FINDINGS (plain sentences), one per piece of evidence, with no goal wording. Findings are cheap to check and wrong ones cost nothing. | open |
| L2-2 | Pipe each accepted finding through `csd-autodev-intake` and report how many produce a goal row that passes its checks unedited. Below 50% the loop is not ready for stage 4 of GOAL-DISCOVERY.md's graduation path. | open |
| L2-3 | Only once L2-2 is boringly good: let autodev survey a repo, propose goals, and queue the ones that pass, with the operator reviewing the queue rather than writing it. **Do not skip to this.** | open |

## L3 — The loop is observable without journalctl

**Why.** Watching it today means reading `journalctl` and a heartbeat JSON. The
platform's stated design is one API with many clients, and there is no client.

| id | task | state |
|---|---|---|
| L3-1 | Expose the loop's state — current goal, in-flight PR, last verdict, retry budget, pending events — as JSON on the existing lab console at `:9118`, reading the same files the loop writes. Read-only. | open |
| L3-2 | Add a `/events` view rendering `events/received.jsonl` so "why did it wake" is answerable without ssh. | open |
| L3-3 | Wire L3-1 into Open WebUI as a page, so the operator can steer from the same place they chat. | open |

## L4 — Onboard the rest of the collective

**Why.** Twelve repos, one on the ladder. Each new repo is a rung, and
`ONBOARDING-A-REPO.md` already says how.

| id | task | state |
|---|---|---|
| L4-1 | rung 1: `nl-router` — goals written, see `GOALS-nl-router.md`. | open |
| L4-2 | rung 2: `mcp-vacuum` (Python, 2.4 MB). Survey first; do not write goals from memory. | open |
| L4-3 | rung 3: `homelab-hosts` (Shell). The gate is shellcheck, not pytest — `run_repo_gates` needs to learn that before this rung can run. | open |
| L4-4 | Audit every collective repo's `cabal-self-review.yml` for the `github.server_url` guard that skipped rung 0 for a week. `nl-router` has it. Assume the others do until checked. | open |

## L5 — Close the loop on its own defects

**Why.** Five defects were found today by a person reading output. Each became a
fix by hand. The system should absorb its own findings.

| id | task | state |
|---|---|---|
| L5-1 | Teach `csd-ci-scrutiny` which EVENT a commit was scrutinised for, so a `pull_request`-only workflow is not reported as missing on a push to main. It is misleading by hand today, harmless in the loop. | open |
| L5-2 | Have a failed tick file its own finding through `csd-autodev-intake` when the same gate error repeats across the retry budget — the loop currently gives up silently and waits for a person. | open |
| L5-3 | Record every verdict, retry and merge as a row the console can chart, so "is it getting better" is a question with an answer. | open |
| L5-4 | ~~The gate cannot run a non-pytest suite.~~ **Done** — `gate_command()` now selects `cargo test` for a Cargo.toml, `shellcheck -S warning` for a shell repo (including extensionless scripts with a shell shebang), and pytest otherwise. `run_repo_gates` no longer requires a `tests/` directory, which had made every non-Python repo report a pass on work nothing checked. | done |
| L5-6 | `next_open_goal()` falls back to the CSD-era `'region-pretrain'` when no row is open, so a ladder repo whose goals are all done would be handed a goal that makes no sense for it. Make the fallback conditional on there being no TARGET_REPO. Found while writing the empty-queue guard for emit_continue, which the fallback silently defeated. | open |
| L5-5 | Every repo hits the same bootstrap knot: `cabal-self-review` fails on a hardcoded URL and an unset token, so the PR that would fix it also fails, and an operator has to merge one PR by hand per repo. Fix it once at the source — a shared workflow, or a template the onboarding step rewrites — so rung 2 onward needs no hand merge. | open |

## L6 — Decompose the four agents into the twelve roles

**Why.** `docs/AGENT-ORGANISATION.md` is the target: specialised roles, feedback
that returns a defect to the layer that caused it, durable handoffs, and
escalation instead of a retry counter. Four of the thirteen roles run today.

Ordered cheapest-and-most-load-bearing first, because everything downstream needs
agents to be addressable and able to hand work over.

| id | task | state |
|---|---|---|
| L6-1 | ~~Role registry: roles as data, with what each owns, may change, and escalates to.~~ **Done** — `config/roles.json`. | done |
| L6-2 | ~~Mailbox: durable inbox/outbox with the full envelope, atomic claim.~~ **Done** — `scripts/csd-agent-mail`. | done |
| L6-3 | ~~Style repair: findings applied, never returned.~~ **Done** — `scripts/csd-style-repair`. | done |
| L6-4 | Stub audit: every stub carries GOAL / BLOCKED / UNBLOCK / SEQUENCE, its goal must exist, and a stub whose goal is closed is a failure. | open |
| L6-5 | Wire the mailbox into the loop: a reviewer's REQUEST_CHANGES becomes a message to the role that OWNS the defect, using `defect_routing`, instead of always going back to the implementer. | open |
| L6-6 | Replace the retry counter with the escalation ladder: attempt → retry → change approach → peer review → escalate to the owning role → alternate model or GPU. Each step must change SOMETHING; repeating a strategy is a loop, not persistence. | open |
| L6-7 | Split `test-engineer` out of `implementer`. The gate exists (`run_repo_gates`) but nothing authors the tests, which is exactly how G-SCHEMA-1 landed a behaviour change with none of them. | open |
| L6-8 | Split out `doc-engineer` and add the documentation gate. | open |
| L6-9 | `architect` and `product`, and the `product ⇄ architect` loop that settles requirements against interface contracts before anything is decomposed. Hardest to evaluate, so last of the core set. | open |
| L6-10 | Orchestrator: choose model, quantisation and GPU per task from the registry, rather than one alias per role. Includes reclaiming a card for project compute. | open |
| L6-11 | Assert CI/local parity mechanically. `gate_command()` reads the repo's CI command now, but nothing proves the two run the same checks — and two definitions of correctness is how a repo goes green in one place and broken in the other. | open |
| L6-12 | Continuous replan: after a merge, reassess priorities, assumptions, and whether existing tasks are still valid — preserving what still holds rather than rewriting it, with an explicit justification for any change. | open |

## L7 — Onboard every repo to the house standard

**Why.** `config/house-rules.json` declares the standard; `csd-repo-onboard`
audits against it and names the first thing to fix. Adoption is expected to go
red — configuring a linter properly surfaces real findings, and that is the
point. Fix the first, re-run, get the next.

autodev itself went through it: 16 findings → lint clean → 10 files reformatted
→ **adopted, 0 findings**. It caught a dead assertion (`assert ... or True`) that
had been sitting in the suite. That is the shape every repo below should follow.

| id | task | state |
|---|---|---|
| L7-1 | ~~Declare the house standard per language, and the one CI pattern.~~ **Done** — `config/house-rules.json`. | done |
| L7-2 | ~~Build the intake audit that reports one finding at a time.~~ **Done** — `scripts/csd-repo-onboard`. | done |
| L7-3 | ~~Adopt it in csd-autodev.~~ **Done** — 16 findings ground to zero. | done |
| L7-4 | Adopt in `nl-router`. Audit says 6 findings, starting with `[tool.ruff.lint]` absent. It also has no `uv.lock` despite its CI running `uv run`. | open |
| L7-5 | Adopt in `abliterate-harness` (rung 0). Its suite is unittest, not pytest — decide whether the standard accommodates that or the repo moves. | open |
| L7-6 | Teach `csd-repo-onboard` to WRITE the config it recommends (`--apply`), so onboarding a repo is one reviewed PR rather than a hand edit per file. Gated behind the same review as everything else. | open |
| L7-7 | Build the Rust arm: `context-mcp`, `mycelium` and `peft-rs` are Rust, and nothing verifies clippy/rustfmt/toolchain-pin today. Needs `fleet-ci-base` to carry the toolchain. | open |
| L7-8 | Bake the declared toolchains into `localhost/fleet-ci-base:1` so `no_runtime_installs` is satisfiable. Today a repo obeying that rule has no way to get `uv`. | open |
| L7-9 | Assert CI/local parity mechanically: run the repo's CI command and the local gate over the same tree and require the same verdict. Two definitions of correctness is how a repo goes green here and broken there. | open |
| L7-10 | Wire `csd-repo-onboard --goals` into the loop so a newly-onboarded repo self-queues its own adoption findings, one PR at a time, through the normal review and gates. | open |

## L8 — Security scanning, with the operator's severity policy

**The blanket rule.** CRITICAL and HIGH are must-fix with no acceptance path —
an agent may not annotate its way past one. MEDIUM is posture-dependent and
blocks a release. LOW is acceptable. For a release, every *tractable* medium and
low is fixed; one an attacker cannot reach is allowed **with an annotation**, so
a release is never blocked on an unreachable low and the decision stays
revisitable. See `config/security-policy.json`.

| id | task | state |
|---|---|---|
| L8-1 | ~~Declare the policy and the acceptance-annotation contract.~~ **Done**. | done |
| L8-2 | ~~Build the adoption workflow so autodev runs it, not the operator.~~ **Done** — `scripts/csd-adopt`, wired to the empty-queue path. | done |
| L8-3 | `csd-security-scan`: run the self-hosted scanners, normalise findings, apply the policy. An absent scanner is reported ABSENT, never counted clean. | open |
| L8-4 | Add `S` (flake8-bandit, built into ruff) to the required select, so Python SAST comes with the house lint standard rather than a separate tool. Expect findings — that is adoption. | open |
| L8-5 | Bake the scanners into `fleet-ci-base:1`. It has gitleaks and trivy; semgrep, osv-scanner, cargo-audit and ruff are missing, so CI cannot run the policy the merge gate depends on. | open |
| L8-6 | Audit the acceptance annotations: every `SEC-ACCEPTED` must carry SEVERITY, REACHABLE and REVISIT, REACHABLE must argue from architecture rather than from the code being careful, and none may sit on a critical or high. | open |
| L8-7 | Wire the security gate ahead of the review gate in `land_pr`, so a critical blocks before a model is asked for an opinion. | open |
| L8-8 | A release mode for the whole pipeline that applies the release gate rather than the merge gate, and emits the annotated-exception list as a release artefact. | open |

## L9 — File extensions, and the CI base image

**Why.** Tooling keys off extensions. ruff walks `*.py`; every script in this
repo is extensionless, so twenty Python files went unlinted while the repo
reported clean. The `extend-include` list I added is a **patch over the real
problem** — the fix is the extension.

| id | task | state |
|---|---|---|
| L9-1 | Rename the 20 Python scripts in `scripts/` to `.py`. Cross-cutting: the deploy wrapper, the systemd units, the rsync paths, `scripts/gate`, every `ROOT / "scripts/..."` reference and the tests all name them. One PR, or it half-lands. | open |
| L9-2 | Provide extensionless CLI entry points for the ones invoked by name (`csd`, `csd-adopt`), via `console_scripts` or symlinks — a rename must not break the operator's muscle memory. | open |
| L9-3 | Once L9-1 lands, drop `extend-include` from `pyproject.toml`. Keeping it after the rename leaves the patch in place to rot. | open |
| L9-4 | Extend `csd-repo-onboard` to flag any file whose shebang disagrees with its extension, per the `extensions` map in the house rules. | open |
| L9-5 | Bake the toolchains into `fleet-ci-base:1`: uv, ruff, semgrep, osv-scanner, cargo + clippy + rustfmt, shellcheck, shfmt. Runtime installs are tolerated now, so this is an efficiency goal rather than a blocker — but CI cannot run the security policy the merge gate depends on until it lands, and a gate that cannot look is a gate that passes. | open |
| L9-6 | The 55 remaining lint findings in `scripts/`, one PR each. `csd-lab-console` is the hard one: it embeds JavaScript in Python strings, so its 200+ character lines need judgement rather than a formatter. | open |
| L9-7 | Address the 93 medium security findings for a release: 74 ruff `S` rules, 17 semgrep, 2 osv. Each is either fixed or annotated `SEC-ACCEPTED` with SEVERITY, REACHABLE and REVISIT — and REACHABLE must argue from architecture, not from the code being careful. | open |

## L10 — Mycelium prerequisites

Chosen over CSD on evidence (see `config/ladder.json`). Two things must exist
before the pipeline can work it unattended.

| id | task | state |
|---|---|---|
| L10-1 | **Stand up CI on the Forgejo copy.** `cabal-collective/mycelium` has ONE Actions run ever, a failed advisory `release-dry-run`; `tzervas/mycelium` has zero. All real CI is GitHub Actions, and the standing rule is Forgejo for dev/CI. Without this the merge gate has nothing to read. | open |
| L10-2 | **Resync the Forgejo copy.** Its HEAD equals GitHub `main`, GitHub `dev` is 7 commits ahead, and the repo description claims it is newer than GitHub. It is not. A pipeline working a stale mirror produces conflicts nobody asked for. | open |
| L10-3 | Fix the one failing test: `mycelium-l1::compiler_stage1 token_myc_keyword_set_matches_the_rust_oracle` — a differential oracle fed a garbled non-UTF8 identifier. Self-contained, reproducible, a good first real task. | open |
| L10-4 | The GitHub merge gate is 63 min median, 200 min max. If any part of the gate stays on GitHub, the ladder's advance timeout and the CI watcher deadline both need raising from 30 min or every merge times out. | open |
| L10-5 | Triage the 139 open issues: 39 are `status:needs-design` and are NOT autonomous work — they are language-design decisions. Split the ~100 that are from the ones that need the operator, so the pipeline never picks one it cannot finish. | open |

## L11 — embeddenator is not a target

`cabal-collective/embeddenator` was scheduled next and **is empty**: 0 Rust
files, 0 `Cargo.toml`, 11 component directories that are broken gitlinks with no
`.gitmodules` anywhere in history, 5 shell scripts, 0 CI runs, one doc commit
since July. Forgejo reports 577 MB; the actual clone is 1.4 MB — **that column is
a stale metric and should not be trusted for any repo.**

The real code moved to `aphelion/embeddenator-*`, eleven Rust repos last touched
2026-09-02. Mycelium's own ADR-004 already ruled embeddenator prior art rather
than a prerequisite, and built `crates/mycelium-vsa` from scratch instead.

| id | task | state |
|---|---|---|
| L11-1 | Operator decision: drop embeddenator, or retarget the work at `aphelion/embeddenator-*`. The monorepo documents its own obsolescence and nothing in the ladder should wait on it. | open |

## L12 — Autodev cannot create repositories, and two things need it

Measured 2026-09-08: `POST /orgs/cabal-collective/repos` returns **403** for both
the operator token (`git/tzervas-forgejo`) and the agent token (`forgejo/token`)
— *"token does not have at least one of required scope(s): [write:organization]"*.
Both can read the org and reach its repos; neither can create one.

This is not a small gap. It blocks:

- **the doc sanitisation** — autodev is git-denied on `tzervas/*` by design (it
  develops only in the collective org, which is the containment working), so
  sanitising `csd-autodev`'s own docs needs a collective copy that nobody can
  currently create;
- **the demo applications**, which are entire new repositories by definition.

| id | task | state |
|---|---|---|
| L12-1 | Operator: mint a Forgejo token with `write:organization` for the agent, or create `cabal-collective/autodev` by hand. Everything below waits on one of those. | open |
| L12-2 | Once created, seed `cabal-collective/autodev` from `tzervas/csd-autodev` and point the ladder at it, so autodev sanitises its own documentation through the normal gates. Goals are already written: `docs/goals/csd-autodev-sanitise.md`, nine of them, one file each. | open |
| L12-3 | Teach the loop to create a repository when a goal calls for one, gated on the same review as everything else. A pipeline that must ask a person for a repo cannot build demo applications unattended. | open |
| L12-4 | Re-probe the token's scopes as part of onboarding, so a missing capability surfaces when a repo is added rather than when work stalls against it. | open |
