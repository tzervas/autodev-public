# Licensing

**MIT.** Nothing in the dependency graph forces stricter terms — every
dependency, required or optional, is permissive.

| package | used by | licence |
|---|---|---|
| `textual` | the TUI client | MIT |
| `torch` | `csd-autodev-rag-curate embed` only | BSD-3-Clause |
| `transformers` | `csd-autodev-rag-curate embed` only | Apache-2.0 |
| `qdrant-client` | `csd-autodev-rag-curate embed` only | Apache-2.0 |
| `pytest`, `ruff` | development | MIT |
| `pytest-asyncio` | development | Apache-2.0 |
| everything else | — | Python standard library (PSF) |

Apache-2.0 and BSD-3-Clause are both MIT-compatible and neither is copyleft, so
MIT is available for this work.

There is **no vendored or copied third-party code**: no file in this tree carries
another project's copyright header.

## A correction worth recording

The first version of this file claimed every import resolved to the standard
library or to `textual`. That was **false**, and I only found out because I wrote
a script to check the claim instead of asserting it: `torch`, `transformers` and
`qdrant_client` are imported inside `cmd_embed` in `csd-autodev-rag-curate`.

They are function-local, so they are genuinely optional — nothing else in the
tree needs them, and every other command runs without them installed. But they
were **undeclared**, which is misleading to anyone cloning this and would have
made the licence claim wrong by omission. They are now declared as the `rag`
extra in `pyproject.toml`.

Verified 2026-09-08 against the actual imports in `scripts/`, `src/` and
`tests/`, not against memory of what a package usually ships.

---

## Derived work, and the side projects

**The policy, in order:**

1. **Original work is MIT.** Same as this repository. It is the licence that
   makes something easiest for someone else to keep alive.
2. **Derived work inherits the parent's licence**, whatever it is, including
   copyleft. Not reluctantly — a fork that fights its parent's terms is a fork
   nobody can contribute back to.
3. **Adhere to the parent's spirit, not only its text.** This matters most for
   the case these side projects usually start from: a project that is abandoned
   or nearly so. The goal there is to make it *maintainable* — keep the
   attribution, keep the structure recognisable, keep changes contributable
   upstream if upstream ever returns — while meeting goals the original did not
   have.
4. **Source-available is not open source.** A licence that restricts the field
   of use is a maintenance risk even when the restriction does not bite today,
   because it constrains who can fork it if the owner changes direction.

## Fleet components, by licence

The router catalog already carries a `license` key per runtime
(`config/model-router.example.json`). This extends that discipline to
everything under evaluation, because the licence decides whether a component
can be forked if it stalls — which is the failure these projects exist to
survive.

| component | licence | note |
|---|---|---|
| podman, systemd | Apache-2.0 / LGPL | the runtime floor |
| LiteLLM | MIT | the gateway; load-bearing |
| llama.cpp, Ollama, microsoft/BitNet | MIT | serving |
| vLLM, Qdrant | Apache-2.0 | serving, vector store |
| OpenHands | MIT | agent, under evaluation |
| **Nomad** | **BUSL-1.1** | **verified 2026-09-14 — source-available, NOT open source** |
| Dify | Apache-2.0 **with additional conditions** | confirm the restrictions before adopting |
| quad-ops, Headroom, 9Router | **unverified** | confirm at evaluation, before any dependency |

### Nomad is the one that changes a decision

`docs/FLEET-SCHEDULING.md` and `docs/QUADLET-CLUSTER.md` both propose Nomad for
scheduling. It was relicensed from MPL-2.0 to **BUSL-1.1**, which the OSI does
not recognise as open source. It permits non-competing production use — a
private fleet is plainly that — and each release converts to an open licence
after a delay. Since April 2026 its release and support model follows IBM's
enterprise lifecycle.

So it is **usable here and not forkable on demand**, which is exactly the
property point 4 is about. That does not disqualify it; it means the choice
should be made knowing the licence rather than discovering it later. The
alternative already recommended in `QUADLET-CLUSTER.md` — option B, a placement
renderer over Helm-templated manifests and Quadlet — is entirely MIT-able work
over Apache-2.0 and MIT components, and that is now a point in its favour that
was not counted when it was written.

### The rule this repository already demonstrates

The correction recorded above happened because somebody wrote a script instead
of asserting a claim. The same applies here: **the three "unverified" rows must
be checked at evaluation, not at adoption**, and a licence asserted from memory
is exactly the kind of claim this file exists to have been wrong about once.
