# Fleet scheduling: placement, priority, and yielding legibly

**Status: captured, not ratified.** This is destined for the private
fleet-platform repository as an ADR superseding its ADR-0012 on the Kubernetes
question. It is written here because that tree is behind Forgejo and a fourth
stale document in it helps nobody. Move it when the sync lands.

The repository is not named here, and neither are the fleet's hosts. This tree
is published, and an architecture document is exactly the kind of artefact that
reads as harmless while being a map of what exists and where. The private
counterpart branch carries both.

---

## Kubernetes is excluded, for a better reason than the usual one

> **Corrected by `docs/QUADLET-CLUSTER.md`, option E.** This section's
> conclusion is stated too broadly. Multi-node rootless Kubernetes DOES exist —
> `KubeletInUserNamespace` is beta in v1.37 and Usernetes forms multi-node
> rootless clusters over Flannel VXLAN. What follows is true **of k3s**, which
> is the implementation that does not support it. Read it as *exclude rootless
> k3s*, not *exclude Kubernetes*.


The common objection — "Kubernetes needs root" — is **not true**.
`KubeletInUserNamespace` (rootless mode) graduated to **beta in Kubernetes
v1.37**, and rootless k3s does not require rootless Docker underneath.

The disqualifying fact is narrower and decisive:

> Multi-node rootless clusters, or multiple rootless k3s processes on the same
> node, are not currently supported.

The fleet is four hosts. Multi-node **was the entire premise of ADR-0012**, so
the rootless variant cannot deliver the thing k3s was adopted for. You can have
rootless, or you can have the cluster, not both. Usernetes Gen2 does multi-node
rootless over podman, and adopting it would trade a well-understood setup for
an experimental one on the fleet's only compute.

Secondary costs that would land anyway: rootless k3s runs in its own network
namespace, service ports below 1024 are republished with a +10000 offset, host
access is port-forward only, and CSI/device access needing direct host devices
is explicitly flagged as possibly non-functional — which is the category GPU
device plugins live in.

**So: podman rootless, per-node systemd + Quadlet, no Kubernetes.** The
objective is that no component anywhere has root, and no account exists that
could be escalated into, so a breakout lands in a user namespace with nothing
under it.

## What excluding Kubernetes does NOT solve

ADR-0012 chose k3s for **scheduling**, not for containers. Its cited defect was
two CPU runners carrying byte-identical labels, so jobs targeting the
services host executed on the operator's desktop. Dropping Kubernetes leaves that open, and
the current owner of that job — `csd-model-router` plus `fleet-gpu-mode` plus
flock plus a timer — is exactly what produced it.

**Candidate: Nomad with `nomad-driver-podman`.** Official HashiCorp driver,
supports **rootless podman sockets on cgroup v2**, pasta networking by default
on podman 5.0+, built-in GPU device plugins, multi-host scheduling, one binary.

Honest caveat, to verify on a host rather than assume: the Nomad *client agent*
generally wants elevated privileges for some operations. Workloads run rootless
via the podman socket; whether the agent itself can be fully unprivileged is a
separate question and is the thing that decides whether Nomad actually satisfies
the objective above.

## Topology

Named with this tree's own sanitised host labels, the ones in
`config/model-router.example.json` and `config/fleet.example.json`. The real
fleet names stay in the private override — a topology table is exactly the kind
of thing that reads as harmless and is a map.

| node | role | GPU jobs |
|---|---|---|
| `host-edge` | services, **CPU runners only** | no |
| `host-a` | 24 GB card, operator's desktop | yes |
| `host-gpu-b` | 16 GB card | yes |
| `host-gpu-b-1080ti` | 11 GB card, VFIO guest on `host-gpu-b` | yes |

Three GPU-capable nodes, one of which is a VM on the second. Host and guest are
separate scheduler clients; GPU accounting is separate, which is correct because
VFIO passthrough is exclusive.

## Constraints by capability, never by hostname

A job declares **what it needs**; the scheduler finds a node that has it. The
1080 Ti takes everything it can support; a job needing a feature Pascal lacks
lands on a card that has it, without anybody naming a host.

