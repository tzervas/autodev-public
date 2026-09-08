# The programme: what gets built, in what order, and when a repo is done

Autodev works one repository to **completion**, cuts a **Forgejo release**, and
moves on. This document is the queue and, more importantly, the **definition of
done** — without one, "fully polished" is unbounded and the pipeline never
advances past its first repo.

---

## Definition of done

A repository is **complete** when all of the following hold. Each is machine-
checkable, because a criterion only a person can judge is a criterion that
quietly never gets met.

| # | criterion | checked by |
|---|---|---|
| 1 | adopted to the house standard | `csd-repo-onboard` exits 0 |
| 2 | no critical or high security findings | `csd-security-scan` exits 0 |
| 3 | every medium is fixed or annotated `SEC-ACCEPTED` | `csd-security-scan --release` exits 0 |
| 4 | no unannotated stubs | `csd-stub-audit` exits 0 |
| 5 | the repo's own suite passes | `run_repo_gates` |
| 6 | CI green on the default branch | `csd-ci-scrutiny` = CLEAN |
| 7 | CI reproduces local validation | the parity check (L9-9) |
| 8 | no open goals in its goal file | the queue is empty |
| 9 | README states what it is, how to run it, how to develop on it | `doc-engineer` gate |
| 10 | every public surface has a docstring | the docs gate |

**Then, and only then, a release is cut.** Not to an external registry — a
Forgejo release with a semver tag, notes generated from the merged PRs, and the
gate results attached as evidence. Publishing outward is a separate decision that
stays with the operator.

### Why 8 is not enough on its own

An empty goal queue means *the goals somebody wrote* are finished, not that the
repo is done. Criteria 1–7 and 9–10 are what the queue is measured against, and
`csd-adopt` refills the queue from them. A repo leaves the queue when the
**checks** pass, not when the list is empty.

---

## The order, and why

| # | repo | language | why here |
|---|---|---|---|
| 0 | `abliterate-harness` | Python | **done** — 11→26 tests, 6 PRs merged |
| 1 | `nl-router` | Python | in progress; smallest real surface, tests already exist |
| 2 | `mcp-vacuum` | Python | Python toolchain is the one that works today |
| 3 | `tg-agent-relay` | Python | same, larger; exercises the pipeline at size |
| 4 | `homelab-hosts` | Shell | needs the shellcheck gate first (L5-4) — a deliberate stretch |
| 5 | `embeddenator` | mixed | scheduled next by the operator |
| 6 | `CSD` **or** `mycelium` | — | whichever is closer to done and higher utility; **assessed, not guessed** |

Ordering is by **tractability of the toolchain**, not by interest. Python first
because uv + ruff + pytest is proven here; Shell needs a gate that does not exist
yet; Rust needs the whole Rust arm (L9-7). Pointing the pipeline at a language it
cannot gate produces confident green on work nothing checked.

## After the ladder: demo applications

Autodev creates **new repositories** in the collective org and builds
applications that demonstrate what the completed repos can do. A demo is not a
toy: it is the first honest test of whether those repos are usable by someone who
did not write them, and it is where DX findings actually come from.

Each demo is a repo, goes through the same pipeline, and meets the same
definition of done.

## If Mycelium completes: the hyphae

Mycelium's goal is **one language with varying levels of sugaring and
desugaring** — ergonomic code and applications over the same core. If it reaches
done, the ecosystem gets libraries (**hyphae**) targeting AI/ML, each in its own
repo, each making real use of the language's distinctive features rather than
transliterating Python.

This is the bleeding-edge test of the pipeline: writing idiomatic libraries in a
language the models have never seen, against a spec that only exists in this
fleet. It is deliberately last, because everything before it is what makes it
survivable.

---

## What must exist before each stage

Recorded so the queue does not stall on a missing capability discovered late.

| stage | blocked on |
|---|---|
| rung 4 (Shell) | shellcheck gate in `run_repo_gates` — L5-4 |
| rung 6 if Rust | the Rust arm: clippy, rustfmt, toolchain pin, cargo-audit — L9-7 |
| any release | `csd-release` and a semver tagging convention |
| demo apps | autodev creating repos in the collective org |
| all of it | CI can run the security policy — `fleet-ci-base` lacks the scanners (L9-5) |

---

## Publication: two layers, on purpose

Publishing is a pipeline stage, not a one-off. It runs **both** of these, and
neither replaces the other.

| layer | what it does | what it cannot do |
|---|---|---|
| **autodev patches the source** | rewrites literals in its own docs to the committed example values, one file per PR, through the normal gates | it is a language model; it will miss one |
| **`csd-publish`** | deterministic substitution, structural redaction, then a verifier that greps its own output and refuses on any survivor | it cannot improve the source, so the debt stays |

The second is the gate; the first shrinks what the gate has to catch. Running
only the source fix trusts a model with an irreversible action. Running only the
tool leaves every doc carrying real addresses forever, one forgotten `--check`
away from exposure.

**The verifier has caught something on every run so far** — bare `fleet` in seven
files, an `svc-*` rule that undid itself, `203.0.113.` as a bare prefix, `Fleet-`
capitalised at a sentence start, and the publisher naming identifiers in its own
substitution table. That is five distinct misses in a task that looks like
find-and-replace, which is the argument for keeping the deterministic layer
permanently rather than declaring the source clean and deleting it.

### What is withheld, and why it is not a redaction problem

`docs/AUTODEV-IDENTITY-AND-SANDBOX.md` was tried with section-level redaction and
**failed**. Dropping the finding-disposition and ground-truth sections still left
41 hits of exploit-shaped content in the design chapters, including a line that
names a specific service, its weakest point, and how to reach it.

The security reasoning is not a section of that document; it **is** the document.
A fresh architecture doc covering the same ground is honest work. Redacting that
one is not.
