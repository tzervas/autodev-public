# ADR-0017: Autodev harness is a sibling repo, not CogSynDelta

**Status**: Accepted
**Date**: 2026-08-31
**Decision Makers**: tzervas
**Technical Story**: Autodev was landing loop/lab/forgejo-CLI slices on
CogSynDelta PR #3 (`region-pretrain`). That is out of scope for the
Python-first PoC.

## Context

CogSynDelta is the one-mind PoC (`src/cogsyndelta/poc/`, region
pretrain, router, compress). The self-hosted implementer
(`csd-autodev-loop`, `csd-lab-console`, `csd-autodev-forgejo`,
`csd-autodev-git`) grew in the same tree and the same PR as LatentVAE
WikiText work. Forgejo identity `autodev` must still land CSD product
slices on `tzervas/CogSynDelta` and memory-gate slices on
`tzervas/memory-gate`. The **harness itself** is a third product.

`git/autodev` cannot mint org repos (403). Operator creates
`tzervas/csd-autodev` on Forgejo (private, never GitHub).

## Decision

1. Harness source of truth is Forgejo **`tzervas/csd-autodev`**, local
   path `/home/operator/code/personal/tzervas/csd-autodev`
   (`CSD_AUTODEV_HOME`).
2. `csd-autodev-loop` routes harness goals/paths there. Applying
   `scripts/csd-autodev-*`, `scripts/csd-lab-console`,
   `tests/test_autodev_*`, `tests/test_lab_console.py` into CogSynDelta
   is rejected or rerouted.
3. CogSynDelta PRs stay PoC: `src/`, `tests/test_poc_*`, CSD program
   docs. Region-pretrain does not own the lab UI or the worker.
4. Units may keep `ExecStart` on the CSD copy until operator cutover.
   Do not delete the CSD copies in the same tick as the split.
5. Never GitHub. Never `main` / `staging` / `develop` / `dev`.

## Rationale

### Why This Approach

One PR cannot honestly mix "pretrain LatentVAE on WikiText-2" with
"rewrite the autodev follow_pr SHA glue". Review, CI, and merge-gate
are different contracts.

### Alternatives Considered

#### Option 1: Keep harness in CogSynDelta `scripts/`

- **Pros**: One clone, units already point here.
- **Cons**: Every harness fix dirty-touches the PoC PR.
- **Why Rejected**: Operator: out of scope.

#### Option 2: Submodule inside CogSynDelta

- **Pros**: Version pin.
- **Cons**: Extra merge theatre on both trees.
- **Why Rejected**: Sibling repo is enough; pin later if needed.

## Consequences

### Positive

- CSD CI measures PoC, not lab HTTP.
- Autodev can PR its own loop without `region-pretrain` wait-gate.

### Negative

- Two clones until units switch `ExecStart`.
- Operator must create the Forgejo repo and grant `autodev` write.

## Compliance

- [x] Python-first CSD PoC unchanged
- [x] No GitHub
- [x] Protected trunks untouched
