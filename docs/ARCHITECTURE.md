# Autodev Architecture

Autodev is an autonomous software development pipeline that runs on self-hosted models,
against a self-hosted forge, with no external service in the loop. It picks up a goal, writes the code, runs the repository's own tests, opens a
pull request, waits for CI, gets an **independent review from a different
model**, and merges only when every gate passes. Then it takes the next goal. When the goals run out it audits the repository against a house standard and
writes new ones. When those run out it reads the project's stated intent and asks what is still missing.

It is event-driven: a merge, a push, or a CI result wakes it. It is not on a timer.

## Why

I wanted to know what an autonomous development loop actually breaks on, using
hardware I own and models I run, where the failure modes are mine to see rather than a vendor's to hide.

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

## The Three Units

The system is built from three cooperating units:

1. **Webhook Receiver** (`autodev-events.service`) — Listens on a LAN address for events from the forge (push, merge, CI result). It validates the source and writes a sentinel file to the event directory.
2. **Path Unit** (`autodev-tick.path`) — A systemd PATH unit that starts a tick when the sentinel file appears. It does NOT map events to goals or check whether a repo is onboarded.
3. **Tick** (`autodev-tick.service`) — The main loop. It picks the next goal, clones the repository into a persistent worktree, runs the goal, and then either merges the result or discards it.

## Event-Driven vs Polling

Autodev is event-driven rather than polled. Polling would require a timer to wake the loop, which introduces latency and unnecessary CPU usage. Events are immediate: a merge happens, the webhook fires, and autodev starts. This reduces the window for human error and makes the system more responsive.

## Agent Identity and Model

The agent's identity is separate from the model that answers for it. The agent runs as a service account (`svc-autodev`) with its own keys in its own secret store. The model is invoked via a gateway that acts as an OpenAI-compatible endpoint. Credentials come from the agent's own vault by `secret exec`. The gateway does NOT mint tokens.

## Worktree-Per-Repo Layout

Each repository is cloned into a persistent worktree under `/var/lib/autodev/work`. This ensures that the agent's changes are isolated and that the agent can run tests and lint checks without affecting other repositories. The worktree is not cloned per goal; it persists across cycles.

## Goal Queue and Branching

Goals are stored in a queue. When a goal is picked, it is converted into a branch. The agent writes the code, runs the tests, and opens a pull request. The pull request is merged only when every gate passes.

## Quality Gates

Quality gates are never weakened to make a finding disappear. A gate that fails does not mean "retry until it passes". Instead, the agent follows the escalation path:

1. **Retry** — The same agent attempts the task again, assuming transient failures.
2. **Peer Escalation** — If retries are exhausted, the task is handed to a different agent (peer) with a different model or GPU to provide a second opinion.
3. **Set Aside** — If peer escalation fails, the goal is set aside with the blocker recorded. The row remains open, and the queue moves to other work.

## Containment Properties

The agent runs as its own service account, cannot write its own harness, has no sudo, and reads only its own secret vault. The agent's worktree is isolated from the host filesystem. Git hooks are not disabled; they are allowed to run as part of the repository's standard workflow.

## How a goal becomes a diff

The loop accepts two reply protocols to turn a goal into a diff:

1. **Whole-file proposal** — A single `PATH:` block containing the complete new contents of one file. The loop replaces that file entirely with the provided text.
2. **Substitution proposal** — A `SUBSTITUTIONS:` block containing literal from/to pairs. The loop applies these replacements in-place across the repository, preserving all other content.

There are two protocols because some changes are structural (replacing an entire file) while others are local edits (swapping a few tokens). The loop chooses the protocol based on the goal's intent and the shape of the proposed change.

## Implementation Details

The loop's behavior is governed by several guards in `scripts/csd-autodev-loop`:

- `is_substitution_goal` — Determines whether a goal should be handled via substitutions or a whole-file proposal. It checks the goal text for keywords like "SUBSTITUTIONS:" or "PATH:".
- `parse_substitutions` — Extracts the literal from/to pairs from a substitution goal. It validates that the pairs are well-formed and that the "from" strings exist in the target files.
- `apply_substitutions` — Performs the actual replacements in the repository. It ensures that replacements do not overlap and that the file structure remains valid.
- `fenced_block` — Ensures that code blocks in the goal are properly fenced and do not interfere with the substitution logic. It prevents accidental parsing of code as text.
- `shrink_check` — Refuses a whole-file write that DELETES most of an existing file; it has nothing to do with bloat or minimal diffs.

## GPU Roles and Separation

The system uses a pool of GPUs rather than fixed seats for specific roles. This separation is critical for the escalation mechanism to function efficiently.

- **Primary Card (3090 Ti)**: Hosts the primary task models and swaps between them as needed. This card is used for the implementer role, which performs the heavy lifting of code generation and reasoning.
- **Secondary Card (5080)**: Provides complementary capacity and serves as the natural home for escalation and second opinions. It is deliberately kept separate from the implementer's model to ensure that a second opinion comes from different weights, avoiding the bottleneck of queuing behind the work it is meant to unblock.
- **Edge Card (1080 Ti)**: Dedicated to RAG (Retrieval-Augmented Generation) tasks, specifically retrieval and indexing. This keeps the reasoning cards free for the core development loop.

If the implementer and the helper shared a backend, asking for help would either queue behind the work it is meant to unblock or force a model swap on the card in use — so the cheapest moment to get unstuck becomes the most expensive one. This separation ensures that the escalation path remains efficient and that the system can handle complex tasks without deadlocking.
