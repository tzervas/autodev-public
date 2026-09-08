#!/usr/bin/env bash
# Give the OPERATOR the access the CLI needs, and nothing more.
#
# The agent runs as its own account with a private home (0750). That is the
# containment, and it stays: the operator cannot read the agent's vault, its
# worktrees, or its harness. But `autodev submit`, `queue`, `status`, `pause`
# and `target` all read or write agent-owned state, and a CLI that tells you to
# go and type sudo is a wrapper around advice.
#
# ACLs rather than group membership, for two reasons: they name ONE user
# instead of widening a group, and they take effect immediately rather than at
# the operator's next login.
#
# Idempotent. Safe to re-run after a deploy.
set -euo pipefail

AGENT="${AGENT:-svc-autodev}"
OPERATOR="${OPERATOR:-operator}"
HOME_DIR="/home/${AGENT}"
STATE="${HOME_DIR}/state"

need_root() { [ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }; }
need_root

# Traverse only. The operator can pass THROUGH the agent's home; it cannot list
# it, so .secrets, work/ and autodev/ stay closed.
setfacl -m "u:${OPERATOR}:--x" "$HOME_DIR"

# The queue, steer file and heartbeat: read and write.
setfacl -m "u:${OPERATOR}:rwx" "$STATE"
setfacl -d -m "u:${OPERATOR}:rw-" "$STATE"
find "$STATE" -maxdepth 1 -type f -exec setfacl -m "u:${OPERATOR}:rw-" {} +

# Briefs are written by the operator and read by the AGENT, so this one needs to
# work in both directions: setgid so a submission lands in the agent's group,
# and a default ACL so the group can read it. Without this the agent cannot open
# what the operator just submitted.
install -d -o "$AGENT" -g "$AGENT" -m 2770 "${STATE}/briefs"
setfacl -m "u:${OPERATOR}:rwx" "${STATE}/briefs"
setfacl -d -m "u:${OPERATOR}:rw-" "${STATE}/briefs"
setfacl -d -m "g::rw-" "${STATE}/briefs"

# The ladder says which repo the loop is on. `autodev target` writes it.
setfacl -m "u:${OPERATOR}:--x" "${HOME_DIR}/autodev"
setfacl -m "u:${OPERATOR}:r-x" "${HOME_DIR}/autodev/config"
setfacl -m "u:${OPERATOR}:rw-" "${HOME_DIR}/autodev/config/ladder.json"
# Read-only: the operator runs the tools, and MUST NOT be able to rewrite the
# harness the agent executes.
setfacl -m "u:${OPERATOR}:r-x" "${HOME_DIR}/autodev/scripts"

# Event sentinels, so `submit` can wake the loop rather than leaving the work
# until the hourly net. WRITE, not read: this was r-x once, and the wake failed
# silently while `submit` reported success -- twenty minutes of nothing
# happening with two goals sitting open. Same setgid arrangement as briefs,
# because the AGENT has to be able to consume what the operator drops.
install -d -o "$AGENT" -g "$AGENT" -m 2770 "${HOME_DIR}/events"
setfacl -m "u:${OPERATOR}:rwx" "${HOME_DIR}/events"
setfacl -d -m "u:${OPERATOR}:rw-" "${HOME_DIR}/events"
setfacl -d -m "g::rw-" "${HOME_DIR}/events"

# THE COMMAND ITSELF. Without this `autodev` is a path the operator has to
# remember, in a directory they cannot list, needing three environment variables
# to work -- which is not a CLI, it is a recipe. A symlink is enough because the
# script resolves its own root through `Path(__file__).resolve()`, which follows
# it.
ln -sfn "${HOME_DIR}/autodev/scripts/autodev" /usr/local/bin/autodev

# ...and the config it reads to find everything else. This file holds addresses
# and names; secrets are vault ENTRY NAMES here, never values, so the operator
# reading it discloses nothing they do not already have. Without it the CLI
# falls back to the documentation-safe defaults and writes to directories that
# do not exist on this host.
setfacl -m "u:${OPERATOR}:r--" "${HOME_DIR}/autodev/config/fleet.json" 2>/dev/null || true
setfacl -m "u:${OPERATOR}:r--" "${HOME_DIR}/autodev/config/fleet.example.json"

# `autodev watch` reads the tick journal. Read-only, and it grants nothing the
# operator could not already get through sudo. Group membership applies at the
# operator's NEXT LOGIN -- `sg systemd-journal -c ...` works in the meantime.
usermod -aG systemd-journal "$OPERATOR"

# start/stop, for those units only. See the rule for its scope.
install -m 0644 -o root -g root \
    "$(dirname "$0")/../polkit/49-autodev-operator.rules" \
    /etc/polkit-1/rules.d/49-autodev-operator.rules
systemctl reload polkit 2>/dev/null || systemctl restart polkit

echo "operator access granted to ${OPERATOR}:"
echo "  state + briefs + ladder: read/write"
echo "  harness scripts:         read only"
echo "  vault, worktrees:        UNCHANGED (still closed)"
echo "  units:                   start/stop/restart, autodev only"
echo "  journal:                 read (next login)"
echo "  command:                 /usr/local/bin/autodev"
