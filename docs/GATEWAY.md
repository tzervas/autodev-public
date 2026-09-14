# The gateway, and what it is allowed to own

LiteLLM is becoming load-bearing rather than a passthrough. This is the audit
of which autodev features it already provides in the **free self-hosted (MIT)**
tier, which stay ours, and how each is captured as configuration rather than as
somebody's shell history.

Everything below was checked against LiteLLM's own documentation. Where a
feature's tier could not be established from the docs, it says so rather than
guessing — a plan that is load-bearing on a gated feature fails at the worst
moment, and "probably free" is not a licence position.

---

## The tier line

**Free, self-hosted, MIT.** Virtual keys, per-key budgets and `max_budget`,
rpm/tpm limits, model access restrictions per key, spend tracking, every
routing strategy, `fallbacks` and `context_window_fallbacks`, `allowed_fails`
and `cooldown_time`, `num_retries`, `model_group_alias`, `credential_list`,
logging callbacks, semantic caching, the admin UI. Key and budget features
require a **Postgres** database.

**Enterprise.** SSO/SAML beyond five users, org and team RBAC, SCIM, audit
logs, enterprise guardrails, and **key rotation** — both the manual
`/key/{key}/regenerate` endpoint and scheduled rotation.

**Not established.** Whether the Prometheus `/metrics` endpoint is gated. Treat
it as unknown and prove it on the fleet before any dashboard depends on it.

That rotation line matters here: `scripts/csd-secret-rotate` exists, and key
rotation is precisely the thing the free tier does not do. Rotation of the
gateway's own virtual keys stays ours.

---

## Feature map

### Adopt — LiteLLM does this, and better

| autodev today | LiteLLM (free) | note |
|---|---|---|
| role → gateway alias, by convention | `model_list` + `model_group_alias` | the alias table becomes one file |
| `demote()` in `csd-gateway-complete` | `allowed_fails` + `cooldown_time` | a failing backend is isolated by the router |
| ad-hoc retry around a dead backend | `fallbacks`, `context_window_fallbacks` | context-window fallback is free and we do not have it |
| `record_call()` | spend tracking + `success_callback` | needs Postgres |
| per-call cost accounting | native | |
| lab console telemetry ingestion | `success_callback` to Langfuse/OTel | the console keeps the GPU half |
| credentials pasted per script | `os.environ/` + `credential_list` | the vault still fills the environment; that seam stays ours |

### Adopt — and this one changes a safety property

**A virtual key per role.** `docs/AGENT-ORGANISATION.md` states that agent
identity must be separate from model identity, and today that separation is a
convention: every role reaches the same gateway with the same credential, and
`must_differ_from` is an assertion `csd-autodev-review` makes *after the fact*
by asking `/model/info` what two aliases resolved to.

A virtual key carries model access restrictions. Give the reviewer a key that
**cannot reach the implementer's model at all**, and a correlated review
labelled independent stops being a thing we detect and becomes a thing the
gateway refuses. That is the difference between a check and a boundary, and it
is free.

It also gives each role its own budget and rpm/tpm ceiling, so a runaway
continuation loop is bounded by the gateway rather than by noticing.

### Keep — LiteLLM has no equivalent

| ours | why it cannot move |
|---|---|
| `plan_budget()` sizing a budget from the prompt actually sent | LiteLLM takes `max_tokens`; it does not compute one |
| effort degradation (high → low → none) | no concept of degrading the thinking and keeping the answer |
| two-phase think-then-write | one call is one call |
| continuation on `finish_reason=length` with whole-line dedupe | not a router concern |
| rc 9 (cut off, resumable) vs rc 6 (thought until the budget was gone) | the distinction that made truncation look like failure |
| `csd-budget-calibrate` p99 sizing | LiteLLM supplies the DATA; the sizing rule is ours |
| per-runtime structured output (`json_schema` vs GBNF) | the proxy passes `response_format` through; it does not translate |
| everything in `config/model-router.json` | no GPU memory, device swapping, or hardware capability matching |

### Beware — the semantics differ

**`fallbacks` is not escalation.** LiteLLM falls back when a call *fails*.
`ask_for_help()` escalates when an agent is *stuck* on a green call — a
deliberate change of strategy to a different role at a different budget,
carrying a structured stuck-report. Wiring escalation to `fallbacks` would fire
it on the wrong trigger and never on the right one. They coexist: fallbacks for
a dead backend, escalation for a live one that is not getting anywhere.

---

## Capturing it: CaC, and the gap

`config.yaml` is genuinely declarative and belongs in git. Secrets stay out via
`os.environ/VAR`, which resolves at runtime, and `credential_list` de-duplicates
a credential across deployments. `config/litellm.example.yaml` in this repo is
the documentation-safe version, same contract as `fleet.example.json`.

**The gap, and it is the important part of this document.** Virtual keys,
budgets and teams are **not** in `config.yaml`. They are rows in Postgres,
created through `/key/generate`. So the half of the gateway that carries the
safety property — which role may reach which model — is *runtime state*, not
configuration, and nothing reconciles it against a declared intent.

That is the same shape as every defect in `docs/LESSONS.md`: a control whose
real value lives somewhere nobody is looking, which drifts from the document
describing it without anything going red.

So CaC here needs a reconciler, not just a file:

1. declare the intended keys — role, permitted models, budget, rpm/tpm — in a
   committed file;
2. read the live keys from `/key/list`;
3. report the difference, and apply it only when asked;
4. **fail the merge gate when the live state does not match the declaration**,
   because a reviewer key that has quietly gained access to the implementer's
   model is exactly the finding this is for.

Step 4 is the one that makes it a control rather than a convenience. Without
it, the declaration is a comment.

## IaC

The proxy, its Postgres, and the model runtimes are containers on a systemd
host. `deploy/systemd/fleet-gpu-mode-*.service` is the established pattern:
plain `.service` units invoking `podman run`, because Debian 12 ships podman
4.3.1 and Quadlet needs 4.4. The gateway and its database follow the same
shape, with `After=`/`Requires=` ordering so the proxy does not start before
its database, and the Quadlet upgrade path noted in the same place.

`DATABASE_URL`, `LITELLM_MASTER_KEY` (must begin `sk-`) and the model API keys
arrive by `secret exec` from the agent's own vault, never in a unit file and
never in `config.yaml`.

## Ordered, and what each step needs

| step | unblocks | needs |
|---|---|---|
| 1. `config.yaml` in git with `model_list` + `model_group_alias` | the alias table stops being three files | the live alias→backend map |
| 2. `allowed_fails`, `cooldown_time`, `fallbacks` | retires `demote()` | step 1 |
| 3. Postgres + spend tracking | retires `record_call()` | a database |
| 4. virtual key per role | makes reviewer ≠ implementer a boundary | step 3 |
| 5. key declaration + drift check in the gate | makes step 4 a control | step 4 |
| 6. `success_callback` to Langfuse/OTel | retires the console's telemetry half | step 3 |

Steps 1 and 2 are pure configuration and can be done from the current tree.
Steps 3 onward need the fleet.
