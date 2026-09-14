# Identity, authorization, and the tools that overlap

**Status: analysis, nothing chosen.** Applies the one-tool-one-job rule to
four candidates raised for the Kubernetes route, plus the transport and
discovery layer.

---

## The overlap that needs deciding first

**agentgateway is an LLM gateway. So is LiteLLM.** That is the same job.

agentgateway (Linux Foundation, Rust) is a data plane for agentic traffic
spanning **inference gateway, LLM gateway, MCP gateway and A2A gateway** with
full MCP and A2A support. LiteLLM was made load-bearing on the strength of its
routing, virtual keys, per-key model allow-lists, budgets and spend — see
`docs/GATEWAY.md`.

Under the one-tool-one-job rule these cannot both own LLM traffic. Three
coherent outcomes, and *which* matters because a key allow-list is currently
the mechanism making reviewer-is-not-implementer a boundary:

| outcome | condition |
|---|---|
| **LiteLLM keeps LLM traffic**, agentgateway is not adopted | the default; nothing changes |
| **agentgateway replaces LiteLLM** | only if it has an equivalent to per-key model restrictions, budgets and spend. **Unverified.** If it does not, this trades a proven safety property for protocol breadth |
| **Split by protocol** — LiteLLM owns OpenAI-shaped LLM calls, agentgateway owns MCP and A2A | defensible, because those are genuinely different protocols and neither tool does the other's. This is the only "both" that is not an overlap |

The third is probably right, and it is conditional on A2A and MCP actually
being in the architecture — which `docs/AGENT-BUILD-PLAN.md` proposes and
nothing has yet built.

**kagent is a third agent framework.** It runs AI agents natively in
Kubernetes (CNCF Sandbox). ADK and OpenHands already contend for the inner
loop and `AGENT-BUILD-PLAN.md` phase 3 says ONE agent, for the reason that two
make every failure a question of which one did it. kagent is excluded by that
rule unless it *replaces* one of them — it is not an addition.

## OpenBao: one tool, several jobs, no overlap

This is the shape the rule likes. OpenBao (Linux Foundation OpenSSF, **MPL-2.0
— genuinely open source**, unlike the BUSL licence noted in
`docs/LICENSING.md`) would own:

| job | today | with OpenBao |
|---|---|---|
| secret storage and injection | a vault directory + `secret exec` | KV v2 |
| **mTLS certificates** | nothing | the PKI engine, as an internal CA with short-lived certs |
| JWT issuance | nothing — Qdrant JWT RBAC and LiteLLM keys are each hand-managed | JWT/OIDC issuance |
| dynamic database credentials | a static Postgres password | dynamic, per-consumer, expiring |
| SSH CA | static keys | SSH certificate authority |

Four of those are jobs nobody currently owns, and the mTLS one is the answer
to a requirement raised with no mechanism attached. Replacing `secret exec`'s
*interface* is not required — it can front OpenBao instead of a directory, so
the existing call sites are unchanged.

The honest cost: OpenBao is a stateful service with an unseal ceremony, and it
becomes a hard dependency of everything. Its own availability and its own
backup are now load-bearing — and `docs/FLEET-RESOURCES.md` already lists the
vault among the four things that are genuinely irreplaceable.

## Cerbos: complementary, if the boundary is drawn

Cerbos is an Apache-2.0 policy decision point — RBAC, ABAC and conditional
rules in YAML, stateless, AuthZEN compliant, free with no usage caps. The
commercial product is the managed control plane, not the PDP.

**It does not overlap Kubernetes RBAC, provided the boundary is explicit:**

| layer | owner | governs |
|---|---|---|
| cluster API | Kubernetes RBAC | who may create a pod, read a secret, scale a deployment |
| application decisions | Cerbos | may *this role* act on *this resource* under *these conditions* |

That second row is exactly `config/roles.json`'s `may_change` — which
`docs/goals/conversion.md` records as **declared and enforced by nothing**.
Cerbos is a real candidate to own it, and it is ABAC-shaped: "the implementer
may write paths matching these globs, in this repository, when the goal is
open" is a conditional rule, not a role grant.

**Where it WOULD overlap:** Cerbos can act as a Kubernetes admission
controller, which is OPA/Gatekeeper's job. Pick one for admission. Using
Cerbos for application authorization and something else for admission is two
policy languages for one concern — the thing the rule exists to prevent.

## Transport and discovery

**mTLS** — the PKI engine above issues it; the mesh or the gateway enforces it.
Do not acquire a service mesh for this alone: on a fleet this size, mTLS
between a handful of services is certificates plus configuration, not a
control plane. Revisit if the service count grows past what can be reasoned
about.

**mDNS and CoreDNS do not overlap, and must not be made to.** CoreDNS owns
in-cluster names (`svc.cluster.local`); mDNS/avahi owns LAN discovery for
things outside the cluster. One boundary, stated: **anything in the cluster is
found by CoreDNS; anything outside it is found by mDNS.** A name resolvable by
both is a name that will resolve differently depending on who asks, which is
the byte-identical-runner-labels defect in a different costume.

## Storage: blocked on hardware, and it should be

Choosing filesystems, device classes, and volume claim shapes "based on the
hardware I actually have" needs the hardware inventory, which is not in any
tree here. Deciding without it would be exactly the kind of guess this
programme keeps finding.

What the decision needs, per node:

```bash
lsblk -o NAME,SIZE,ROTA,DISC-MAX,MODEL,SERIAL,MOUNTPOINT,FSTYPE
nvme list                                    # NVMe inventory
for d in /dev/sd? /dev/nvme?n1; do
  smartctl -A "$d" | grep -Ei 'Percentage_Used|Wearout|Total_LBAs_Written|Power_On_Hours'
done
vgs; pvs; lvs                                # existing LVM, if any
findmnt -t btrfs,ext4,xfs,zfs -o TARGET,SOURCE,FSTYPE,OPTIONS
```

`ROTA` separates mechanical from solid state; the SMART lines give wear and
written volume, which is what decides which device takes the write-heavy
classes and which is already too far through its endurance to be given more.
Model and serial matter because consumer drives vary by an order of magnitude
in rated TBW, and capacity is not a proxy for it.

### The decisions that inventory unblocks

- **Filesystem per class.** XFS for large sequential (weights, archives); ext4
  for general; btrfs only where its snapshots or checksums are actually wanted,
  since its write patterns are not free on flash. ZFS is a separate
  conversation — its ARC competes for the RAM that inference needs.
- **Device classes.** `nvme` / `ssd` / `bulk`, mapped to TopoLVM device classes,
  **derived from `ROTA` and SMART rather than declared** — the same discipline
  as fingerprinting SM.
- **How the mechanicals are served.** Local to their node, or exported over NFS
  to the cluster? Local is simpler and faster; exported means any node can
  archive. The existing tree already has an NFS topology playbook, so this is
  a question of extending it rather than choosing from scratch.
- **PVC shape.** Local PVs with node affinity pin a pod to a disk, which is
  correct for weights and wrong for anything that must move. That is a
  per-workload decision, not a fleet-wide one.

## Open questions

1. Does agentgateway have an equivalent to LiteLLM's per-key model allow-list?
   **This decides the gateway question**, because that allow-list is what makes
   reviewer-is-not-implementer a boundary rather than a check.
2. Is kagent a replacement for ADK/OpenHands, or is it out? It cannot be an
   addition.
3. Does Cerbos own `may_change`, and if so does OPA/Gatekeeper still own
   admission, or does Cerbos take both?
4. Hardware inventory — nothing in the storage section can be decided without
   it.