**The gap.** Nomad's NVIDIA device plugin fingerprints through NVML and exposes
`memory`, `driver_version`, `cores_clock`, `memory_clock`, `pci_bandwidth`,
`bar1`, `display_state`, `persistence_mode`, `power`.

**`compute_capability` is not among them.** SM-based constraints are therefore
*not* native and must come from node metadata.

**Which is precisely where ADR-0012's defect came from.** Two hand-typed labels
that happened to match is how jobs ran on the wrong host. So:

> **Fingerprint SM at client start** — read `nvidia-smi
> --query-gpu=compute_cap` and write the node metadata programmatically. Never
> type a capability label into a config file.

Same discipline as `identify` reading shebangs instead of maintaining a list of
extensions: a machine-derived label cannot drift from the machine.

## Priority, and the delta that bites

Nomad preemption has been **open source since v0.12.0** for service, batch and
system jobs, driven by job `priority`.

> Only allocations from jobs with a **priority delta of 10 or greater** are
> eligible to be preempted.

That is deliberate — it stops cascades — and it means priorities must be
assigned in **bands**, not as fine-grained numbers. Autodev tiers and GPU job
tiers separated by less than 10 will simply never preempt each other, and the
failure is silent: nothing happens, and nothing says why.

## Yielding is ours, not the scheduler's

**Nomad preemption evicts. It does not pause and resume.** It stops the
allocation and reschedules it, signalling with SIGTERM and a `kill_timeout`.

So the split is:

| job | owner |
|---|---|
| placement, capability matching, priority arbitration | the scheduler |
| what "yield" means for this workload | ours |
| telling dependents what happened | ours |

### "Offload and reload" is three different things

Conflating them is the design error to avoid. They differ in cost, maturity and
blast radius:

1. **Inference server → unload weights.** vLLM `sleep()`/`wake_up()`, SGLang's
   `torch_memory_saver`, Ollama's TTL. Weights move to pinned CPU buffers;
   sub-5-second restores are demonstrated. **Landmine: vLLM's
   `--enable-sleep-mode` has been broken since v0.14.0** and the fleet runs
   v0.28.0. Verify before depending on it.
2. **Training / quantisation → framework checkpoint.** Save optimiser state.
   Standard, application-level, and the only one of the three that is boring.
3. **Arbitrary CUDA process → CRIU + NVIDIA `cuda-checkpoint`** (driver 570+),
   with CRIUgpu doing transparent GPU container checkpoint via podman → runc →
   CRIU. Real, and bleeding edge. Do not make the loop depend on it first.

## Legibility: the part already built

The requirement is that a paused agent is **not** a black box — the model
should see "you are paused, expected back at T", not a crash it has to
interpret. No scheduler provides that. Both halves already exist here:

- **`scripts/csd-agent-mail`** is a durable envelope carrying originating
  agent, target, current state, attempts, evidence, requested action,
  blocking/non-blocking and expected response. A preemption notice *is* an
  envelope, addressed to the roles waiting on the paused unit.
- **`config/decision-table.json`** already separates outcomes demanding
  opposite responses, and **defaults to escalate** for anything unanticipated —
  so a preempted job today is safe but silent.

**Add a ninth state.** `preempted`: action `wait`, `retryable: true`, carrying
`resume_eta` and the preempting job's estimated duration, alongside the
existing `gate`/`measured`/`stage` detail keys.

That is the whole "no black boxes" requirement, expressed in machinery that
exists. The table's founding argument is that `rejected` is not `crashed` and
`refused` is not `failed`; `preempted` is not `crashed` is the same distinction,
and filing a scheduled pause under failure would train the operator to ignore
exactly the signal this is for.

## Open, and which are blocking

| question | blocking? |
|---|---|
| Can the Nomad client agent run unprivileged? | **yes** — it decides whether Nomad meets the objective |
| Is vLLM sleep mode usable on v0.28.0? | yes for class 1 above |
| Does Dify stay excluded? ADR-0002 forbade it; ADR-0012 superseded only the k8s prohibition | no, but it is unresolved |
| Where does LiteLLM actually live — `self-hosted-ai`, per ADR-0002's "clients must not assume LiteLLM :4000"? | no, but autodev points at :4000 today |
