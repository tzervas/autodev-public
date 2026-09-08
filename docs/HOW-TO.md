# Autodev CLI and lab TUI

Operator how-to for the **self-hosted** implementer. Hosted Grok is
planner / unblock only. Identity is **`autodev`**. Token lives in the
CSD vault (`git/autodev`), never `~/.secrets`.

Canonical tree: this repo (`tzervas/csd-autodev`). Product slices still
land on `tzervas/CogSynDelta` and `tzervas/memory-gate`.

Design spec (identity, gateway, sandbox, curated RAG; rows AD0..AD6): `docs/AUTODEV-IDENTITY-AND-SANDBOX.md`.

Until unit cutover, systemd still runs the **CogSynDelta copies** of
these scripts. Commands below work from either tree. Prefer `bash`
(`nu` `cp`/`git` wrappers have bitten this host).

```bash
export SECRET_VAULT=/var/lib/autodev/cabal/csd-vault
export SOPS_AGE_KEY_FILE=$SECRET_VAULT/age.txt
CSD=/var/lib/autodev/code/personal/tzervas/CogSynDelta
HARNESS=/var/lib/autodev/code/personal/tzervas/csd-autodev
```

**Never:** GitHub; `main`/`staging`/`develop`/`dev`; pause 3090 LocalAI;
`git/cabal-forgejo-admin` from this CLI; bind `0.0.0.0`; merge skip-theatre.

---

## TUI (lab dashboard)

URL: **https://code.example.com/lab**  
Bare `https://code.example.com/` 302s to `/lab`. Chat UI is still
https://ai.example.com (not this page).

Unit: `systemctl --user status csd-lab-console` on prime (`:9118`).
Homelab proxies GPU APIs to prime.

| Tab | What you are looking at |
|---|---|
| **GPUs** | Live 3090 / 5080 / 1080 Ti guest: smi, loaded aliases, lock, in-flight prompt |
| **Autodev** | What the worker is doing now: think / in-flight, last tick, queue, heartbeat (~2s) |
| **Goals** | `next_goal`, PHASE-1 board, `need_grok` mailbox, scale/HF flags |
| **Chat** | One LocalAI completion (3090). Not a second WebUI |
| **Pool** | Combined VRAM (3090+5080+1080 Ti when live) |
| **Spawn** | Queue an extra alias (helpers). Autodev/`local/code` still preempts |
| **History** | Lab sessions + autodev-out ticks |
| **Steer** | Pause/resume loops; **set note** (this is how you talk to autodev) |

### Talk to autodev from Steer

1. Open **Steer**.
2. Paste a note (CI job URLs, "merged main into the PR", import/pytest hints).
3. Click **set note**. That writes `/var/lib/autodev/cabal/csd-steer.json` `note`.
4. Confirm: **Goals** ping / Autodev tab, or `GET /lab/api/steer`.

`need_grok` is a **feature flag, default off** (`CSD_NEED_GROK=0`). Autodev
will not set the mailbox or page hosted Grok. Enable with `CSD_NEED_GROK=1`
on the unit if you want the old ping.

The old **stop-after-N-retries** stall is also off (`CSD_STOP_RETRY=0`).
After `CSD_APPROACH_RETRIES` (default **3**) fails of the same SHA/tactic,
the worker **changes approach** (new implement brief: different file/fix)
instead of stopping.

The worker **does not** treat the note as a chat. It:

- keeps `next_goal` (unless you also change it)
- on a red PR, one implement retry if the note looks like CI/branch help
- follows the **live Forgejo PR head**, not a stale heartbeat SHA
- if the feature is behind `main` (or local SHA ≠ PR SHA), **merges**
  `forgejo/<feature>` then `forgejo/main` into the feature (not rebase)

Same note fingerprint is not retried forever (`consumed_note`).

Pause/resume:

- TUI: **pause loops** / **resume**
- CLI: `./scripts/csd-lab-console --steer pause`

Set the board goal:

```bash
"$CSD/scripts/csd-lab-console" --steer-next region-pretrain
"$CSD/scripts/csd-lab-console" --steer-note 'PR #3 lint I001 + missing train_latent_vae_on_public_split; jobs at git.example.com/.../runs/638/jobs/0'
```

JSON (also under `/lab`):

| Method | Path | Use |
|---|---|---|
| GET | `/api/status` | Snapshot (GPUs, feed, app, steer) |
| GET/POST | `/api/steer` | Read/write steer file |
| GET | `/api/goals` | Board + heartbeat + grok mailbox |
| POST | `/api/apply` | Bearer `csd/apply-token` worktree write |
| POST | `/api/loop` | One `csd-autodev-loop --once` |
| POST | `/api/forgejo/*` | whoami, prs, status, wait, comment, merge-gate |
| PATCH | `/api/runs/{id}` | Record an outcome and its structured detail |
| GET/POST | `/api/decisions` | The decision table; evaluate one outcome |
| GET/POST | `/api/escalations` | List; open one (receipt + log tail required) |
| GET/PATCH | `/api/escalations/{id}` | Read one; resolve it with its resolution |
| GET | `/api/escalations/stats` | Escalated-to-autonomous ratio |

Outcome states, retry caps, escalation routing: **[docs/DECISION-TABLE.md](DECISION-TABLE.md)**.

`HEAD` is supported (bare-host probes used to 501).

---

## CLI

### Worker

