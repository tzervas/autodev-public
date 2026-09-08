# autodev

An autonomous software development pipeline that runs on self-hosted models,
against a self-hosted forge, with no external service in the loop.

It picks up a goal, writes the code, runs the repository's own tests, opens a
pull request, waits for CI, gets an **independent review from a different
model**, and merges only when every gate passes. Then it takes the next goal.
When the goals run out it audits the repository against a house standard and
writes new ones. When those run out it reads the project's stated intent and asks
what is still missing.

It is event-driven: a merge, a push, or a CI result wakes it. It is not on a
timer.

## Why

I wanted to know what an autonomous development loop actually breaks on, using
hardware I own and models I run, where the failure modes are mine to see rather
than a vendor's to hide.

The answer turned out to be more interesting than the loop. Most of this
repository is not "make the model write code" — that part works. It is the
machinery for **noticing when something is quietly wrong**:

- a CI job that reports `skipped`, which reads as success
- a test suite that silently excluded 50 tests because the config was an
  allow-list
- a lint gate that scanned zero files because every script is extensionless
- a goal that closed as done with half its work missing, on green CI
- a reviewer that approved nothing and would have deadlocked the queue
- a "sync" that never synced, because its failure was excused from its own check

## The CLI

`scripts/autodev` is the operator's side of the loop. It takes a subcommand:

```bash
autodev submit brief.md      # hand it work: a brief, or a file of goal rows
autodev status               # units, target repo, queue, in-flight PR
autodev watch -f             # one line per tick, live
autodev start                # unattended, on forge events
```

`submit` is how work gets in. A brief in prose is decomposed into goals by the
loop itself on its next tick — the operator holds no gateway credential, so
submitting needs nothing but write access to one directory. A file that already
contains `| G-... | ... | open |` rows is taken verbatim, because a plan you
decomposed yourself is not improved by a model rewriting it.

The goal queue lives in the agent's state directory, not in this repository.

`start` arms the forge-event triggers rather than spawning a worker: the loop
runs when the forge emits something, not on a timer.

[`docs/CLI.md`](docs/CLI.md) documents every subcommand.

**There are two CLIs, and they are not the same thing.** `scripts/autodev` is
the operator interface described above. `src/csd_client/cli.py` is a thin HTTP
client for the lab console — a different program, for a different service, with
its own commands. It is not missing `submit`, `status` or `watch`; those belong
to `scripts/autodev` and always have. Goals proposing to add them to the client
have been raised and rejected repeatedly, so this is written down rather than
rediscovered.

## Architecture


## Safety

The agent runs as its own service account. It cannot write the harness it
executes, has no sudo, and reads only its own secret vault; it works in a clone,
opens a pull request, and merges only when every gate has passed on evidence
rather than on inference. `docs/ARCHITECTURE.md` describes the design and
`docs/LESSONS.md` describes what went wrong on the way to it.

## Where things are

| | |
|---|---|
| the goal queue | the agent's state directory, one file per repository — not in this tree |
| briefs you submit | the same state directory, under `briefs/` |
| what it is working on | `autodev status`, or the heartbeat beside the queue |
| why a tick happened | one line per tick from `autodev watch` |

Goals are rows: `| G-SOMETHING-1 | what to do | open |`. A row saying "Prove
the file no longer mentions `X`" is checked after the change is applied, so a
goal that states its success condition is held to it.
