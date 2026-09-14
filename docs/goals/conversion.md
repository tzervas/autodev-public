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
| G-PUBLISH-1 | Refuse a substitution table whose distinct patterns share a replacement. | done |
| G-SCHEMA-1 | Specify the two reply protocols as JSON Schema in `config/reply-schemas.json`. | done |
| G-UNIT-1 | systemd units for the three GPU modes, exclusivity by `Conflicts=`. | done |
| G-CVSS-2 | Band an OSV advisory by its worst score, not its first. | done |
| G-CORRUPT-1 | Repair the six sanitiser collapses; add a `from != to` tripwire. | done |
| G-ROUTER-EX-1 | Ship `config/model-router.example.json` and the `(example, real)` fallback. | done |
| G-MERGE-1 | Refuse a publish where two originals became one string, checked on the OUTPUT. | done |

### What the first pass through this queue cost, and why

Recorded because the failure was in the GOALS, not in the implementers, and
the next batch is written by whoever reads this.

**G-PUBLISH-1 was rejected once.** The first implementation exempted
`shared_replacement` per RULE rather than per REPLACEMENT, so a marked rule and
an unmarked rule sharing a replacement passed silently — a guard reporting
clean for the case it was installed to catch. It passed every acceptance
criterion, because criterion 2 only exercised a pair the flag *cleared*; "one
flag of two is enough" was never questioned, so it was never wrong. **Write
the criterion that would fail, not the one that demonstrates the feature.**

**G-UNIT-1's goal cited a file this repository does not contain.**
`config/model-router.json` holds the `pool` block with `preempt_autodev` and
`headroom_mib`, and it is in the PRIVATE tree only — `config/` here has no such
file. The implementer checked, found it absent, encoded the policy from the
goal's stated semantics instead, and said so. That is rule 1 of
`docs/WRITING-GOALS.md` enforced from the other direction, and it is the third
time a goal has cost a cycle by describing something that is not there. **A
goal naming a path must be written against the tree it will run in.**

**G-SCHEMA-1's criterion 3 was ill-posed.** It asked the schemas to reject "a
substitution whose arrow is split across two lines". In JSON there is no arrow
— that ambiguity existed only because the grammar was prose, which is the
entire reason the goal exists. The implementer reconstructed the degenerate
instance the failure collapses to (a `from` with no matching `to`) and
documented the reinterpretation rather than silently satisfying the words.

## Open — verifiable without the fleet