```bash
systemctl --user status csd-autodev-loop
journalctl --user -u csd-autodev-loop -f
"$CSD/scripts/csd-autodev-loop" --once    # one tick, JSON on stdout
```

`--worker` is what the unit runs (30s sleep). Heartbeat:
`/var/lib/autodev/cabal/autodev-heartbeat.json`. Mailbox:
`/var/lib/autodev/cabal/csd-need-grok.json` (`need: true` wakes hosted Grok).

### Git as autodev

```bash
"$CSD/scripts/csd-autodev-git" -C "$HARNESS" status
"$CSD/scripts/csd-autodev-git" -C "$HARNESS" push forgejo HEAD:feat/bootstrap
```

Author is `autodev <autodev@example.com>`. Vault must be CSD (the
wrapper sets it).

### Forgejo API as autodev

```bash
secret exec TOKEN=git/autodev -- "$CSD/scripts/csd-autodev-forgejo" whoami
secret exec TOKEN=git/autodev -- "$CSD/scripts/csd-autodev-forgejo" prs CogSynDelta
secret exec TOKEN=git/autodev -- "$CSD/scripts/csd-autodev-forgejo" pr CogSynDelta 3
secret exec TOKEN=git/autodev -- "$CSD/scripts/csd-autodev-forgejo" compare CogSynDelta main '<sha>'
secret exec TOKEN=git/autodev -- "$CSD/scripts/csd-autodev-forgejo" status CogSynDelta '<sha>'
secret exec TOKEN=git/autodev -- "$CSD/scripts/csd-autodev-forgejo" wait CogSynDelta '<sha>' --timeout 600
secret exec TOKEN=git/autodev -- "$CSD/scripts/csd-autodev-forgejo" merge-gate CogSynDelta '<sha>' 3
```

`merge-gate` merges **only** if required checks **ran and succeeded**.
Skip / `|| true` / missing runner is not green.

Allowlisted repos: `CogSynDelta`, `memory-gate`, **`csd-autodev`**.

### Lab snapshot (terminal, not the HTML TUI)

```bash
"$CSD/scripts/csd-lab-console"           # one snapshot
"$CSD/scripts/csd-lab-console" --watch   # 5s refresh
"$CSD/scripts/csd-lab-console" --chat 'ping'
```

### Priority

```bash
"$CSD/scripts/csd-autodev-priority" status
"$CSD/scripts/csd-autodev-priority" on    # autodev preempts; Comfy stays wrapped
```

Never pauses LocalAI. Comfy stays **up** behind `host-gpu-b.lock`.

---

## Daily recipes

**See what it is doing**

1. https://code.example.com/lab → Autodev tab
2. `journalctl --user -u csd-autodev-loop -n 20`

**Unstick a red PR after you merged `main` into the feature**

Steer note with the new Actions URLs. The loop must pick the **new**
head SHA (not the pre-merge SHA). If push is `fetch first`, it
`merge --no-edit` from `forgejo/<branch>` then `forgejo/main`.

**Change the goal**

```bash
"$CSD/scripts/csd-lab-console" --steer-next region-pretrain
```

Harness work uses goal names containing `autodev-harness` / `lab-console`
/ `csd-autodev` so files land **here**, not in CogSynDelta.

**Do not merge until**

`merge-gate` `required_ran` and `required_succeeded` are true on the
**current** SHA.

---

## Files on disk

| Path | Role |
|---|---|
| `/var/lib/autodev/cabal/csd-steer.json` | Operator steer (pause, note, next_goal) |
| `/var/lib/autodev/cabal/autodev-heartbeat.json` | Last tick + inflight PR |
| `/var/lib/autodev/cabal/csd-need-grok.json` | Hosted-Grok mailbox |
| `/var/lib/autodev/cabal/autodev-out/*.json` | Per-tick apply/git/pr |
| `/var/lib/autodev/cabal/csd-vault` | `git/autodev`, apply token, LocalAI key |

---

## Secrets (no plaintext)

`git credential.helper store` wrote **plaintext** HTTPS userinfo to
`~/.git-credentials` (Forgejo, GitHub, Hugging Face). Do not `cat` that
file. Do not paste tokens here.

Rotate, then shred:

```bash
# 1) In YOUR bash TTY (hidden passphrase, something only you know):
bash /var/lib/autodev/code/personal/tzervas/csd-autodev/scripts/csd-secret-rotate

# 2) On the websites: rotate Forgejo / GitHub / HF credentials that
#    lived in ~/.git-credentials. I cannot see them and will not print them.

# 3) Stop git from storing plaintext, then shred the store:
git config --global --unset-all credential.helper
shred -u ~/.git-credentials
shred -u /var/lib/autodev/cabal/csd-apply.token   # after lab unit uses vault env

# 4) Forgejo git as autodev only:
"$CSD/scripts/csd-autodev-git" -C "$HARNESS" push forgejo HEAD
```

The rotate script rewraps the CSD vault onto a **new** age identity,
writes **one** passphrase-encrypted backup
(`~/.secret-backups/csd-vault-age.pass.age`), replaces live `age.txt`,
shreds the old identity, remints `git/autodev` without printing it.

Live `age.txt` stays mode 600 for systemd `secret exec`. The passphrase
backup is the only copy you can take off-box.

## Units

```bash
systemctl --user status csd-autodev-loop csd-lab-console
```

Cutover (later): point `ExecStart` at `$HARNESS/scripts/...`. Do not
delete the CogSynDelta copies in the same step.
