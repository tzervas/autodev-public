# Finding goals in a repository, and decomposing them into tasks

A **goal** is an outcome worth a reviewer's attention. A **task** is one PR.
Goals are composed of tasks; a goal with one task is usually mis-scoped, and a
task that cannot be stated in two sentences is really a goal.

This is the procedure, in the order the evidence becomes available. It is written
to be run by a person today and by autodev later — the steps are mechanical on
purpose.

---

## Step 1 — Survey. Gather evidence, propose nothing.

```bash
scripts/csd-repo-survey /path/to/repo
scripts/csd-repo-survey /path/to/repo --json > survey.json
```

It reports what is *there*: public functions no test names, constants defined and
never read, the dict keys the code subscripts, TODO markers, and every workflow's
`runs-on`.

It deliberately does not suggest goals. A survey that guesses at intent produces
goals as wrong as ones written from memory, only faster — and the failures here
all came from goals asserting things the source did not say.

**Read the survey output and the README before writing anything.**

## Step 2 — Group the evidence into goals

Evidence is not a goal. Eight untested functions are not eight goals; they are
one or two goals about correctness, decomposed into tasks.

Group by *outcome a reviewer would recognise*:

| evidence | the goal it belongs to |
|---|---|
| functions no test names | **the library's contract is proven** |
| a constant defined and never read | **the validator enforces what it declares** |
| `runs-on` no runner can match | **CI can actually run** |
| TODO/FIXME markers | read them; most are a note, some are a goal |
| undocumented public functions | **the public surface is documented** |
| subscripted keys | not a goal — this is *input* to every test task |

A repo usually yields three to six goals. More than that and they are tasks
wearing the wrong label.

## Step 3 — Decompose each goal into tasks

A task is one PR **and one file** — `apply_proposal` writes exactly one file per
tick, and a goal spanning two will land the half the model picked, pass CI, and
close anyway. See the "One goal, one file" section of `WRITING-GOALS.md`; it cost
G-SCHEMA-1's tests.

A task is well-scoped when:

- it names **one file to change** and **one function or behaviour** to target;
- it states what must be **proven**, not just "add tests";
- it carries what the **call chain requires** — the survey's `fixture_keys` are
  exactly this;
- it says whether it **changes behaviour**;
- a reviewer could tell in one read whether it succeeded.

Size it to what the implementer does well: three to six assertions, one file,
additive where possible. The measured working range is a change of roughly
30–100 lines. Larger than that and the reply risks truncation; smaller and the
overhead of a PR is not worth it.

**Order tasks so each stands alone.** If task 2 needs task 1 merged, say so in
its text — the loop takes one goal at a time and will otherwise pick a task whose
premise has not landed.

## Step 4 — Write the rows

Goals and tasks share one table; the goal is the prefix of the id.

```markdown
| id | goal | state |
|---|---|---|
| G-PROVE-1 | In `tests/test_x.py`, add tests for `license_ok` in `src/x.py`. Prove ... | open |
| G-PROVE-2 | In `tests/test_x.py`, add tests for `kv_gib` and `total_gib`. Prove ... | open |
| G-CI-1    | `.github/workflows/y.yml` names a label set no runner offers ... | open |
```

`G-PROVE-1` and `G-PROVE-2` are two tasks of one goal. The branch is named from
the id, so they land as separate PRs and the goal completes when its last task
does.

See `WRITING-GOALS.md` for the wording rules and worked examples.

## Step 5 — One watched tick per new goal

Run the first task of a new goal with `systemctl start autodev-tick.service` and
read the PR before enabling the timer. The first task is where a wrong premise
shows up, and watching it costs a minute.

---

## Worked example: rung 0

The survey reported: 8 untested functions in `abliterate_lib.py`, subscripted keys
`trust / official_orgs / abliterate_orgs / quant_orgs / refuse_orgs / hosts / id /
weights_gib`, and three workflows of which one demanded `gpu` + `host-homelab`.

Grouped into three goals:

**G-CI — CI can actually run.** One task: the `runs-on` in `abliterate.yml` names
a combination no runner offers, so the job is queued forever and reports as
skipped, which reads as success. *Landed as PR #4, `+1 -1`.*

**G-VAL / G-FIT / G-TRUST — the library's contract is proven.** Three tasks, one
per cluster of related functions, each naming its assertions and each carrying the
`trust` fixture requirement the survey surfaced. *G-VAL landed as PR #2, five
tests, green.*

**G-SCHEMA — the validator enforces what it declares.** One task, flagged as a
behaviour change: `CATALOG_SCHEMA` is declared and never read, so a catalog with
the wrong version passes silently.

Note what the survey prevented. The first attempt at G-VAL was written from
memory, asserted schema validation that does not exist, and cost a full cycle —
four tests, three failing, a closed PR. The survey's `fixture_keys` line would
have shown `trust` immediately, and its function list would have shown that
nothing validates `schema`.

---

## Graduating: autodev doing this itself

The steps above are mechanical enough to hand over, in this order — each stage
earns the next:

1. **Survey** — already a tool. No judgement, safe to run anywhere.
2. **Propose goals from a survey** — a `planner` call over `survey.json`, emitting
   candidate goals for a human to accept or reject. Cheap to check, and wrong
   proposals cost nothing because nothing acts on them.
3. **Decompose an accepted goal into tasks** — the same, one level down. Judge it
   by whether the tasks are independent and whether each names its file, its
   target and its assertions.
4. **Write the rows and execute** — only once 2 and 3 have been right often
   enough to be boring.

The gate throughout is the same one that already works: a task is only real if a
PR passing the repo's own tests can be produced from it. A proposed goal that
cannot be decomposed into such tasks was not a goal.

**Do not skip to 4.** The failures that cost the most here were all goals asserted
without evidence, and an agent writing its own goals from memory would produce
those faster than a person can read them.
