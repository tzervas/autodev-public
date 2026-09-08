# Autodev runs on events, not on a clock

Autodev used to tick every five minutes. It now runs when the forge says
something happened, and otherwise does not run at all.

This document is the design, the failure modes it is built against, and how to
operate it.

---

## Why the timer was wrong

The timer was wrong in both directions at once.

**Too slow.** Everything autodev reacts to is an event: a PR merges, CI reports,
a branch appears. After a merge it sat idle for up to five minutes before
noticing — and the merge is exactly the moment the next goal becomes available.

**Far too fast.** 288 ticks a day, of which roughly one had anything to do. Each
one woke the loop, synced a worktree, queried the forge and wrote a heartbeat, to
conclude that nothing had changed. That is the "agent that checks again" pattern
the fleet's standing rule exists to forbid.

A five-minute poll is not a compromise between those. It is both faults.

## The shape

```
Forgejo ──webhook──► csd-forge-events serve ──┐
 (.170)   push, PR   (svc-autodev, :9119)     │
                                              ├─► a sentinel ──► autodev-tick.path
csd-forge-events watch-ci ────────────────────┤   /var/lib/autodev/events/   │
 (one per in-flight commit; CI has no webhook)│                               ▼
                                              │            autodev-tick.service
the loop itself, on a refused push ───────────┘             (drains, runs one cycle)
```

Five pieces, each doing one thing:

| piece | job | deliberately does NOT |
|---|---|---|
| the webhook | say that something happened | say what to do about it |
| `csd-forge-events serve` | verify, record, write one sentinel | run the loop |
| `csd-forge-events watch-ci` | turn a pollable CI API into an event | decide the verdict |
| `autodev-tick.path` | start a tick when a sentinel appears | decide anything |
| `csd-autodev-loop` | drain, then ask the forge what is true | trust the event body |

### This forge emits no CI event, so we build one

Probed one name at a time against Forgejo 16.0.2, because **it silently drops
event names it does not know** rather than erroring — a hook subscribed to
`status` looks identical to a working one and fires never:

| asked for | result |
|---|---|
| `push`, `pull_request`, `pull_request_sync` | accepted (`pull_request` expands to ten variants) |
| `create`, `delete`, `release`, `repository`, `issues` | accepted |
| `status`, `statuses`, `commit_status` | **dropped** |
| `workflow_run`, `workflow_job`, `check_run`, `check_suite` | **dropped** |

So `watch-ci` is the adapter. It polls — deliberately. The rule is *nothing
should sit in a loop asking whether anything has changed*; this is one bounded
process, tied to one commit, that exists only while that commit's CI is
undecided, holds an `O_EXCL` lock on the sha, and whose output is an event. With
no PR in flight, nothing is running.

`land_pr` **arms** it and returns rather than blocking. A tick that waits half an
hour cannot be stopped, cannot be told about a merge that happened meanwhile, and
holds its worktree throughout. `autodev-tick.service` sets `KillMode=process` so
the watcher outlives the tick that armed it.

### Four agents in sequence, and only one of them can merge

| stage | agent | model | what it decides |
|---|---|---|---|
| implement | `coder-deep` | qwen3.5-9b @ host-a | one file's contents |
| local gate | pytest | — | does the repo's own suite still pass |
| CI | the forge's runners | — | does it pass on a clean checkout |
| **review** | `reviewer` | qwen3.5-4b @ host-gpu-b | does it satisfy the goal, and is it correct |
| merge | `merge-gate` | — | only after CLEAN **and** APPROVE |

The reviewer runs **after CI settles** — so it sees the verdict it is reviewing
under — and **before merge-gate**, so its refusal actually stops the merge. It
reads the **diff that landed**, not the implementer's reply, because the reply is
only what the model claims it wrote.

`csd-autodev-review` refuses to run at all if the reviewer and implementer
resolve to the same model, asked of the gateway every time. And it **fails
closed**: a crash, an unreadable reply, a missing script, or a diff too large to
read in full all leave the PR unmerged.

**It is not an oracle, and the loop does not treat it as one.** Measured on two
real PRs it caught the half-goal cold and also produced a false INCOMPLETE,
claiming a test was missing that was present in the diff. So a REQUEST_CHANGES
carries the objection **back to the implementer**, which is told the reviewer may
be mistaken and to answer the point rather than silently repeat itself. A wrong
review costs one cycle; without that path it would block a goal forever.

### A refused push is also an event

Every other transition emitted; a tick refused by the test gate emitted nothing,
so a goal the model got wrong on its first attempt sat until the hourly
reconcile. `emit_retry` writes a `retry` sentinel, bounded by the same
approach-retry budget the loop uses to change tactic — a self-emitted event is a
self-retrigger, and `StartLimitBurst=12` is the backstop that does not depend on
that bound being right.

### Only the forge may reach the port

Forgejo signs the request **body** only, over plaintext HTTP, with no timestamp
or nonce. One captured delivery is therefore a permanent trigger primitive, and
each replay starts an agent that commits, pushes and merges. Headers are outside
the MAC, so a replayer also chooses `X-Forgejo-Event`.

No cryptographic freshness is available on this side, so the control is network
identity enforced by the kernel: `IPAddressAllow=203.0.113.20/32` with
`IPAddressDeny=any`. It is a cgroup BPF filter the attacked process cannot
bypass, it fails closed, and it blocks egress so a defect in the parser cannot
phone home. Verified: host-a itself now gets no answer on 9119; the forge
gets 200.

### The receiver never runs the loop

It writes a file. systemd decides what that means.

