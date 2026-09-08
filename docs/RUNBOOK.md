# Autodev runbook

Operating autodev: starting it, watching it, and what to do when a tick fails.

Every failure signature below is one that actually happened. If you are reading
this because something broke, jump to **Failure signatures**.

---

## Everyday commands

```bash
# One tick, watched. Always safe — a tick with no events is not an error.
sudo systemctl start autodev-tick.service
sudo journalctl -u autodev-tick.service -n 1 --no-pager | tail -1

# Unattended. Autodev runs on FORGE EVENTS, so this is three units, not one:
sudo systemctl enable --now autodev-events.service   # receives the webhooks
sudo systemctl enable --now autodev-tick.path        # a tick per event
sudo systemctl enable --now autodev-tick.timer       # hourly net for a lost delivery

# STOP. All three, or it keeps running — and each tick is a separate process,
# so this stops the next one rather than interrupting the current one.
sudo systemctl stop autodev-tick.path autodev-tick.timer autodev-events.service

# Follow it live.
sudo journalctl -u autodev-tick.service -f

# What is it working on, and what happened last?
sudo -u svc-autodev cat /var/lib/autodev/state/heartbeat.json | python3 -m json.tool

# Why did the last tick run? (deliveries the receiver has seen)
sudo -u svc-autodev tail -5 /var/lib/autodev/events/received.jsonl
```

See `EVENT-DRIVEN.md` for the design and its failure signatures.

Note `sudo -u svc-autodev` rather than `su`: the account's shell is `nologin`, on
purpose. `runuser -u svc-autodev -- bash -lc '...'` also works, because it invokes
bash explicitly.

## Reading a tick

Every tick prints one JSON object. The fields that matter, in the order they are
decided:

```json
{
  "ok": true,
  "goal":    "| G-VAL-1 | ... | open |",
  "applied": {"ok": true, "wrote": ".../tests/test_abliterate.py"},
  "git":     {"ok": true, "branch": "autodev/g-val", "sha": "9f03bc5..."},
  "pr":      {"number": 3, "merged": false}
}
```

- **`applied.ok: false`** — the model's reply could not be used. Look at `error`.
- **`applied.skipped`** — the model declined, and the text is its reason. This is
  usually a *good* signal: it declines when a goal is underspecified.
- **`git.ok: false, error: "repo tests fail; refusing to push"`** — the gate did
  its job. `gates.output` has the pytest tail.
- **`pr.number`** with `"merged": false` — a PR is open and waiting.
- **`events.why`** — why this tick exists: `pull_request`, `status`, `push`,
  or `manual` for a hand-started tick or the hourly net. A run of `manual`
  ticks doing real work means events are being lost.
- **`discovery.adopted`** — the loop found an open PR the heartbeat did not
  know about, and took it over instead of starting a second one.

## Steering it

```bash
# Pause without stopping the service.
sudo -u svc-autodev tee /var/lib/autodev/state/steer.json <<'JSON'
{"pause": true, "note": "holding while I review PR #4"}
JSON

# Resume.
sudo -u svc-autodev tee /var/lib/autodev/state/steer.json <<'JSON'
{"pause": false}
JSON
```

The goals file is the other steering surface — see `WRITING-GOALS.md`. Reordering
rows changes what it picks up next; marking a row anything other than `open`
takes it out of the queue.

---

## Failure signatures

### `"error": "... ALREADY failing before this change"` / `needs_onboarding`

**Not the implementer's fault, and not a retry.** The repo's suite fails on the
tree WITHOUT the change, so the gate re-ran it as a baseline and said so.

Usually the gate is not running the repo's own command. `gate_command()` picks
`uv run pytest` when there is a `uv.lock`, and puts `src/` on `PYTHONPATH` for a
src layout — nl-router failed every gate with `ModuleNotFoundError: No module
named 'nl_router'` while its own CI was green, because CI runs `uv run pytest`
and installs the package first.

Fix the repo's onboarding, not the goal. Compare against what the repo's CI
actually runs:

```bash
grep -A3 'name: Test' <repo>/.github/workflows/ci.yml
```

### `"error": "repo tests fail; refusing to push"`

**Working as intended.** The model wrote something that breaks the repo's own
suite. Read `gates.output`. If it names something the change did not touch, look
for `needs_onboarding` above first.

Then ask which of these it is:

- *the tests are wrong* — the goal described behaviour that does not exist. Fix
  the goal, not the code. This is the most common cause.
- *the code is wrong* — let it try again; the loop changes approach after
  repeated failures on the same tactic.

### `"error": "refusing sync on main"` — fixed 2026-09-08

Was: the worktree returned to the default branch every tick, so the in-flight
handler refused while a PR was open, and every tick failed until the PR cleared.

Now the sync triggers on a **merge**, not on a tick. While a PR is open the
worktree stays on its branch; when the PR merges (or its branch vanishes, which
delete-on-merge causes) it returns to the default branch, fast-forwards, prunes,
and **marks that goal `done`** so the next tick moves on instead of rebuilding
work that already landed.

