# What broke, and the shape of it

Every entry here is a defect this pipeline actually had, grouped by the pattern
underneath. The patterns repeat: nearly all of them are one of four mistakes
wearing different clothes, and knowing the four is worth more than knowing the
forty.

This is a working document. When something breaks in a way that is already
described here, that is a signal the fix was applied in one place and not as a
rule.

---

## 1. An allow-list you have to remember to update

The most expensive pattern, by a distance. Something enumerates what it covers,
the world grows a new member, and the check silently covers less than it claims
to — while still reporting success.

| where | what it listed | what it missed |
|---|---|---|
| `pyproject.toml` ruff | `*.py` | every script in `scripts/`, all extensionless — 20 files unlinted while the gate said "clean" |
| `pytest.ini` | `python_files` as an allow-list | 50 tests, including all 23 written that day. Green suite, code never executed |
| `scripts/gate` | ten paths by hand | the other ten that `extend-include` already declared |
| `csd-publish` | text suffixes to sanitise | `.html`, the moment the console's page moved into one. Three real hostnames reached the publication tree |
| `repo_context` | paths ending in a known extension | `scripts/autodev` — so a goal saying "read it" got a context without it, and was invalidated for asking the impossible |
| `csd-publish` forbidden list | the identifiers someone thought of | `host-gpu-b`, so a substitution that silently failed to match could not be caught |

**The rule.** Enumerate what you EXCLUDE, not what you include, and let the
default be "covered". `csd-publish` now tries every file as UTF-8 and sanitises
whatever decodes; `tests/conftest.py` strips the whole `CSD_*`/`AUTODEV_*` space
rather than a list of known-dangerous names. A new member is then covered on the
day it appears, not on the day someone remembers.

Where an allow-list is genuinely right — the forge repository allowlist is, because
widening it should be a reviewed act — make the list the code and not the config,
and make the failure loud.

## 2. A failure that reports success

Second most expensive, and the reason so much of this repository is machinery for
noticing rather than for doing.

- A CI job with a `runs-on` no runner matches is queued forever and reports
  `skipped`, which reads as success. CI was dead for a week.
- `github.server_url` comes from the RUNNER's registered address, not `ROOT_URL`,
  so a guard comparing it to a domain silently never matched.
- `wake()` swallowed its own `OSError`. `autodev submit` printed "the loop
  decomposes it on its next tick" and no tick came for twenty minutes, with every
  indicator healthy.
- "Nothing to commit" was read as "the work is already there", so a goal that
  produced a no-op closed as **done** with none of its work present — on a green
  gate, because prose has no tests.
- A goal declining was recorded as `ok: true`, so nothing retried and the goal
  stalled politely, forever.

**The rule.** If a check cannot distinguish "passed" from "did not run", it is
not a check. Ask for the evidence: `csd-ci-scrutiny` compares runs against the
workflow FILES, because comparing runs to runs can never reveal a workflow that
never dispatched. `land_git` asks the forge whether a PR from this branch ever
merged, rather than inferring it from an empty diff.

## 3. State that does not survive the process

Every tick is a fresh process. Anything counted within one run is always 1.

`attempt_count()` exists because this was written **three times independently** —
for the implementer, for apply refusals, and for gate refusals, where the field
being read was `approach_fails`, which is 0 on every tick. Each copy made a
bounded retry unbounded: the budget was never reached, so the loop retried
forever and never escalated. The same defect appeared a fourth time in the
escalation counter, which only the escalate branch set, so the retries between
two escalations dropped it.

**The rule.** A counter that decides anything belongs in the heartbeat, keyed by
what it counts and by which goal it counts for. One helper, not one per caller.

## 4. Being strict about something the other side cannot verify

The model cannot check its output against your parser. Strictness there does not
prevent the error; it relocates it.

- The reply parser required a fence to close at the FIRST closing fence, so any
  markdown document — every one of which contains a ```bash block — was cut at
  its first code block. The loop writes whole files, so the remainder was
  **deleted**: 239 of RUNBOOK.md's 250 lines, through a green gate.
- Given a multi-line passage and a one-line `from => to` grammar, the model put
  the arrow on its own line. Sensible, unparseable, retried nine times, goal
  invalidated.
- The planner was asked for three table cells and produced four — the fourth
  holding the acceptance criteria, which is part of the goal. A strict parser
  would have refused four good goals on the operator's first use.

**The rule.** Accept what is actually produced, and keep a second line of defence
that does not depend on parsing being right. `shrink_check` refuses any
whole-file write that leaves under half of an existing file, whatever the parser
thought it read.

---

## 5. Evidence about the wrong thing

A check that runs somewhere other than where the claim applies proves nothing,
however green it is.

`csd-release` ran its four checks against a WORKING TREE and would cut the
release from a BRANCH. Those are the same thing only when the tree is clean and
merged. On 2026-09-08 the agent had `.shellcheckrc` as an unpushed commit on a
feature branch: `adopted` passed because the file was on disk, and the rung
reported **READY** for a release that would have shipped without it.

`unreleased_work()` now runs first and blocks on uncommitted changes, a feature
branch, or commits the remote does not have — because the point of a rung is
that its evidence is checkable by someone other than the agent that produced it.

**The rule.** Before trusting a check, ask what it looked at.

## 6. A goal that states its own success condition, unchecked

Every goals file here is written with "Prove ..." clauses. Nothing read them.

G-README-CLI-1 said *"Prove the section no longer mentions `docs/GOALS.md`"*.
The substitution applied cleanly, the goal closed as **done**, and the README
still said `docs/GOALS.md` — the change had replaced a line inside a code fence
and left the wrong prose exactly where it was, so the file came out worse than
before. Every gate passed, because prose has no tests and the substitution
protocol only guarantees that the quoted text was found.

`goal_claims()` reads "prove" sentences with a backticked needle and turns them
into absent/present assertions; `check_claims()` holds the change to them and
puts the file back when it fails. Narrow on purpose — a claim it cannot parse is
simply not checked, which is where every goal already was.

**The rule.** If the goal says what success looks like, that sentence is a test.
Use it.

## Corollaries

**A retry that sends the same prompt cannot succeed.** It spends a call to prove
what the last one proved. Every retry now carries why the last attempt was
rejected — the same mechanism as a reviewer's objection or a peer's guidance,
applied to the case that happens most.

**Ask for one form, not a choice.** Offering the whole-file and substitution
protocols with a sentence about which to prefer did not work; the model chose the
wrong one and returned a 28-line summary of a 272-line document. The loop reads
the goal and the file and asks for exactly one.

**A guard is only real if you have seen it fail.** Three CSD guards were once
structurally incapable of firing. Everything here that matters has been verified
by breaking it: re-admitting three variables to `conftest.KEEP` turns 6 tests
red; reinstating the case-sensitive workflow match turns 2 red; reinstating
`return default` in one fallback turns 1 red.

**Set aside is not abandoned.** A goal that cannot be done now keeps its row and
goes to the back of the queue. A goal the helper proves wrong is rewritten once,
and only then marked `invalid` — with its ORIGINAL text intact, because the next
reader needs to know what was being asked.

**Parameterise before you need to.** The fleet's real addresses were hardcoded
across 26 files, and publishing meant rewriting them every time — a step that has
to be right always and was wrong twice on its first run. `config/fleet.example.json`
is committed and documentation-safe; the private override is gitignored; the
public tree never contains the real values at all. `fleet.path()` is how a script
reaches a directory, so moving the agent means editing one file rather than
finding twenty defaults that happened to agree.
