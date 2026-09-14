# Converting hand-written standards to maintained ones

Autodev's own queue for the agent-independent layer: the parts that do not care
what writes the diff, what serves the model, or at what quantisation. Those
survive whichever way the ADK/OpenHands boundary lands, so they are worth
hardening now and porting once.

**What is deliberately NOT here.** The edit protocol, worktree and sandbox
management, and the reply parsers. Those belong to whoever owns the inner loop,
and converting them before that is decided is work thrown away.

## What was measured, and what it cost

Two conversions were dropped after measuring them rather than after doing them.
Both are recorded because the measurement is the reusable part.

| candidate | claim | measured | verdict |
|---|---|---|---|
| SARIF for the scanner parsers | ~250 LOC out | the three convertible parsers are 59 lines; a correct `parse_sarif` costs 50-60 | **dropped** — net zero, and it trades three tested parsers for one untested one |
| Semgrep for the stub audit | ~200 LOC out | the four-marker contract, the goals cross-reference and qualified names all need Python AST anyway | **dropped** — semgrep plus glue replacing pure Python, one contract across two tools |

Two severity-flattening traps surfaced while prototyping the SARIF reader, and
both would have passed review: **ruff emits every finding as `level: error`**,
and **semgrep puts `level` on the rule, not the result**. A normaliser reading
`result.level` would have collapsed every semgrep severity onto one value and
promoted every ruff finding from medium to high, in a merge gate, silently.

The general lesson, which is the reason this table exists: *the gate scripts
are already well-factored.* The reimplementation worth undoing is in the
plumbing (224 `try:` blocks of defensive JSON parsing, 47 `urllib.request`
sites, a 2381-line `BaseHTTPRequestHandler` console) and in the four big-ticket
items below — not in small, tested, single-purpose parsers.

## Done

| id | goal | state |
|---|---|---|
| G-CVSS-1 | Replace the hand-written CVSS v3.1 arithmetic in `scripts/csd-security-scan` with the `cvss` library. | done |
| G-STYLE-1 | Make `.pre-commit-config.yaml` the style declaration, subsuming the `TOOLS` table when present. | done |

## Open — verifiable without the fleet

