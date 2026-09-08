# The agent organisation

**Target state.** A closed-loop autonomous R&D and engineering team: specialised
agents with bounded responsibilities, structured handoffs, feedback loops that
return a defect to the layer that caused it, and mandatory delivery gates.

**Today.** Four roles, one linear path, a retry counter, and no durable
messaging. This document is the map from here to there, and it is written so the
gap is visible rather than glossed.

---

## What exists, and what it is missing

| target capability | today | gap |
|---|---|---|
| 12 specialised roles | 4 (implement, plan, review, utility) | roles are not modelled as objects at all |
| agent identity ≠ model identity | an env var per role, hardcoded to one gateway alias | no registry, no per-task selection |
| structured handoffs | a dict in `heartbeat.json`, overwritten each tick | no inbox, no durable envelope, no addressing |
| defect returns to its layer | every failure returns to the implementer | no notion of which layer owns a finding |
| escalation | **built**: retry, then ask a peer, then set aside with the row still open | the peer is one role (`plan`), not chosen per defect |
| gates | tests + CI + one reviewer | no test-engineering, docs, UI/UX or DI/DX gate |
| CI/local parity | different commands entirely until today | `gate_command()` reads the repo's CI; not yet verified equal |
| continuous replan | goals are a static hand-written table | nothing reassesses the plan after a merge |

Nothing below should be built all at once. The ordering that matters is: **the
registry and the mailbox first**, because every other role depends on being
addressable and on being able to hand work over.

---

## Roles

A role is a **contract**, not a model. It declares what it owns, what it may
change, which gate it satisfies, and what it escalates to.

| role | owns | may change | gate it satisfies |
|---|---|---|---|
| `analyst` | repository state, intent, gaps, context for everyone else | nothing | — |
| `ideator` | candidate improvements, features, research directions | nothing | — |
| `planner` | goals, milestones, priorities, sequencing | the goals file | — |
| `decomposer` | goals → independently achievable tasks | the goals file | — |
| `product` | user stories, requirements, scope, definition of done, success AND kill criteria, acceptance criteria | requirements docs | acceptance criteria exist |
| `architect` | system boundaries, interfaces, APIs, data models, constraints | design docs, interface stubs | design is coherent |
| `implementer` | code satisfying an approved design | source | implementation complete |
| `reviewer` | correctness, quality, maintainability, architectural consistency, regressions | nothing | code review passes |
| `test-engineer` | unit, integration, regression, system tests | test files | required tests exist and pass |
| `doc-engineer` | inline, API, architecture, user, developer docs, examples | docs | documentation complete |
| `ux` | usability, accessibility, interaction design (UI projects) | UI source | UI/UX review passes |
| `dx` | APIs, SDKs, CLIs, config, error messages, install, debugging, extension points | those surfaces | DI/DX review passes |

**Roles are logical.** Several share a model; a hard task gets a different model,
quantisation, context size or GPU. `reviewer` and `implementer` must never
resolve to the same model — `csd-autodev-review` already enforces that and
refuses to run otherwise.

## The pipeline

```
analyst → ideator → product ⇄ architect → decomposer → implementer
                                              ↓
                                  reviewer → test-engineer → doc-engineer
                                              ↓
                                    ux / dx (when applicable)
                                              ↓
                                     final validation → merge
                                              ↓
                                     planner reassess → next task
```

`product ⇄ architect` is deliberately a loop, not a step: requirements and
interface contracts settle against each other before anything is decomposed.

### Feedback returns to the layer that caused it

This is the property that makes the pipeline more than a queue.

| finding | returns to | not to |
|---|---|---|
| the code is wrong | `implementer` | — |
| the interface is ambiguous | `architect` | the implementer, who would guess |
| the requirement was unclear | `product` | the architect, who would invent one |
| the test proves the wrong thing | `test-engineer` | the implementer |
| a test reveals a design fault | `architect` | the implementer, who would patch around it |
| the docs cannot describe it clearly | `architect` or `product` | the doc engineer |
| the error message is unusable | `dx` | — |

A defect patched at the wrong layer is the failure mode this prevents: the
implementer working around an architectural fault produces code that passes every
gate and encodes the fault permanently.

After a correction, **the downstream stages that were invalidated run again** —
not the whole pipeline, and not nothing.

## Gates

A change merges only when **all applicable** gates pass. Not-applicable must be
recorded as such, with a reason, so a silently skipped gate is impossible.

1. implementation complete against the approved design
2. requirements and acceptance criteria satisfied
3. code review passed
4. required tests exist and pass
5. documentation complete
6. UI/UX review passed *(if the project has a UI)*
7. DI/DX review passed *(if the project has a developer-facing surface)*
8. local validation passes
9. CI passes
10. no unresolved blocking findings

**CI must be a near one-for-one reproduction of local validation.** Two different
definitions of correctness is how a repo ends up green in one place and broken in
the other — `gate_command()` now reads the repo's own CI command for exactly this
reason, and parity should be asserted, not assumed.

## Escalation, not a retry counter

