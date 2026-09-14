# Elasticity and storage tiering

**Status: design, not ratified.** Companion to `docs/QUADLET-CLUSTER.md`
option E, which is the orchestrator this assumes.

The goal: every workload declares a right-sized minimum, grows into shared
capacity on demand, and data lands on the storage that suits it — without
burning out consumer SSDs, and without a backup regime beyond long-term
compressed archives on the mechanicals.

---

## Elasticity: this is now production-ready, which it was not recently

| capability | status |
|---|---|
| **In-place pod resize** (`InPlacePodVerticalScaling`) | alpha v1.27 → beta v1.33 → **GA in v1.35** |
| In-place resize of **pod-level** resources | beta in v1.36 |
| **VPA `InPlaceOrRecreate`** — try non-disruptive, fall back to recreation | alpha in VPA 1.7.0, needs k8s ≥1.33 |

So "minimums with dynamically allocated requests" is the native model:
`requests` is the guaranteed floor and what the scheduler packs against,
`limits` is the ceiling, and **Burstable QoS** (requests < limits) *is* the
shared negotiable space. VPA right-sizes the floor from observed usage, and
in-place resize now applies it without evicting the pod.

### Two edges that bite, and they are not symmetric

**CPU is compressible. Memory is not.** Exceeding a CPU limit throttles;
exceeding a memory limit is an OOM kill. So a shared negotiable pool works
differently for each:

- **CPU** — overcommit freely. Contention degrades latency, which is
  recoverable and visible.
- **Memory** — overcommit only into headroom that physically exists. On a
  fleet whose whole purpose is LLM inference, memory demand is large, spiky,
  and correlated across workloads; when several burst together, something is
  killed. *Which* something is decided by QoS class and OOM score, not by
  importance. An implementer mid-goal and a log shipper are equally eligible.

**Memory shrinks badly.** A new memory limit can sit below what the process is
already using, so downsizing memory in place often cannot be applied and falls
back to recreation. Changing QoS class — Burstable to Guaranteed — also forces
recreation. Both matter here because recreation of an inference pod means
reloading weights.

**Practical consequence:** give the agent loop and the inference servers
`Guaranteed` QoS (requests == limits) and let everything else be Burstable
around them. The negotiable pool is then genuinely negotiable, and the thing
that must not be OOM-killed is not eligible to be.

## Storage tiering: the mechanism exists

**TopoLVM** is a capacity-aware CSI plugin backed by LVM. It supports HDD,
SATA SSD and NVMe, and its `topolvm.io/device-class` parameter creates a
separate StorageClass per device type — so a PVC asks for `nvme`, `ssd` or
`bulk` and lands on the right spindle. It reports free capacity per node, so
the scheduler places pods where the storage actually is. Thin provisioning,
snapshots and online expansion come with it.

That is the whole "SSDs do what they are good at" mechanism, already built.

**Disk is not negotiable the way CPU and memory are.** You cannot burst
capacity. Thin provisioning lets you overcommit a pool, and filling a thin
pool is worse than filling a filesystem — writes fail mid-transaction. Any
overcommit here needs a hard stop well before the pool is full, not an alert
at 95%.

### What goes where, and why

| data | tier | reason |
|---|---|---|
| model weights | NVMe / SSD | **the ideal SSD workload** — written once, read many, large sequential reads. Reads cost no endurance at all |
| container images | SSD | read-mostly after pull |
| control-plane datastore | the **highest-endurance** device available | fsync-heavy small writes; this is the classic consumer-SSD killer |
| training checkpoints | bulk, or an endurance-rated device | large, write-heavy, and written on a schedule you control |
| logs and metrics, post-rotation | mechanical | sequential, high volume, low value per byte |
| long-term compressed archives | mechanical | sequential writes; spinning rust does not wear out this way |

### The endurance rule that decides an architecture choice

**Do not put replicated block storage on consumer SSDs.** Longhorn, Ceph and
friends multiply every write by the replica count, and write amplification is
exactly what consumes a TLC or QLC drive's rated TBW. A three-way replica turns
one write into three, forever.

TopoLVM is *local and unreplicated*, which is usually described as its
limitation. On this fleet it is the feature: one write is one write. Resilience
comes from the archive tier and from the fact that most of what is on SSD —
weights, images — is reproducible from elsewhere.

### The gap worth building

Searching for it found the mechanism for tiering and **nothing** for treating
wear as a scheduling signal. So:

> NVMe exposes `Percentage_Used`; SATA exposes `Media_Wearout_Indicator`. A
> node-level exporter publishes it, and a controller **taints the node or
> marks the device class unschedulable for write-heavy classes** once it
> crosses a threshold.

That is a small, specific, genuinely missing component — an exporter and a
policy — and it turns "don't burn out the consumer SSDs" from an intention
into a mechanism. It is the same discipline as fingerprinting SM rather than
typing it: a measured property, enforced, instead of a rule someone remembers.

## Backup: the stance is right, with four named exceptions

"Long-term compressed archives on the mechanicals before offload or deletion,
and not much else" is a defensible position for this fleet. Most of what is
here is reproducible: model weights re-download, container images re-pull,
code is in git and mirrored.

Four things are not, and one of them is a total-loss single point:

1. **The forge's own database** — issues, pull request history, CI results.
   The git objects are mirrored; the *forge state around them* is not, and the
   mirror is already known to lag and to be missing branches. The canonical
   host for everything, unbacked, is the largest single risk on the fleet.
2. **The secret vault.** Nothing else can regenerate it.
3. **The private substitution table** (`fleet.json`'s `publish` block). Without
   it a publication cannot be reproduced — and it currently needs two fixes
   before the next publish will even succeed.
4. **In-flight training checkpoints.** Hours of GPU time, and the only copy.

None of these is large. All four fit comfortably in the archive tier that is
already planned — the change is making that scheduled and verified rather than
incidental. An archive nobody has restored from is a backup nobody has.

## Open questions

1. Does `podman kube play` honour `resources.requests`/`limits` and
   `resizePolicy`, or drop them the way it drops `replicas`? **This decides
   whether any of the elasticity above survives option A/B**, and is
   irrelevant under option E.
2. Which device on each node is actually highest-endurance? Consumer drives
   vary by an order of magnitude in rated TBW; this needs reading off the
   hardware, not assumed from capacity.
3. Does the control-plane datastore land on a device that can take it, and
   what is the measured write rate?
4. Can TopoLVM's device classes be driven from the same fingerprinted node
   facts as GPU capability, so a disk's class is derived rather than declared?