| id | goal | state |
|---|---|---|
| G-PUBLISH-1 | `scripts/csd-publish` verifies that no ORIGINAL identifier survives into the published tree, and does not verify that DISTINCT originals map to DISTINCT replacements. Nothing checks the substitution table for collisions. This has already corrupted three places in the published tree: `scripts/csd-lab-console:656` reads `if ip not in {HOST_GPU_B_IP, "203.0.113.99", "203.0.113.99"}` where two different real addresses both became `203.0.113.99`, turning a three-element exclusion set into two; and `tests/test_substitutions.py` lines 70 and 91 now assert `"203.0.113.10" in after and "203.0.113.10" not in after`, which cannot hold, so both tests fail at baseline. Add a fail-closed guard that refuses the publish when two substitution rules with DIFFERENT patterns share the same `replacement`, naming every colliding pattern and the replacement they share. Give it the narrow escape hatch this repo uses elsewhere: a rule carrying `"shared_replacement": true` is exempt, because case variants of one hostname legitimately collapse. A rule without it is not. Prove: a table with two distinct patterns sharing a replacement is refused and BOTH patterns are named in the message; the same table with `shared_replacement` on one rule is allowed; a table with no collision is unaffected; and the existing "an original survived" refusal still works. IMPORTANT: `SUBSTITUTIONS` is built by `load_rules()` at MODULE IMPORT time from `config/fleet.json` or `$AUTODEV_FLEET_CONFIG`, so a test must set the environment variable and THEN load the module — and `tests/conftest.py` deletes every `AUTODEV_*` variable in an autouse fixture, which runs BEFORE the test body, so `monkeypatch.setenv` inside the test is what makes it stick. There is no `tests/test_publish.py`; this script is untested. | open |
| G-SCHEMA-1 | The loop's two reply protocols are defined only by the parser that reads them, so the model cannot check its output against them and strictness relocates the error rather than preventing it — `docs/LESSONS.md` pattern 4. Write them as JSON Schema in `config/reply-schemas.json`: a whole-file proposal (`path` plus `content`) and a substitution proposal (`path` plus `substitutions`, an array of from/to pairs). This is the backend-independent prerequisite for constrained decoding — vLLM takes a schema directly, llama.cpp takes GBNF compiled from one — so the schemas must be data, not code. Prove the schemas against the fixtures that already exist: the `REPLY` constant in `tests/test_substitutions.py` validates, and the whole-file form asserted in `test_a_file_proposal_still_parses` validates. Prove they REJECT the shapes that cost real cycles: a substitution whose from/to arrow is split across two lines, and an object with neither `content` nor `substitutions`. Do NOT change `extract_json`, `parse_substitutions` or any parser — this goal adds the specification, it does not switch anything over to it. `jsonschema` is already a dev dependency. | open |
| G-UNIT-1 | `scripts/fleet-gpu-mode` hand-rolls container lifecycle that systemd owns: an `flock` on fd 9 with a `9>&-` fix for conmon inheriting it, a `wait_healthy` curl poll, a `gpu-mode.json` state file, and rollback-to-previous-mode. Add declarative systemd units under `deploy/systemd/` for the three modes (`dev`, `review`, `free`) that make mode exclusivity a `Conflicts=` guarantee rather than a lock file, and readiness a unit property rather than a poll. Write plain `.service` units invoking `podman run`, NOT Quadlet `.container` files: Quadlet needs podman >= 4.4 and Debian 12 bookworm ships 4.3.1 with no backport, so a Quadlet unit would not start on the target host. State the Quadlet upgrade path in a comment. Preserve two properties from the script exactly: it passes `--device nvidia.com/gpu=all` (CDI, already the current standard — do not change it to `--gpus`), and `config/model-router.json` sets `"preempt_autodev": false`, so the restart policy must NOT let a crashed review container reclaim a card autodev is using. Prove every unit passes `systemd-analyze verify`. Do NOT delete or modify `scripts/fleet-gpu-mode`: which units the host actually runs is an operator deployment decision, and the script is what works today. | open |

## Open — blocked on the fleet

Each needs a live probe, and unverified infrastructure config is the failure
`config/security-policy.json` already names: a check that reports clean because
it never ran.

| id | goal | state |
|---|---|---|
| G-GBNF-1 | Compile `config/reply-schemas.json` to GBNF for the llama.cpp family. Blocked on G-SCHEMA-1 and on a probe: llama-server converts a subset of JSON Schema itself, but `response_format: json_schema` has documented breakage across versions where `json_object` behaves differently, so which path works must be measured rather than assumed. | open |
| G-CAPS-1 | Add `structured-output` to `runtimes.*.caps` in `config/model-router.json` (`json_schema` for vLLM, `gbnf` for llama.cpp/LocalAI, unknown for bitnet-cpp) and probe it at container start rather than per request. Blocked: bitnet-cpp has `"image": null` and "probe reports missing", so its cap cannot be established until an image exists. | open |
| G-ROUTER-1 | Move `demote()`, `record_call()` and `backends()` out of `scripts/csd-gateway-complete` into LiteLLM Router config (`fallbacks`, `cooldown_time`, `allowed_fails`, `model_group_alias`). Keep the two-phase think-then-write, the continuation on `finish_reason=length` with whole-line dedupe, and the rc 9 / rc 6 distinction — LiteLLM has none of those. Blocked on a live gateway. | open |
| G-QDRANT-1 | Retire `scripts/csd-autodev-rag-gateway` (203 lines) in favour of Qdrant's own JWT RBAC. Its docstring says Qdrant "has no native auth on the loopback deployment", which is true of that deployment and not of Qdrant: per-collection read/write keys land with `QDRANT__SERVICE__JWT_RBAC=true` on 1.9+. Blocked on confirming the installed version. | open |
| G-MUSTDIFFER-1 | `must_differ_from` in `config/roles.json` resolves gateway aliases to prove reviewer and implementer are different models. With three runtimes in the pool, two aliases can reach the same GGUF through different runtimes and still look different. Make the check compare resolved weights, not aliases. Blocked on the live gateway's alias table. | open |