A bounded number of *equivalent* attempts, then a deliberate change of strategy:

```
attempt → retry → change approach → peer review → escalate to planning/architecture
        → alternate model or GPU → resume
```

**The budget bounds one agent's stretch at one approach. It does not bound the
effort.** This distinction is the whole design, and getting it wrong is what the
loop used to do: `emit_retry` returned `ok: false` at the budget and the goal sat
untouched until a person looked at it. Bounded, and also given up on.

What happens now, in `retry_or_escalate()`:

| attempts | what happens |
|---|---|
| under the budget | the same agent tries again — cheapest first, and most failures are transient |
| at the budget | `ask_for_help()` puts the failures to a **different** agent at a different role and budget, `post_mail()` records the exchange in the durable mailbox, and the guidance comes back as `approach_note` |
| after guidance | the within-agent counter **resets** — a new approach earns a full stretch — and a `continue` event wakes the loop immediately |
| after `MAX_ESCALATIONS` | the goal is **set aside, not abandoned**: the row stays `open`, the blocker is written onto it, and the queue moves to other work and returns later |

Nothing in that table stops. The guidance reaches the implementer's brief the
same way a reviewer's objection does, and it is advice rather than an order —
the implementer may say in one line why the advice is wrong and do the thing it
believes is right, because the peer is another fallible model.

Repeating an identical strategy is not persistence, it is a loop. What travels at
each step is a **structured stuck-report**, not a plea:

> I am stuck on this. Here is what I attempted, here are the failures and the
> evidence, and here is the specific point where I need help.

That package goes to another agent — on the other GPU, so the second opinion
comes from different weights — which may independently conclude the problem
belongs to Planning, Architecture or Testing rather than to the implementer at
all.

### Two agents, two cards

Which card an agent runs on is not a deployment detail here, it is what makes
escalation affordable.

| card | role | why |
|---|---|---|
| 3090 Ti | **implementer** | the swap-out card: whichever model is doing the work holds it, and it changes per task |
| 5080 | **helper / coordinator** | resident, and never the implementer's model — it answers *while* the implementer keeps its weights loaded |
| 1080 Ti | **RAG** | retrieval and indexing, deliberately off the two reasoning cards |

If the implementer and the helper share a backend, asking for help either queues
behind the work it is meant to unblock or forces a model swap on the card in
use — so the cheapest moment to get unstuck becomes the most expensive one. That
was the arrangement until this was measured: `coder-deep` (implementer) and
`planner` (then the escalation target) were both served from the same host, and
nothing said so.

Nothing in this repository can enforce it, because the alias-to-host mapping
lives in the gateway and can be repointed without any code change here. So it is
checked instead:

```bash
csd-gateway-complete cards      # exits non-zero if the split has collapsed
```

The helper is deliberately **not** a copy of the implementer. A duplicate gives
no second opinion, only a second bill.

## Messaging

Each agent has a durable **inbox and outbox**. Not shared context: context is
consumed and overwritten, and a handoff must survive until the recipient is ready
for it. Today's `heartbeat.json` is exactly the anti-pattern — one dict,
rewritten every tick.

An envelope carries: originating agent · target agent or role · repository and
worktree · task or issue · current state · relevant artefacts · decisions already
made · attempts performed · evidence and failures · requested action ·
blocking/non-blocking · expected response.

## GPUs are a pool, not two seats

The 3090 Ti (24 GB) hosts primary task models and swaps between them; the 5080
(16 GB) provides complementary capacity and is the natural home for escalation
and second opinions, because a different GPU means different weights. **Either
card can be reclaimed** for project compute — a VSA or CSD validation run is a
first-class use of the GPU, not an interruption.

The orchestrator selects on task type, model capability, VRAM, context need,
quantisation, GPU architecture, expected runtime, current utilisation, and
whether project computation needs the card. This is why role identity must be
separate from model identity.

---

## Unified style, and the stub contract

Two rules the pipeline enforces rather than complains about.

### Style is fixed, never a refusal

A style finding is **routed and repaired**, not returned as a blocking objection.
The repo's own formatter and linter run, the fix is applied, and only a
disagreement the tools cannot resolve becomes a finding for a human. A pipeline
that halts on formatting is a pipeline that does not run.

### Stubs are allowed — annotated, sequenced, and tracked to completion

Planning forward is legitimate: interface stubs, unimplemented API surfaces and
scaffolding are how an architecture gets expressed before it is built. What is
**not** legitimate is a stub that quietly becomes permanent.

Every stub carries a machine-readable annotation:

```python
def reindex(store: Store) -> None:
    """Rebuild the index from the source documents.

    STUB: not implemented.
    GOAL:     G-INDEX-3
    BLOCKED:  needs the Store.iter_documents contract from G-INDEX-1
    UNBLOCK:  land G-INDEX-1, then this becomes a 30-line implementation
    SEQUENCE: after G-INDEX-1, before G-INDEX-4
    OWNER:    implementer
    """
    raise NotImplementedError("G-INDEX-3")
```

The rules:

