# A cluster experience that keeps podman's security posture

**Status: design options, nothing chosen.** Destined for its own repository.
Captured here so the reasoning survives with the rest of the fleet planning.

The goal: manage the whole heterogeneous fleet from one release, declaratively,
**completely rootless**, without a component anywhere holding root.

---

## "Kubernetes-like" is two separate things, and only one is hard

Bundling them is why this looks bigger than it is.

1. **Declarative desired state, applied as one release.** Write it down, apply
   it, the fleet converges. *This is largely solved already — see below.*
2. **Scheduling and placement across heterogeneous nodes.** Deciding *which*
   host runs what, from capabilities rather than hostnames. *This is the
   actual gap.*

## What podman already gives you, verified

`podman kube play` accepts **Pod, Deployment, DaemonSet, Job,
PersistentVolumeClaim, ConfigMap and Secret**. Quadlet has a **`.kube` unit**
that plays a manifest and hands its lifecycle to systemd, passing ConfigMaps
through `--configmap`.

So the authoring format can be Kubernetes YAML, the runtime is rootless podman,
and the supervisor is systemd. **That is most of item 1, today, with no new
software.**

And `helm template` renders charts to YAML **without a cluster or an API
server** — Helm is a templating engine that happens to ship a Kubernetes
client. A chart can render manifests that `podman kube play` runs. One release,
values files per host class, no Kubernetes.

### The trap, and it is the kind this fleet cares about

> Podman accepts Deployment YAML, but **the actual replica count is ignored and
> set to 1**.

A manifest saying `replicas: 3` applies cleanly, reports success, and runs one.
That is a failure that reports success — `docs/LESSONS.md` pattern 2 — and it
is silent. Anything built on this must either reject `replicas != 1` at render
time or never emit the field.

Second constraint: Quadlet units do not support `User=`, `Group=` or
`DynamicUser=`. Rootless means creating the service account and placing units
in **that user's** unit search path, which is what `deploy/quadlet/README.md`
already documents. It is a deployment shape, not a limitation.

## The gap, restated smaller than it looks

Quadlet guarantees lifecycle **within one host**. Nothing distributes units
across hosts or decides which host should get which.

But look at the actual placement problem on this fleet:

| workload | where it can run | decided by |
|---|---|---|
| services, CPU runners | one node | hardware |
| implementer | the 24 GB card | VRAM |
| reviewer | a different card than the implementer | independence |
| RAG / index | the 11 GB card when live | policy |
| a GPU job needing sm_80+ | either of two cards | **capability** |

Only the last row is genuinely dynamic, over **three** candidate nodes. That is
a constraint satisfaction problem with a handful of variables, not a scheduler.

**And the constraint language already exists.** `config/model-router.json`
encodes `needs_caps`, `prefer_caps`, `exclusive_with`, `vram_mib`, `pool_ok`
and host capability sets with `caps`/`lacks`. That vocabulary was written for
this exact question.

What it does *not* do is emit anything. It makes decisions **at runtime, inside
a script**. The same logic evaluated **at render time, emitting manifests per
host**, is the missing piece — and it is a change of output, not of algorithm.

## The options, with what each actually costs

| | approach | cost | gets you | misses |
|---|---|---|---|---|
| **A** | `helm template` → k8s YAML → Quadlet `.kube`, per-host GitOps | low, no new software | one release, declarative, rootless, familiar format | placement is manual — each host is told what it runs |
| **B** | **A + a placement renderer** driven by node facts and the existing capability vocabulary | moderate, one small component | A, plus capability-based placement over a fleet you can enumerate | no live rescheduling; convergence is per-apply |
| **C** | Nomad + `nomad-driver-podman` | new control plane | real scheduling, preemption, GPU device plugins | whether the *agent* can run unprivileged is unresolved, and it is the whole objective |
| **D** | adopt or extend Usernetes for multi-node rootless Kubernetes | highest | genuine Kubernetes, rootless, multi-node | someone else's roadmap, experimental, on the fleet's only compute |

### Recommended: B

Not because it is clever, but because it is mostly assembly:

- **Authoring** — Helm charts, rendered with `helm template`. Real charts, real
  values files, no cluster.
- **Placement** — a renderer that reads node facts (**fingerprinted**, per
  `docs/FLEET-SCHEDULING.md`: SM from `nvidia-smi`, never typed) and the
  existing `needs_caps`/`lacks` vocabulary, and decides which manifests render
  for which host.
- **Delivery** — rendered manifests land in each host's rootless Quadlet search
  path; systemd converges.
- **Reconciliation** — a pull on a timer per host, or a push. Either way the
  desired state is in git and the fact is `systemctl --user`.

The custom surface is **one renderer**. Everything else is podman, systemd,
Helm and git, all of which already exist and none of which needs root.

### Why not C first

Nomad is the right answer if live rescheduling and preemption are required, and
`docs/FLEET-SCHEDULING.md` argues they will be once training shares the cards.
But it introduces a control plane whose own privilege requirements are the
unresolved question, and the objective here is that *nothing* holds root. B and
C are not exclusive: B is the declarative layer, and C could later own
placement without changing how anything is authored.

## Prior art to evaluate before writing anything

- **quad-ops** (`trly/quad-ops`) — GitOps for Quadlet, converts Compose to
  Quadlet units, works system-wide and rootless. Directly adjacent. Evaluate
  whether it already does the delivery half before building it.
- **`podman kube play`** and Quadlet `.kube` — the authoring and lifecycle half,
  already shipped.
- **`podman farm`** — multi-node podman, for builds. Check whether its node
  abstraction is reusable.

The one-tool-per-job rule applies to this design itself: if quad-ops owns
delivery, the new component is *only* the placement renderer.

## Open questions

1. Does quad-ops already cover delivery, and does it do so rootless
   per-user? If yes, the build shrinks to the renderer.
2. Does `podman kube play` honour everything a chart would emit — probes,
   resource limits, node selectors — or silently drop them the way it drops
   `replicas`? **Each silent drop is a defect that reports success**, so this
   needs an explicit compatibility matrix before anything depends on it.
3. Is `helm template` worth it over plain Jinja/`kustomize`, given no cluster?
   Charts buy ecosystem familiarity and values-file layering; they also buy
   Kubernetes semantics that podman only partly implements.
4. Where does GPU device access land? CDI (`AddDevice=nvidia.com/gpu=all`)
   works in a Quadlet `.container`. Whether a `.kube` manifest can express the
   same is unverified.

## Repository

Its own, when created. Suggested name: **`quadlet-fleet`**.

It is not autodev (which consumes the fleet, not defines it) and not the
platform repository (which is this fleet's concrete deployment, whereas this is
a general mechanism). Keeping it separate is also what makes it publishable
without the fleet's real values in it.
