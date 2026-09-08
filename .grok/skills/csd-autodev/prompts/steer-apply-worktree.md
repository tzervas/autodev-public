---
name: steer-apply-worktree
description: Steer the autodev loop then HTTP-apply one allowlisted worktree path. No SSH. No operator-main-wip.
---

# steer-apply-worktree

Identity **autodev**. Apply Bearer is `csd/apply-token` in the CSD vault
(`/fleet-data/cabal/csd-apply.token`). Never print tokens. Never SSH.
Never write `operator-main-wip` / `python-ai/memory-gate`. Prefixes only:
`src/`, `tests/`, `docs/`.

## Args

```json
{
  "pause": false,
  "worktree": "p1-08",
  "path": "src/...",
  "content": "..."
}
```

`worktree` allowlist: lab keys `p1-08`, `p1-07` (not `operator-main-wip`).
`pause` / `note` / `next_goal` / `autodev_priority` optional on steer.

## Steps

1. Steer: `./scripts/csd-lab-console --steer …` or `POST /api/steer`
   with `{pause, note, next_goal, autodev_priority}`. Do not pause 3090
   LocalAI. Comfy stays masked.
2. Apply: `POST /api/apply` JSON `{worktree, path, content}` with
   `Authorization: Bearer` from the apply-token file. Refuse `..`,
   non-prefix paths, and the protected worktree.

## Result

```json
{
  "ok": true,
  "steer": {"pause": false, "note": "", "next_goal": "", "autodev_priority": true},
  "apply": {"ok": true, "wrote": "/path", "error": ""}
}
```

`ok` is false if apply refused or steer write failed. No plaintext tokens.
