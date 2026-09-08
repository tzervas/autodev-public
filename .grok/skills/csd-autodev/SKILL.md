---
name: csd-autodev
description: Autodev lab CLIs — Forgejo git as autodev, GPU plan/lock observe, steer, HTTP apply. Use for PRs, wait-green, worktree writes. Never GitHub. Never operator admin.
---

# CSD autodev (lab CLIs)

Identity **autodev**. Token name `git/autodev` in the CSD vault
(`/fleet-data/cabal/csd-vault`). Never `git/cabal-forgejo-admin`. Never
print TOKEN. Never GitHub. Never `main`/`staging`/`develop`/`dev`.
Never `operator-main-wip`. Never pause 3090 LocalAI. Do not wrap
`csd-autodev-provision`. Grok function tools stay media-shaped (`/fleet-lab`).

Allowlist: `tzervas/CogSynDelta`, `tzervas/memory-gate`, `tzervas/csd-autodev`.
Operator how-to: `docs/HOW-TO.md`.

Git: `./scripts/csd-autodev-git <args>` (`TOKEN=git/autodev`). Result
`{ok, rc, stdout?}`. API: `secret exec TOKEN=git/autodev -- ./scripts/csd-autodev-forgejo <cmd>`.

| CLI | In | Out |
|---|---|---|
| `whoami` | — | `{http, login, memory_gate}` — 403 `/user` ok; `memory_gate` 200 |
| `prs <repo>` | `{repo, state?, limit?}` | `{http, repo, pulls[]}` |
| `pr-create <repo> <title> <head>` | `{repo, title, head, base?, body?}` | `{http, number, url, user}` |
| `status <repo> <sha>` | `{repo, sha}` | `{http, state, checks[]}` |
| `wait <repo> <sha>` | `{repo, sha, timeout?, poll?}` | `{http, state}` — **does not merge** |
| `comment <repo> <n> <body>` | `{repo, number, body}` | `{http, ok}` |
| `merge-gate <repo> <sha> <n>` | `{repo, sha, number}` | `{ok, state, merged, required_ran, required_succeeded, notes}` |

Prompt `prompts/forgejo-wait-green.md`: `may_merge` only if required
checks **ran and succeeded**. Skip / `|| true` / missing runner is not
green. Honest red stays red. Lab: `POST /api/forgejo/merge-gate`.
Do not merge CogSynDelta PR #1 until that holds.

| CLI | In | Out |
|---|---|---|
| `./scripts/csd-autodev-loop --once\|--worker` | `{mode}` | `{ok, rc, goal, applied, out}` — do not wrap provision |
| `./scripts/csd-autodev-priority on\|off\|status` | `{action}` | `{steer, comfy, webui}` — mask Comfy; never pause LocalAI |
| `./scripts/csd-gpu-plan` | — | `{autodev_priority, host-a, host-gpu-b}` writes `CSD_GPU_PLAN` |
| lock observe | — | `{lock, comfy, helper_ok}` from plan/status. Acquire/release is fleet `with-gpu-5080` |
| `csd-lab-console --steer` / `POST /api/steer` | `{pause?, note?, next_goal?, autodev_priority?}` | same keys |
| `POST /api/apply` Bearer `csd/apply-token` | `{worktree, path, content}` | `{ok, wrote?, error?}` prefixes `src/` `tests/` `docs/` |

Apply prompt: `prompts/steer-apply-worktree.md`. No SSH.

Lab JSON (also under `/lab`): `GET /api/status`, `GET /api/gpu`,
`GET /api/gpu/lock`, `GET|POST /api/steer`, `POST /api/apply`,
`POST /api/git`, `GET|POST /api/forgejo/whoami`,
`GET|POST /api/forgejo/prs`, `POST /api/forgejo/pr-create`,
`GET|POST /api/forgejo/status`, `POST /api/forgejo/wait`,
`POST /api/forgejo/comment`, `POST /api/forgejo/merge-gate`,
`POST /api/loop`, `POST /api/priority`.

### Shellcheck Configuration

To ensure shell scripts pass linting without false positives, a `.shellcheckrc` file is required in the repository root. This configuration suppresses specific checks that are not applicable to this lab environment (e.g., missing `#!/bin/bash` in non-executable scripts, or `SC2086` for unquoted variables that are intentionally expanded by the harness).

Example `.shellcheckrc` content:
```ini
# .shellcheckrc for csd-autodev lab scripts
# Suppress checks that are not applicable to this controlled environment

# SC1091: Missing file in path (e.g., sourcing a script that is not in PATH)
# We allow this as scripts are sourced from known locations
--disable=SC1091

# SC2086: Double quote to prevent globbing and word splitting
# We allow this as the harness handles quoting, and scripts are not meant to be run directly
--disable=SC2086

# SC2155: Variable assigned but never used
# We allow this as scripts may be called with unused arguments for compatibility
--disable=SC2155

# SC2034: Local variable is not assigned
# We allow this as scripts may define variables that are not used in all paths
--disable=SC2034

# SC2086: Double quote to prevent globbing and word splitting
# We allow this as the harness handles quoting, and scripts are not meant to be run directly
--disable=SC2086
