#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES
if (( $# > 1 )); then
  printf '%s\n' 'Pass exactly one named labs action; arbitrary Compose arguments are forbidden.' >&2
  exit 2
fi
python3 deploy/labs/validate.py check
compose=(docker --context default compose --env-file deploy/labs/.env -p me-nu-labs -f compose.labs.yaml)
case "${1:-check}" in
  check) ;;
  build) "${compose[@]}" build api ;;
  up) "${compose[@]}" up -d --no-build --wait --wait-timeout 180 api ;;
  migrate) "${compose[@]}" run --rm --no-deps migrate ;;
  seed) "${compose[@]}" run --rm --no-deps seed ;;
  seed-apply) "${compose[@]}" run --rm --no-deps seed --apply ;;
  status) "${compose[@]}" ps -a ;;
  stop) "${compose[@]}" stop api ;;
  *) printf '%s\n' 'Usage: bash deploy/labs/labs.sh {check|build|up|migrate|seed|seed-apply|status|stop}' >&2; exit 2 ;;
esac
