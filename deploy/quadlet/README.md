# Quadlet units for the autodev stack

Declarative podman containers as systemd units, for the homelab cluster. Every
file here is **documentation-safe**: RFC 5737 addresses, public upstream image
paths, no credential of any kind. Override locally; nothing here should need
editing to be publishable.

## Version floor, stated because it is load-bearing

| directive | needs |
|---|---|
| Quadlet at all (`.container`, `.volume`, `.network`) | **podman >= 4.4** |
| `Notify=healthy` (unit waits for the healthcheck, not the process) | **podman >= 5.0** |
| `HealthOnFailure=` | podman >= 4.3 |

**Debian 12 bookworm ships podman 4.3.1 and there is no 4.4+ in
bookworm-backports.** A `.container` file on 4.3.1 is not an error — it is a
unit that is never generated, which is "absent reads as clean", the failure
this repository is built around. Debian 13 trixie ships 5.4.2 and clears both
floors at once.

So: **check `podman --version` on each host before enabling any of these.**
`deploy/systemd/fleet-gpu-mode-*.service` is the plain-`.service` equivalent
for hosts still on 4.3.1, and is deliberately kept rather than replaced.

## Install

Rootless, per the service account:

```bash
install -d -m 0755 ~/.config/containers/systemd
install -m 0644 deploy/quadlet/*.{container,volume,network} ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start litellm.service
```

Rootful equivalent is `/etc/containers/systemd/`. Quadlet generates a
`.service` per `.container`, so the unit you start is `litellm.service`, never
`litellm.container`.

## Secrets

**No secret appears in any file here, and none should be added.** Two supported
routes, both keeping credentials out of git and out of `argv`:

```bash
# podman secrets — preferred, referenced as Secret= in the unit
secret exec LITELLM_MASTER_KEY=gateway/master -- \
  sh -c 'printf %s "$LITELLM_MASTER_KEY" | podman secret create litellm-master-key -'
```

or an `EnvironmentFile=` pointing at a path **outside the repository**, written
by `secret exec` at deploy time and mode 0600. The units reference
`%h/.config/autodev/*.env` for this and that path is deliberately not in the
tree.

## What is and is not verified

Verified here: INI shape parses, cross-file references resolve (every
`Volume=`/`Network=` names a unit that exists), and no real address or
credential is present.

**Not verified, and not claimable from this container:** every podman
directive. There is no podman and no Quadlet generator in the environment these
were written in, so `AddDevice=`, `HealthCmd=`, `Secret=` and the image tags
are unproven. `systemd-analyze verify` does not read `.container` files at all.
Run `podman-system-generator --dryrun` on a real host before trusting any of
it — this is the same limit recorded for the `.service` units, and it is the
difference between a check and a claim.

## Pinning

Tags below are `:vX.Y.Z` placeholders on purpose. **Pin to a digest before any
host runs these.** An unpinned tag is a supply-chain hole, and
`config/model-router.example.json` already carries `image_digest` for exactly
this reason.
