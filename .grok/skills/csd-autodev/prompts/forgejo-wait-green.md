---
name: forgejo-wait-green
description: Poll Forgejo combined status; may_merge only when required checks ran and succeeded. Skip theatre is not green. Does not merge.
---

# forgejo-wait-green

Identity **autodev** (`TOKEN=git/autodev` in the CSD vault). Never GitHub.
Does **not** merge. CogSynDelta PR #1 waits until this gate is true.

## Args

```json
{"repo": "tzervas/CogSynDelta", "sha": "<commit>"}
```

`repo` allowlist: `tzervas/CogSynDelta`, `tzervas/memory-gate` (short name ok).

## Steps

1. `secret exec TOKEN=git/autodev -- ./scripts/csd-autodev-forgejo wait <repo> <sha>`
   — poll combined status; honest red stays red; timeout is not success.
2. `… status <repo> <sha>` — inspect `checks[]` `{context, state}`.
3. `required_ran` = every required context has a non-empty `state` that is
   not pending/missing.
4. `required_succeeded` = every required context is `success`.
5. Skip, `|| true`, empty checks, or missing runner ⇒ **not green**.
6. `may_merge = required_ran && required_succeeded && state == "success"`.

## Result

```json
{
  "ok": true,
  "state": "success|failure|error|pending|timeout",
  "required_ran": false,
  "required_succeeded": false,
  "may_merge": false
}
```

`ok` is transport/CLI success, not merge permission. No plaintext tokens.