If you see this again, the sync is being called without the in-flight record —
check that `TARGET_REPO` is set, since the PR lookup needs it.

### `"applied": {"ok": false, "error": "could not parse a file proposal..."}`

The reply was not in a form the loop could read. It accepts, in order: `PATH:`
plus a fenced block (preferred), a JSON object, a JSON array (first element), and
a bare `NOTE:` line as a refusal. A truncated reply is the usual cause — check
whether `AUTODEV_MAX_TOKENS` is large enough for the file being written.

### `"applied": {"ok": false, "error": "path escapes the worktree: ..."}`

The model proposed a path with `..` or a leading `/`. Refused everywhere; no
action needed beyond noticing it happened.

### Empty content, `finish_reason: length`

The model spent its whole budget reasoning and wrote nothing. `reasoning_effort`
is set per role at the gateway (`none` for implementation). If you see this,
something is overriding that — check the rendered gateway config.

### Everything looks healthy and nothing runs

Check the PATH unit, not just the service:

```bash
systemctl status autodev-tick.path      # look for: Result: unit-start-limit-hit
```

When `autodev-tick.service` trips `StartLimitBurst`, **the path unit fails too**
and stays failed after the service recovers. `systemctl reset-failed` on the
service alone does not bring it back, and every other indicator still looks
right: the receiver is active, the timer is armed, sentinels accumulate, and
nothing consumes them.

```bash
autodev start          # resets both names, then starts. That is all it takes.
```

Fixed at the source on 2026-09-08: the path unit inherited systemd's default
start limit of 5-in-10s, and a path unit has no business having one — it sits
and watches, it does not restart in a loop. All the limit did was fail the unit
when an operator started it a few times in quick succession, which is what
happens while working on the loop. `StartLimitIntervalSec=0` on the path unit;
the rate limit that matters is still on the service, where it belongs.

Both unit names, every time. This happened on 2026-09-08 when a self-retry with
no backoff spent an hour's burst in two minutes — the burst is now sized from
observed operation (60/hour), but the recovery is the same whenever it fires.

### Nothing happens after a PR merges

The loop is event-driven; the merge should have woken it within seconds. In
order: is `autodev-events.service` running, does `received.jsonl` show the
delivery, did a sentinel appear in `/var/lib/autodev/events/`, is
`autodev-tick.path` active. The forge's own webhook page lists recent deliveries
and their response codes, which tells "never sent" apart from "sent and refused".

Sentinels piling up undrained is its own failure: the path unit re-arms only on
the empty-to-nonempty edge, so an undrained directory means no further event ever
starts a tick. Clearing it by hand is safe and loses nothing — the forge remains
the source of truth for everything the sentinels held.

### `wait: {"rc": 2}` / a PR that never gates

CI has not reported. Run the scrutiny by hand:

```bash
secret exec TOKEN=git/tzervas-forgejo -- \
  /var/lib/autodev/autodev/scripts/csd-ci-scrutiny \
  cabal-collective/abliterate-harness <sha>
```

`SUSPECT` with *"declared workflow produced NO run at all"* usually means a
`runs-on` label set that no runner offers. See `RUNNERS.md`.

---

## Recovering a stuck worktree

```bash
sudo -u svc-autodev bash -c '
  cd /var/lib/autodev/work/abliterate-harness
  git checkout main && git fetch --prune origin && git reset --hard origin/main
  git clean -fd
'
sudo -u svc-autodev rm -f /var/lib/autodev/state/heartbeat.json
```

This is safe: the worktree holds nothing that is not either pushed or disposable.

## Where things live

| | |
|---|---|
| harness (read-only to the agent) | `/opt/autodev/` |
| the repo it works on | `/var/lib/autodev/work/<repo>/` |
| goals, steer, heartbeat, output | `/var/lib/autodev/state/` |
| its vault | `/var/lib/autodev/.secrets/` |
| the wrapper carrying all env | `/usr/local/bin/autodev-tick` |
| forge event sentinels | `/var/lib/autodev/events/` |
| units | `/etc/systemd/system/autodev-{tick.service,tick.timer,tick.path,events.service}` |

## Checking containment

If you want to re-verify the agent is confined — worth doing after any change to
its account or units:

```bash
# no escalation
sudo -u svc-autodev sudo -n true              # expect: a password is required

# cannot read the operator's vault, or the other agent's
sudo -u svc-autodev env SECRET_VAULT=/home/operator/.secrets \
     /usr/local/bin/secret get fleet/sudoer-pass   # expect: not accessible

# cannot rewrite its own harness
sudo -u svc-autodev touch /var/lib/autodev/autodev/scripts/PROBE  # expect: denied

# cannot reach the operator's repos
sudo -u svc-autodev env SECRET_VAULT=/var/lib/autodev/.secrets bash -c '
  /usr/local/bin/secret exec TOKEN=forgejo/token -- git ls-remote \
    https://git.example.com/tzervas/CogSynDelta.git HEAD'   # expect: Forbidden
```