| id | goal | state |
|---|---|---|
| G-ONEFILE-1 | `extract_json` in `scripts/csd-autodev-loop` accepts an ARRAY of proposals — its docstring says so, because "the model returns an array whenever the goal touches more than one file". `apply_proposal` writes exactly ONE, which `config/roles.json` already records under `decomposer`: "One file per task is a hard constraint: apply_proposal writes exactly one, so a two-file task lands half and closes anyway." So a two-file reply is parsed successfully, half-applied, and the goal closes as done on a green gate — a failure that reports success, `docs/LESSONS.md` pattern 2. `config/reply-schemas.json` (G-SCHEMA-1) describes a SINGLE object in both protocols and has no array form, so constraining the model to it makes the half-applied reply unrepresentable rather than merely documented. Decide and implement one of: reject an array in `apply_proposal` with a named error so it retries as one file per goal, or apply every element. Do NOT leave it parsing an array and writing one. Prove whichever you choose against a two-element array reply. IMPORTANT: `extract_json` returns `{"_parse_error": ...}` rather than `{}` on failure specifically so the caller can tell "the model proposed no file" from "we could not read what it said" — a rejection here must preserve that distinction rather than collapsing into it. | open |
| G-SANITISE-REGEX-1 | `csd-publish`'s verifier greps the published tree for each `FORBIDDEN` pattern as TEXT. A regex SOURCE literal spells a dot `\.`, so a pattern hunting for `192.168.` never matches the characters `192\.168\.` — and a real subnet reached the published tree that way in `scripts/csd-lab-console:151`, where it also sat dead because nothing in this tree is in that range. The literal is repaired; the BLIND SPOT is not, and it covers every regex in every published file. Make the verifier also test each forbidden pattern against a de-escaped form of the body (or scan for the escaped spelling of each identifier), and prove it against a fixture containing `re.compile(r"\b(192\.168\.1\.\d+)\b")`. IMPORTANT: this is the PRIVATE side's table that needs the fix; the public tree only carries the evidence. | open |
| G-TRIVY-1 | `config/security-policy.json` configures trivy under `containers`, and `run_scanner` at `scripts/csd-security-scan:435` reports `status: "unsupported"` because `PARSERS` has no entry for it — correctly, and loudly, but it means a configured scanner has never run. Trivy's native JSON carries `Results[].Vulnerabilities[].Severity` as `CRITICAL`/`HIGH`/…, which `severity_from_label` already maps, so this is roughly twenty lines. Do NOT reach for SARIF: that conversion was measured and dropped, and the reason is recorded above. Prove a critical trivy finding blocks the merge gate and that an absent trivy is still reported `absent`, never clean. | open |
| G-STUBAUDIT-1 | The `INTENTIONAL: <why>` escape hatch does not silence what `docs/AGENT-ORGANISATION.md` devotes six paragraphs to promising. `record()` correctly exempts an annotated no-op, and then `judge()` at `scripts/csd-stub-audit:351` re-adds the warning unconditionally: `if s["body"] in SILENT_BODIES: s["warnings"].append(SILENT_WHY)`. All three intentional no-ops in this repo (`csd-autodev-rag-gateway:97`, `csd-forge-events:179`, `csd-lab-console:2090`) carry a valid marker and are warned about anyway, with advice to `raise NotImplementedError("GOAL")` — literal `GOAL`, because `s["goal"]` is None — in functions whose contract is to do nothing. Exit code is unaffected, so this is noise rather than a breakage, which is why it survived. One-line fix: `and not s.get("intentional")`. Prove an annotated no-op produces no warning and an UNannotated one still does. | open |
| G-STYLE-2 | `csd-style-repair` marks every tool after pre-commit `{"why": "pre-commit runs it"}` on the sole evidence that a config file exists and the binary is on PATH — nothing reads the config. Live in this repo: `.shellcheckrc` makes shfmt a configured tool, `.pre-commit-config.yaml` has no shfmt hook, so adopting pre-commit silently retired shell formatting while the report claimed otherwise. By the script's own standard (`csd-style-repair:161`) that line is a lie it tells about itself. Subsume PER TOOL by reading the config text: `ruff-lint` only if a ruff hook appears, `shfmt` only if shfmt appears, and report anything left as `{"why": "configured, and pre-commit does not cover it"}`. Prove a thin config that omits a covered tool does not silence it — nothing catches that today. | open |
| G-STYLE-3 | `csd-style-repair --check` is documented at `:27` as reporting what WOULD change without writing, and it writes. The check argv is `["pre-commit", "run", "--all-files", "--show-diff-on-failure"]`; that flag prints a diff, it does not stop hooks modifying the tree, and `pre-commit run` has no dry-run mode. Worse, the double-run is skipped in check mode (`and not a.check`), so a pre-commit exit that was PURELY repairs is filed `unfixable`, `ok: false`, exit 1 — a pipeline halting on formatting, in the gate path, in the script whose first line forbids exactly that. `tests/test_style_repair.py:138` enshrines it under a docstring asserting the false premise. Either run check mode against a throwaway `git worktree`, or rename the mode to what it does. Blast radius is currently zero because nothing calls this script (see G-STYLE-4), which is the only reason it is not urgent. | open |
| G-STYLE-4 | The style role has no caller. `STYLE = ROOT / "scripts" / "csd-style-repair"` is defined at `scripts/csd-adopt:52` and referenced nowhere else in the tree, while `docs/goals/platform.md:86` records the style role as Done and `config/house-rules.json` describes the lint standard as adopted. Wire it, or correct both records. | open |
| G-ROLES-3 | `config/roles.json`'s `_comment` states "`may_change` is enforced, not advisory". Nothing reads `may_change`, `defect_routing` or `escalates_to` — verified by grep across `scripts/` and `src/`; the only reader of the file at all is `csd-agent-mail`, and only for role NAMES. `must_differ_from` is the one property genuinely enforced, and `csd-autodev-review` does it WITHOUT reading the registry that declares it. Enforcement is expected to land with the ADK decomposition, so this is not a request to build it here — it is that the file defining the safety model currently claims a control it does not have, which is `docs/LESSONS.md` pattern 2 in the worst possible place. Qualify the comment as target-state until enforcement exists. | open |
| G-PUBLISH-2 | `scripts/csd-publish` has no test coverage of `sanitise()` itself, only of the collision guard added by G-PUBLISH-1 and of the end-to-end refusals. The three corruptions in the published tree were produced BY `sanitise()`, and the guard now catches the table that causes them but nothing asserts what `sanitise()` does to a file. Add tests for `sanitise()` and `redact_sections()` directly. Read them first: `sanitise` applies `re.sub(pattern, replacement, text)` for each rule IN ORDER, so an earlier rule's output is visible to a later rule — prove whether that ordering dependence is intended, and state the answer in the test. | open |

