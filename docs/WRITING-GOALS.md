# Writing goals autodev can act on

A goal is a specification handed to an implementer that cannot ask you a
follow-up question. Every rule below was learned by watching a real goal fail.

---

## The format

`/home/operator/state/GOALS.md`. The picker matches a **markdown table row**
containing `| G-` and `| open |`. Anything else is silently ignored — a bullet
list looks fine and is never read.

```markdown
| id | goal | state |
|---|---|---|
| G-VAL-1 | In `tests/test_x.py`, add tests for `parse()` in `src/x.py`. ... | open |
```

Rows are tried top to bottom. The branch is named from the id
(`G-VAL-1` → `autodev/g-val`), so re-running a goal reuses its branch instead of
accumulating near-duplicates.

---

## The three rules

### 1. Describe behaviour that exists

> **What went wrong.** `G-VAL-1` said `validate_catalog` reports a missing or
> mismatched `schema` key. It does not — it validates candidates and hosts, and
> `CATALOG_SCHEMA` is defined but never read. The implementer faithfully wrote
> tests for the specification it was given, and three of them failed.

Read the function before you write the goal. If you find behaviour that *should*
exist but does not, that is its own goal (`G-SCHEMA-1` below), not a test.

### 2. Be specific enough to act on

> **What went wrong.** "Raise unit-test coverage; add tests for the untested
> branches." The model declined and explained that it could not know which
> branches were untested without a coverage report. It was right.

Name the function and state what the test must prove.

### 3. Carry what the call chain requires

> **What went wrong.** `validate_catalog` calls `validate_candidate`, which calls
> `trust_reason`, which reads `catalog["trust"]`. Nothing said so, so every
> generated fixture raised `KeyError` — three ticks in a row.

If acting on the goal requires knowing something two calls away, say it. The
implementer sees the file and its tests; it does not reliably trace a chain.

---

## Examples

### Bad — behaviour that does not exist

```markdown
| G-VAL-1 | Add tests proving `validate_catalog` reports a missing `schema` key. | open |
```

Cost a full cycle: 4 tests written, 3 failed, PR closed.

### Bad — not actionable

```markdown
| G-COV-1 | Raise unit-test coverage of `scripts/lib/abliterate_lib.py`; add tests for the untested branches. | open |
```

Declined, correctly, twice.

### Good — a test goal

```markdown
| G-VAL-1 | In `tests/test_abliterate.py`, add tests for `validate_catalog` in
`scripts/lib/abliterate_lib.py`. It reports: a candidate with no `id` as
"candidate missing id"; a repeated id as "duplicate id <id>"; and a host lacking
`gate_gib` or `vram_mib`. Prove each, and that a well-formed catalog returns an
empty list. It does NOT validate the `schema` key — do not test for that.
IMPORTANT: `validate_catalog` calls `validate_candidate`, which calls
`trust_reason`, which reads `catalog["trust"]` — so every test catalog containing
a candidate MUST include a `trust` block with `official_orgs`, `abliterate_orgs`,
`quant_orgs` and `refuse_orgs` lists, or the call raises KeyError. Copy the shape
from `config/abliterate/catalog.json`. Keep every existing test. | open |
```

Produced PR #2: five tests, 16 passing, merged.

### Good — a defect goal

```markdown
| G-CI-1 | `.github/workflows/abliterate.yml` sets `runs-on: self-hosted, linux,
x64, podman, gpu, 5080, host-homelab`. No runner offers that combination — the
GPU runner is `host-host-gpu-b`, not `host-homelab` — so the job can never be
scheduled and reports as skipped forever. Change `host-homelab` to
`host-host-gpu-b` in that file only. Change nothing else. | open |
```

Produced PR #4: `+1 -1`, exactly the intended line.

### Good — a behaviour change, flagged as one

```markdown
| G-SCHEMA-1 | `CATALOG_SCHEMA` is defined in `scripts/lib/abliterate_lib.py` but
never used, so a catalog with the wrong schema version passes `validate_catalog`
silently. Make `validate_catalog` report a missing or mismatched `schema`, and add
tests for both. This CHANGES behaviour, so update any existing test that a
stricter validator would break. | open |
```

Saying "this changes behaviour" matters: without it the implementer protects the
existing tests and cannot satisfy the goal.

---

## A checklist before you add a row

- [ ] I opened the function and the tests that exercise it.
- [ ] The goal names the file to change and the function to target.
- [ ] It states what must be *proven*, not just "add tests".
- [ ] Anything the call chain requires is spelled out.
- [ ] If it changes behaviour, it says so.
- [ ] It is one PR's worth of work.
- [ ] It says "Keep every existing test" if that is what I mean.

## What good looks like from the other side

The implementer is doing well when it:

- **declines** an underspecified goal with a reason (`NOTE: ...`);
- writes **additive** diffs (`+98 -2`) rather than rewriting a file;
- uses the repo's own idioms, because it was shown the file.

It is being set up to fail when the goal describes something imaginary. It will
implement that faithfully, and the test gate will catch it — which costs a cycle
that reading the source first would have saved.

## One goal, one file

`apply_proposal` writes **exactly one file per tick**. A goal that requires
changing two cannot be satisfied, and — this is the part that bites — **it will
still be marked done**, because the half that landed passes CI.

G-SCHEMA-1 said "make `validate_catalog` report a missing or mismatched `schema`,
and add tests for both". The implementer changed the library, CI went green, the
PR merged and the goal closed. The behaviour change is correct and live. **The
tests do not exist.** Nothing failed, and nothing said a word.

So split at the file boundary, and say which file:

| instead of | write |
|---|---|
| "change `x.py` and add tests" | one goal naming `x.py`, a second naming `tests/test_x.py` |
| "rename it everywhere" | one goal per file, ordered, each saying what the previous one landed |

State it explicitly in the row — `Change ONLY the test file` — so the
implementer is not choosing between two things it cannot both do.

**The second goal must restate what the first one landed.** It runs in a fresh
tick with no memory of the first, and its only context is the repo plus its own
row.
