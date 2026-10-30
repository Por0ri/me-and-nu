#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES
if (( $# > 1 )); then
  printf '%s\n' 'Pass exactly one named checking action; arbitrary Compose arguments are forbidden.' >&2
  exit 2
fi
if [[ "${1:-check}" == build ]]; then
  python3 deploy/checking/validate.py check-build
else
  python3 deploy/checking/validate.py check
fi
compose=(docker --context default compose --env-file deploy/checking/.env -p me-nu-checking -f compose.checking.yaml)
case "${1:-check}" in
  check) ;;
  build) "${compose[@]}" build api ;;
  up) "${compose[@]}" up -d --no-build --wait --wait-timeout 180 api ;;
  migrate) "${compose[@]}" run --rm --no-deps migrate ;;
  status) "${compose[@]}" ps -a ;;
  stop) "${compose[@]}" stop api ;;
  *) printf '%s\n' 'Usage: bash deploy/checking/checking.sh {check|build|up|migrate|status|stop}' >&2; exit 2 ;;
esac
