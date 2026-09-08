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