## Owned by the ADK decomposition — do NOT build here

Recorded so these are not converted twice. Each was considered and withdrawn
because the ADK work owns the seam, not because the gap is not real.

| gap | where it lands |
|---|---|
| `may_change` enforced rather than advisory | `before_tool_callback` / a Plugin |
| `escalates_to` as the ladder | `LoopAgent` + `EventActions.escalate` |
| `defect_routing` to the owning layer | agent transfer + callbacks |
| durable counters (`attempt_count`, the heartbeat) | `SessionService` state — which is why DBOS and Temporal were withdrawn |
| the reply protocol itself | tool calling, which may retire `config/reply-schemas.json` entirely |

**The boundary this branch held to**, mechanically checkable as
`git diff --numstat main..HEAD` against the right-hand column staying empty:
`scripts/csd-autodev-loop`, `csd-gateway-complete`, `csd-autodev-review`,
`csd-autodev-intake`, `csd-agent-mail`, `config/roles.json` and
`src/csd_autodev/**` are untouched.

| id | goal | state |
|---|---|---|
| G-LITELLM-1 | Role-to-model is stated in THREE places: `config/roles.json` (`roles.*.model`), `config/model-router.json` (`aliases.*.jobs` and `aliases.*.host_pref`), and the gateway's own `model_list`/`model_group_alias`. Checked against LiteLLM's router documentation: `jobs` restates `model_group_alias` and `host_pref` restates a deployment's `api_base`, both natively supported. That is a three-way drift surface under the one property the review gate rests on — reviewer and implementer resolving to different weights. Deduplicating changes the router schema, and the PRIVATE file is canonical, so this must be done once against both trees rather than forked. What LiteLLM genuinely cannot express, and which stays: GPU memory, model swapping on a device, and hardware capability matching. | open |
| G-ROUTER-FALLBACK-1 | `scripts/csd-autodev-loop:666,684` read `ROOT / "config" / "model-router.json"` bare, with no fallback to the committed example. `_read_json` returns `{}`, so `ti_ok()` is unconditionally False on any tree without the private file — which is what fails the four remaining `test_autodev_loop` cluster tests. The fix is the two-line resolution already applied to `csd-model-router` and `csd-lab-console`. Held back deliberately: that file is on the ADK side of the boundary and changing it risks conflicting with work in flight. | open |

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
