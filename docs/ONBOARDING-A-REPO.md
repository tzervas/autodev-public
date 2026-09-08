# Pointing autodev at a new repository

What has to be true before a repo is a valid target, in the order it has to be
true. Each step ends with a check, because every one of them has failed silently
at least once.

---

## 1. The repo lives in the collective org

The agent develops in `cabal-collective/*` — its own copies. The originals under
`tzervas/*` stay untouchable backups it cannot reach.

Forgejo **refuses to import from itself** ("disallowed hosts", an SSRF guard), so
a Forgejo-to-Forgejo copy is a bare clone push, not a migration:

```bash
# From an external source, the API migration works:
curl -X POST "$FJ/repos/migrate" -H "Authorization: token $ADMIN" \
  -d '{"clone_addr":"https://git.example.com/tzervas/x.git","repo_name":"x",
       "repo_owner":"cabal-collective","service":"git","mirror":false,"private":true}'

# From Forgejo to Forgejo, push a bare clone instead:
git clone --bare https://git.example.com/tzervas/x.git /tmp/x.git
cd /tmp/x.git && git push --mirror https://git.example.com/cabal-collective/x.git
```

**`mirror: false` matters.** A mirror repo refuses pushes, and the agent has to be
able to branch and open PRs.

**Check:** `git push --mirror` does not carry HEAD, so the default branch ends up
as the literal string `HEAD` and every PR targets a branch that does not exist.

```bash
curl -s "$FJ/repos/cabal-collective/x" -H "Authorization: token $T" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["default_branch"], d["size"])'
# expect a real branch name, not "HEAD", and a non-zero size
```

## 2. It is in the autodev team

```bash
curl -X PUT "$FJ/teams/6/repos/cabal-collective/x" -H "Authorization: token $ADMIN"
```

**Check:** the agent can actually fetch it. API permission and git access are
*not* the same thing — an identity can hold `write` in the API and still be
refused over git.

```bash
sudo -u svc-autodev env SECRET_VAULT=/var/lib/autodev/.secrets bash -c '
  /usr/local/bin/secret exec TOKEN=forgejo/token -- git ls-remote \
    https://git.example.com/cabal-collective/x.git HEAD'
# expect a sha, not "Forbidden"
```

## 3. It is on the Forgejo CLI allowlist

`scripts/csd-autodev-forgejo` carries a hardcoded `ALLOW` set. It is a
blast-radius control and is **deliberately not configurable from the environment**
— an allowlist you can set with an env var is not an allowlist. Add the repo
explicitly:

```python
ALLOW = frozenset({
    ...,
    "cabal-collective/x",
})
```

**Check:** the guard still refuses everything else.

```python
repo_id("x")                      # -> cabal-collective/x
repo_id("cabal-collective/other") # -> exits 2, "repo not allowlisted"
```

## 4. Branch hygiene is on

```bash
curl -X PATCH "$FJ/repos/cabal-collective/x" -H "Authorization: token $ADMIN" \
  -d '{"default_delete_branch_after_merge": true, "has_pull_requests": true, "has_actions": true}'
```

**Check by reading it back.** Forgejo accepts `default_delete_branch_ON_merge`
with a 200 and ignores it silently. And note: even set correctly, the flag only
sets the *UI checkbox default* — an **API merge must pass
`delete_branch_after_merge` itself**, or delete the branch afterwards.

Protect the default branch too, so the agent cannot push it even though it holds
write:

```bash
curl -X POST "$FJ/repos/cabal-collective/x/branch_protections" \
  -H "Authorization: token $ADMIN" \
  -d '{"rule_name":"main","enable_push":false,"required_approvals":0}'
```

## 5. Its workflows ask for labels that exist

A `runs-on` naming a combination no runner offers is queued forever and reports
as `skipped`, which reads as success.

| runner | labels |
|---|---|
| homelab instance runner (every repo) | `self-hosted` `linux` `x64` `podman` `compute-cpu` `host-homelab` `scribe-cpu-build` |
| `host-gpu-b-gpu` | `self-hosted` `linux` `x64` `podman` `gpu` `5080` `host-gpu-b` |

There is **no runner that is both `gpu` and `host-homelab`**.

```bash
# What the repo asks for:
grep -rh "runs-on:" .github/workflows .forgejo/workflows 2>/dev/null | sort -u
```

A mismatch is a good first goal for the repo — autodev fixed exactly this in
PR #4 on rung 0.

## 6. Clone it into the agent's workspace

```bash
sudo -u svc-autodev env SECRET_VAULT=/var/lib/autodev/.secrets HOME=/var/lib/autodev bash -c '
  cd /var/lib/autodev && /usr/local/bin/secret exec TOKEN=forgejo/token -- bash -c "
    b=\$(printf %s \"autodev:\$TOKEN\" | base64 -w0)
    git -c credential.helper= -c http.extraheader=\"Authorization: Basic \$b\" \
        clone https://git.example.com/cabal-collective/x.git \
        /var/lib/autodev/work/x"'
```

Forgejo's git-over-HTTP wants **Basic** auth, not the `token` scheme the API uses.

## 7. Write goals, and point the wrapper at it

Goals: see `WRITING-GOALS.md`. Read the source first.

Then in `/usr/local/bin/autodev-tick`:

```bash
export AUTODEV_TARGET_REPO=cabal-collective/x
export AUTODEV_TARGET_WORKTREE=/var/lib/autodev/work/x
```

## 8. One tick, watched

```bash
sudo systemctl start autodev-tick.service
sudo journalctl -u autodev-tick.service -n 1 --no-pager | tail -1
```

Do not enable the timer until a tick has produced a PR you have read. The first
tick on a new repo is where a wrong goal, a missing fixture or a label mismatch
shows up, and watching it costs one minute.

---

## The checks, as one list

- [ ] repo exists in `cabal-collective`, non-empty, real default branch
- [ ] in the autodev team, and `git ls-remote` succeeds **as the agent**
- [ ] added to `ALLOW` in `csd-autodev-forgejo`, and the guard still refuses others
- [ ] delete-on-merge / PRs / actions verified **by reading back**
- [ ] default branch protected against direct pushes
- [ ] every `runs-on` matches a label set some runner actually offers
- [ ] cloned into `/var/lib/autodev/work/`
- [ ] goals written from the source
- [ ] one watched tick produced a PR worth reading


