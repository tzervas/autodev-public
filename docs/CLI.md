# autodev — CLI reference

This document describes the subcommands of `autodev`, the operator interface to the
autonomous development loop.

## Install

`autodev` is installed at `/usr/local/bin/autodev`. It requires no environment
variables because it reads `config/fleet.json` from the deployed harness.
`autodev watch` requires membership of the `systemd-journal` group.

## Commands

The loop runs unattended, triggered by forge events rather than a timer.
`autodev start` arms the triggers; `autodev stop` disables them.
`autodev run` executes a single tick in the foreground.

| Command | Description |
|---|---|
| `submit <file>` | Turns a brief (or a file of goal rows) into queued goals. |
| `queue [--repo <repo>] [--all]` | Lists queued goals, optionally for a specific repo or including closed/invalid rows. |
| `status` | Shows the current target repo, systemd unit states, queue depth, in-flight PR, and pause state. |
| `watch [-f] [-n <lines>]` | Streams journal output from `autodev-tick.service`, one line per tick. |
| `run [--once] [-v] [--timeout <s>]` | Runs one tick in the foreground, optionally verbose. |
| `start` | Arms the triggers (`autodev-tick.service`, `autodev-tick.path`, `autodev-tick.timer`). |
| `stop` | Stops the triggers. |
| `pause [note]` | Pauses the loop without stopping the triggers. |
| `resume` | Releases a pause. |
| `target <repo>` | Sets the target repository for the loop. |

## Notes

- `submit` accepts either a brief (prose) or a file containing `| G-... | ... | open |` rows.
- The loop is event-driven: it waits for forge events (push, merge, CI result) rather than polling.
- `start` resets failed units first to avoid `StartLimitBurst` issues.
- `stop` only stops the triggers; a tick already running will finish.
