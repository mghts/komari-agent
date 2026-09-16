#!/usr/bin/env bash
set -euo pipefail
image=${1:?image required}
version=${2:?version required}
docker run --network none "$image" --version | grep -F -- "$version"
name="komari-agent-smoke-${RANDOM}-${RANDOM}"
docker run -d --name "$name" --network none \
  -e AGENT_ENDPOINT=http://127.0.0.1:9 -e AGENT_TOKEN=smoke-only \
  -e AGENT_DISABLE_WEB_SSH=true -e AGENT_DISABLE_AUTO_UPDATE=false \
  "$image" >/dev/null
trap 'docker stop "$name" >/dev/null' EXIT
sleep 3
docker logs "$name" 2>&1 | grep -F 'update the Docker image instead of the binary'
test "$(docker inspect --format '{{.State.Running}}' "$name")" = true