This is the single most important line in the design. A receiver that executes
what it receives is remote code execution with extra steps; separating "an event
arrived" from "an agent runs" means a compromise of the receiver yields the
ability to *start a tick*, not the ability to *choose what the tick does*.

### The event says WHEN. The forge says WHAT.

The sentinel carries `merged`, `number`, `state` and so on, and the loop uses
none of them to decide anything. It re-queries the forge.

Webhook deliveries arrive twice, out of order, and sometimes not at all. Making
the loop's state a function of a message with those properties would mean a
duplicated delivery could close a goal whose work never landed. The event is a
doorbell, not a report.

### Draining is what keeps it alive

A systemd path unit re-arms on the **empty-to-nonempty edge**. If the directory
still has files, the next event does not start anything.

So the drain is not housekeeping — it is the mechanism. It runs at the very top
of `tick()`, in a wrapper around the cycle, before the pause check and before
anything else that can return early. `test_drain_consumes_so_the_next_event_can_fire`
exists because the failure mode is "autodev stopped for no reason", which is the
hardest kind to diagnose.

### The hourly timer is a net, not a schedule

`autodev-tick.timer` still exists, at **one hour**.

Webhook delivery is best-effort: the forge retries a few times and gives up. If
the receiver was down for that window, the loop would wait forever for a merge
that already happened. The timer catches exactly that.

At five minutes it would be a poller with an excuse. At one hour, the common path
is always the event, and a tick from the timer that finds work means a delivery
was genuinely lost — which is a signal worth having.

---

## Discovery: the loop finds its own state

Being event-driven is only half of unattended. The other half is not depending on
a local file to know what it was doing.

### In-flight PRs come from the forge

`heartbeat.json` used to be the only record of the open PR. A crash, a state
reset or a manual edit lost it — and a loop that believes nothing is in flight
picks the next goal and opens a **second PR beside the first**.

`discover_inflight()` asks the forge for open PRs whose head is `autodev/*`:

- **they agree** → keep the heartbeat; it is richer (goal text, run id, which
  approaches already failed)
- **the forge has one the heartbeat does not** → adopt it, recovering the goal
  from the branch name
- **the heartbeat names one the forge no longer lists** → report it stale and let
  the sync path decide whether it merged or was abandoned; only that path can
  tell those apart
- **the forge is unreachable** → keep the heartbeat. Reading a network failure as
  "nothing is open" is the dangerous direction: it opens a duplicate PR every
  time the network hiccups. A stale heartbeat is wrong by one tick; the
  alternative is wrong by a PR.

Only `autodev/*` branches are adopted. A human's open PR is not autodev's to take
over.

### Branch discovery

`discover_branches()` lists `autodev/*` on the forge and subtracts every branch
any PR accounts for. What is left is the residue of an interrupted tick: work
pushed just before the process died.

These are **reported, not acted on**. Opening a PR for an abandoned branch would
be the loop guessing that half-finished work was ready.

### Docs discovery

The loop knew the fleet's rules and nothing about the repository it was editing.
`discover_docs()` finds `AGENTS.md`, `CONTRIBUTING.md`, `CONVENTIONS.md`,
`CLAUDE.md` and the README, and quotes them to the implementer ahead of anything
about the goal. A rule the repo states beats a habit the model brought with it.

Discovered, not configured: onboarding a repo does not mean editing the loop.

---

## Operating it

```bash
# Is the receiver up, and what has it seen?
systemctl status autodev-events.service
sudo -u svc-autodev tail -5 /var/lib/autodev/events/received.jsonl

# Is the path unit armed?
systemctl status autodev-tick.path

# What is waiting, without consuming it (consuming would disarm the path unit)
sudo -u svc-autodev /var/lib/autodev/autodev/scripts/csd-forge-events drain --keep

# Register or re-register the webhook. Idempotent: it updates its own hook.
secret exec TOKEN=forgejo/token FORGE_WEBHOOK_SECRET=forgejo/webhook-secret -- \
  scripts/csd-forge-events install \
    --targets cabal-collective/abliterate-harness \
    --url http://203.0.113.10:9119/

# Force a cycle by hand. Always safe; an empty drain is not an error.
sudo systemctl start autodev-tick.service
```

### Failure signatures

**Nothing happens after a merge.** In order: is `autodev-events.service` running;
does `received.jsonl` show the delivery; did a sentinel appear; is
`autodev-tick.path` active. The forge's own webhook page shows recent deliveries
and their response codes, which distinguishes "never sent" from "sent and
refused".

**`signature mismatch` in `received.jsonl`.** The hook's secret and the vault
entry have diverged. Re-run `install`, which rewrites the secret on the hook.

**Sentinels accumulate and the loop never runs again.** The drain is failing, so
the path unit never re-arms. Check `drain_events` in the last tick's JSON: `ok:
false` there names the cause. Clearing the directory by hand restores service and
loses nothing — the forge is still the source of truth for everything the
sentinels contained.

**Everything runs only on the hour.** Every event is being lost; the loop is
surviving on the reconcile net alone. Almost always the receiver is down or the
forge cannot reach port 9119.

---

## What is deliberately not here

- **No event queue or broker.** The directory is the queue. Adding a broker adds
  a service that can fail in ways the filesystem cannot.
- **No retry of a rejected delivery.** The forge already retries, and the hourly
  net covers what it gives up on.
- **No acting on the event body.** See above; this is the property that makes
  duplicated and out-of-order deliveries harmless. It is also why validating the
  body would be a mistake: it would suggest the body matters.
- **No delivery-ID dedup.** `X-Forgejo-Delivery` is outside the MAC, so a
  replayer increments it. It looks like a replay defence and is not one.
- **No auto-PR for orphan branches.** Reported, for a person to look at.