- a stub without `GOAL`, `BLOCKED`, `UNBLOCK` and `SEQUENCE` **fails the gate**
- its `GOAL` must name a row that exists in the goals file
- `raise NotImplementedError` beats `pass` or `return None`: a silent stub is
  indistinguishable from a working function at runtime
- the stub audit runs every cycle, so a stub whose blocker has landed becomes an
  actionable task automatically rather than waiting to be noticed

The point is that a stub is a **scheduled** piece of work with a stated
dependency, not a comment that outlives everyone's memory of it.

**An intentional no-op is not a stub.** `def log_message(self, *a): pass` on a
`BaseHTTPRequestHandler` is a *complete* implementation whose behaviour is "do
nothing" — three of them in this repo silence the stdlib's stderr spam. The first
audit failed all three, correctly, because the contract had no way to say so. It
also refused to exempt them on its own, which was right: inventing an exemption
is exactly the judgement a mechanical audit must not exercise.

The escape hatch is deliberately narrow and demands a **reason on the same
line** — a docstring beginning `INTENTIONAL: <why>`.

`INTENTIONAL:` on its own would be a way to silence the audit. `INTENTIONAL:
<why>` is a claim a person can read and disagree with — the same standard the
four required markers hold a real stub to. Without a reason it is still a
failure.

**The walker reads extensionless files with a Python shebang.** Every script in
this repo is extensionless, so an `rglob("*.py")` walk would have scanned **zero
files and reported the repo clean** — indistinguishable from a real pass, and the
worst result an audit can produce.

---

## Proving ground

The five initial repositories exist to validate the *system*, not to be finished.
What is being measured: agent responsibilities · task handoffs · planning quality
· decomposition quality · model selection · GPU placement · review loops · retry
and escalation behaviour · testing · CI parity · documentation · UI/UX · DI/DX ·
autonomous recovery.

Only once those are consistently good does the pipeline move to the forks of
**CogSynDelta** and **Mycelium**. Those are research-heavy and much harder — but
they carry unusually strong intent and architectural context in their existing
design docs, which is exactly what this pipeline consumes. Pointing it at them
before it is stable would waste that context on a process that cannot yet use it.

## Decomposition order

The next architectural task is deciding what stays combined. Proposed, cheapest
and most load-bearing first:

1. **role registry** — roles as data: model, effort, context, GPU preference,
   what they may change, what gate they satisfy. Everything else needs it.
2. **mailbox** — durable inbox/outbox with the envelope above.
3. **stub audit** — mechanical, no judgement, immediately useful.
4. **style repair** — mechanical, removes a class of blocking finding.
5. **test-engineer** split out of implementer — the gate that already exists in
   spirit (`run_repo_gates`) but has no author.
6. **doc-engineer** — the same for documentation.
7. **architect** and **product** — the upstream loop; the most valuable and the
   hardest to evaluate, so last of the core set.
8. **ux** / **dx** — per-project, only where the surface exists.

`analyst`, `ideator`, `planner` and `decomposer` already exist in weak form
(`csd-repo-survey`, `csd-autodev-intake`, the goals file). They become roles when
they gain an inbox and a registry entry, not before.

---

## Budgets: long context and deep reasoning, together

A reasoning model charges its thinking against the same budget as its answer, so
the two compete for one number. This cost three components before it was
understood as one problem: `reviewer` at `effort=high` timed out on any real
diff, `planner` timed out writing a single goal row, and `coder-deep` unbounded
spent 28,908 characters reasoning and returned nothing at all. Each was fixed by
hand, per model, after it broke.

**No fixed number is right for both a 400-character prompt and a 40,000-character
diff.** So the budget is computed, not configured.

| technique | what it solves |
|---|---|
| `plan_budget()` sizes from the prompt actually sent | a static budget that is wrong at one end or the other |
| degrade **effort**, never the answer | truncated artifacts — the answer is what the caller needs, the thinking is what it can afford to lose |
| **phase separation**: think, then write | high effort AND a long answer, by never putting them in the same call |
| **continuation** on `finish_reason=length` | artifacts larger than any single budget |
| retry a timeout one step down | a timeout is an over-ambitious budget, not an outage |

`complete()` distinguishes **cut off with content** (rc 9, resumable) from
**thought until the budget was gone** (rc 6, nothing to resume from). Folding
those together is what made truncation look like failure.

### The guards, which matter more than the mechanism

Both of the first attempts were wrong, and both would have failed silently:

- The de-duplication floor was 20 characters, so a repeated 15-character line was
  doubled. In Python that is a syntax error or a silently doubled statement. A
  short overlap must now be a **whole line** — a repeated newline-terminated
  fragment is a restatement; a bare `    return ` is a coincidence that would eat
  real output.
- The no-progress check compared lengths *after* de-duplication, so a model
  repeating itself in chunks too short to dedupe looked like growth. It now
  compares the returned chunk itself.

An unattended continuation loop without those burns a card until a person
notices — which is the failure the whole event system exists to remove.

Continuation runs at `effort=none`: resuming is transcription, not deliberation,
and the decisions were made in the first call.
