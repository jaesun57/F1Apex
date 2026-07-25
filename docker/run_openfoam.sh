#!/usr/bin/env bash
# Run an OpenFOAM command inside the pinned arm64 container.
#
#   docker/run_openfoam.sh <case_dir> <command...>
#
# The platform pin is deliberate and load-bearing: without it Docker may
# silently select the amd64 manifest and run everything under emulation at
# roughly an order of magnitude slower, which is the difference between this
# project being feasible and not. Verify with:
#   docker/run_openfoam.sh <case> bash -lc 'echo $WM_OPTIONS'
# and confirm it reports linuxARM64...
set -euo pipefail

IMAGE="${OPENFOAM_IMAGE:-microfluidica/openfoam:2506}"
PLATFORM="${DOCKER_PLATFORM:-linux/arm64}"
MEMORY="${OPENFOAM_MEMORY:-}"          # e.g. 14g; empty = no explicit cap

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <case_dir> <command...>" >&2
    exit 2
fi

CASE_DIR="$(cd "$1" && pwd)"   # absolute; never a bare relative path
shift

# Run as the invoking user so files written into the bind mount stay owned by
# the host user rather than root.
# The image entrypoint does `export HOME=/home/openfoam; mkdir -p $HOME; cd`
# whenever it runs as non-root. That mkdir fails because / is not writable by
# the mapped user, so the container dies before reaching the command. Mounting a
# writable tmpfs there makes the mkdir a no-op and lets the entrypoint proceed.
declare -a docker_args=(
    run --rm
    --platform "$PLATFORM"
    --user "$(id -u):$(id -g)"
    --tmpfs /home/openfoam:rw,mode=1777
    -v "${CASE_DIR}:/case"
    -w /case
)
[[ -n "$MEMORY" ]] && docker_args+=(--memory "$MEMORY")

# Two non-obvious requirements, both discovered the hard way:
#
#   bash -l : the login shell sources OpenFOAM's profile, which sets
#             LD_LIBRARY_PATH. Without it every solver dies with
#             "libfileFormats.so: cannot open shared object file".
#
#   cd /case: the entrypoint runs a bare `cd` (to $HOME) whenever it is
#             non-root, which silently overrides Docker's own -w. Without this
#             the working directory is the tmpfs, so all relative output --
#             every log file and the entire postProcessing tree -- is written
#             there and lost when the container exits.
# Newline-separated rather than `cd /case && { ...; }` -- the brace form breaks
# on multi-line commands, because the closing `; }` follows a newline and parses
# as an empty command.
exec docker "${docker_args[@]}" "$IMAGE" bash -lc "cd /case || exit 1
$*"
