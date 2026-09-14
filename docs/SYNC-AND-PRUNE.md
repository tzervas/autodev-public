# Reconciling this branch with Forgejo, and pruning what is genuinely dead

Written to be executed **on the LAN**, where Forgejo is reachable and this
container was not. Its purpose is to stop the next session re-deriving things
that are already settled, and to keep it from acting on architecture that has
moved on.

Most of what follows is probably already in Forgejo. The value of a second,
independent pass is that this one was made **against primary documentation**
rather than from memory, so where the two disagree, the citations below are
worth checking before assuming canon is right.

---

## Part 1 — Branch prune decisions for `tzervas/autodev`

Measured against `origin/feat/bootstrap` in the GitHub mirror.

| branch | ahead | what is on it | verdict |
|---|---|---|---|
| `docs/autodev-spec` | **0** | strict ancestor of `feat/bootstrap` | **PRUNE — provably safe** |
| `feat/gateway-transport` | 79 | `docs/evidence/`, `docs/cohort/`, `docs/operations/`, real `config/model-router.json` | **KEEP** until Forgejo is confirmed to carry it |
| `docs/inference-stack-runbook` | 1 | `docs/operations/*` | **KEEP** — withheld from publication |
| `docs/autodev-local-loop` | 1 | `docs/autodev-local/*` | **KEEP** — absent from public |
| `feat/g5-decision-table` | 7 | decision-table work | unproven |
| `feat/g4-http-feed` | 6 | lab console / run drive | unproven |
| `feat/g4-tui-ergonomics` | 12 | TUI | unproven |
| `feat/rag-identity-provisioning` | 1 | rag scripts | unproven |
| `fix/lab-console-readability` | 3 | console fix | unproven |

### Why most of these are not safe to delete from here

The files those branches carry which are **absent from `autodev-public` are not
missing by accident**. `scripts/csd-publish` withholds them BY PATH, and the
default list is in `load_rules()`:

```
docs/evidence     docs/operations     docs/cohort
```

They are process evidence, deliberately unpublished. So they exist only in the
private repo and on Forgejo, and `feat/gateway-transport` additionally carries
a `config/model-router.json` with real fleet addresses.

Neither tree is a superset of the other: `autodev-public@main` is ahead of
`feat/gateway-transport` by 12 files (`scripts/autodev`, `docs/LESSONS.md`,
`docs/ARCHITECTURE.md`, `config/fleet.example.json`, `LICENSE`, `deploy/**`)
while `feat/gateway-transport` is ahead by 14.

### The one deletion that needs no further checking

```bash
git push origin --delete docs/autodev-spec
```

It is an ancestor of `feat/bootstrap`; nothing is lost by construction.

### The two-minute check that resolves every other row

```bash
git ls-remote --heads https://git.<forge>/tzervas/csd-autodev.git | awk '{print $2}' | sed 's|refs/heads/||' | sort > /tmp/forgejo-heads
git ls-remote --heads https://github.com/tzervas/autodev.git        | awk '{print $2}' | sed 's|refs/heads/||' | sort > /tmp/github-heads

comm -13 /tmp/forgejo-heads /tmp/github-heads   # on GitHub only -> the ONLY copy, do not prune
comm -23 /tmp/forgejo-heads /tmp/github-heads   # on Forgejo only -> mirror is behind
comm -12 /tmp/forgejo-heads /tmp/github-heads   # both -> GitHub copy is disposable
```

Anything in the third list is safe to prune. Anything in the first is the last
copy of something.

---

## Part 2 — Where this branch may have moved past canon

Verified this round **from primary documentation**, not from memory. Check each
against Forgejo before assuming canon wins.

| finding | source checked | consequence |
|---|---|---|
| Kubernetes rootless (`KubeletInUserNamespace`) is **beta as of v1.37**, so "k8s needs root" is false — but **multi-node rootless k3s is unsupported**, which is what actually disqualifies it | k3s + Kubernetes docs | ADR-0012's premise; see `docs/FLEET-SCHEDULING.md` |
| Nomad preemption is **OSS since v0.12.0** for service/batch/system, and requires a **priority delta ≥ 10** | Nomad docs | priority must be banded, or nothing preempts and nothing says why |
| Nomad's NVIDIA plugin fingerprints memory/clocks/bandwidth/power — **not `compute_capability`** | Nomad device plugin docs | SM labels must be fingerprinted from `nvidia-smi`, never typed |
| LiteLLM **free tier** covers virtual keys, budgets, rpm/tpm, per-key model restrictions, spend, all routing, fallbacks, cooldowns, `model_group_alias`. **Enterprise**: SSO >5 users, RBAC, SCIM, audit logs, and **key rotation** | LiteLLM docs | `docs/GATEWAY.md`; rotation stays with `csd-secret-rotate` |
| LiteLLM virtual keys live in **Postgres, not `config.yaml`** — the safety-bearing half is unreconciled runtime state | LiteLLM docs | `config/gateway-keys.example.yaml` + a drift check in the gate |
| vLLM `--enable-sleep-mode` **broken since v0.14.0**; fleet runs v0.28.0 | vLLM issue tracker | blocks the cheapest offload class |
| CUDA checkpoint/restore is real (driver 570+, `cuda-checkpoint`, CRIUgpu) but bleeding edge | NVIDIA + CRIU | do not depend on it first |
| Qdrant JWT RBAC needs only `QDRANT__SERVICE__JWT_RBAC=true` on ≥1.9 | Qdrant docs | retires `csd-autodev-rag-gateway` (203 lines) |
| Quadlet needs podman ≥4.4; `Notify=healthy` needs ≥5.0 — **Debian 13 clears both** | podman docs | the `.service` fallbacks in `deploy/systemd/` are for 4.3.1 hosts only |
| `identify` classifies extensionless scripts **by shebang** — verified on this tree | run directly | retires the 22-path `extend-include` allow-list |
| `cvss` (Red Hat) is pure Python, zero deps, covers v2/v3/v4 | PyPI + repo | retired ~120 lines of hand-written spec arithmetic |

## Part 3 — Do not redo these

Measured and **dropped**, with the measurement recorded in
`docs/goals/conversion.md`:

- **SARIF for the scanner parsers** — the three convertible parsers are 59
  lines; a correct `parse_sarif` costs 50–60. Net zero, and SARIF's `level`
  enum cannot carry per-tool severity policy, which is what those lines are.
  Two traps found while prototyping: ruff emits every finding as
  `level: error`, and semgrep puts `level` on the rule, not the result.
- **Semgrep for the stub audit** — qualified names, marker extraction and
  Protocol/ABC scoping need the AST regardless.

**Owned by the ADK decomposition, do not build here:** `may_change`
enforcement, `escalates_to`, `defect_routing`, durable counters. The boundary
this branch held is checkable:

```bash
git diff --numstat main..HEAD -- \
  scripts/csd-autodev-loop scripts/csd-gateway-complete \
  scripts/csd-autodev-review scripts/csd-autodev-intake \
  scripts/csd-agent-mail config/roles.json src/csd_autodev/
# must print nothing
```

## Part 4 — Open questions, ranked by what they block

1. **Can the Nomad client agent run unprivileged?** Decides whether Nomad meets
   the rootless objective at all.
2. **Does `podman --version` clear 4.4/5.0 on every host?** Debian 13 says yes;
   confirm per host before enabling the Quadlet units.
3. **Is Dify in or out?** ADR-0002 forbade it; ADR-0012 superseded only the
   Kubernetes prohibition, so its status is genuinely unresolved.
4. **Where does LiteLLM actually live?** ADR-0002 says clients must not assume
   `:4000` from the k8s stack, yet `config/fleet.example.json` points there.
5. **Ollama or the router — who owns GPU residency?** `OLLAMA_KEEP_ALIVE` and
   `aliases.*.ttl_s` decide the same thing; two schedulers on one card will
   evict each other and both be correct.
