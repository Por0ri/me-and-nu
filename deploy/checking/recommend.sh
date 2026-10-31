#!/usr/bin/env bash
# 추천 계산 (model/scripts/daily_recommend.py)을 Checking 서버에서 API 와 따로 돌린다. Checking 전용
# - 이미지: model/Dockerfile (모델 코드만). 통계 문맥 파일은 이미지에 안 넣고 읽기 전용으로 붙인다 (서버에 직접 올림, 공개 레포 X)
#   파일은 컨테이너 사용자 (uid 10001)가 읽을 수 있게 chmod 644
# - DB: Checking 내부 네트워크의 같은 DB. 접속 정보는 실행 중인 Checking API 컨테이너에서 받고 화면 · 명령 인자에 남기지 않는다
# - 메모리 한도 1.5GB (로컬 측정: 최대 약 0.9GB, 예약 약 1.15GB). 서버를 다시 켜도 다시 뜬다 (unless-stopped)
#
#   sudo bash deploy/checking/recommend.sh build     # 이미지 만들기 (이 폴더의 커밋 기준)
#   sudo bash deploy/checking/recommend.sh preview   # 한 번 미리보기 (DB 에 안 씀)
#   sudo bash deploy/checking/recommend.sh up        # 켜 두기: 1초마다 새 가입자, 매일 06:00 (한국)부터 그날 전체 → 추천 표에 씀
#   sudo bash deploy/checking/recommend.sh status | logs | stop
#
# 문맥 파일 위치는 RECOMMEND_CONTEXT 로 바꿀 수 있음 (기본 /home/ubuntu/me-nu-model/service_context.pkl.gz)
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
if (( $# > 1 )); then
  printf '%s\n' 'Pass exactly one recommend action.' >&2
  exit 2
fi
docker=(docker --context default)
image=me-nu-recommend-checking
name=me-nu-checking-recommend
network=me-nu-checking_data
context=${RECOMMEND_CONTEXT:-/home/ubuntu/me-nu-model/service_context.pkl.gz}
args=(--expected-database me_nu_checking --context /data/service_context.pkl.gz)

api_env() {  # 실행 중인 Checking API 컨테이너의 환경 값 하나 (화면에 안 찍음)
  local api
  api=$("${docker[@]}" ps -q --filter label=com.docker.compose.project=me-nu-checking --filter label=com.docker.compose.service=api)
  if [[ -z $api || $(wc -l <<<"$api") -ne 1 ]]; then
    printf '%s\n' 'Exactly one running Checking API container is required.' >&2
    exit 2
  fi
  "${docker[@]}" inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$api" | sed -n "s/^$1=//p"
}

start() {  # 인자: docker run 옵션 · 이미지 · daily_recommend 인자
  if [[ ! -r $context ]]; then
    printf '%s\n' "Context file is missing or unreadable: $context" >&2
    exit 2
  fi
  DATABASE_URL=$(api_env DATABASE_URL)
  if [[ -z $DATABASE_URL ]]; then
    printf '%s\n' 'Could not read DATABASE_URL from the Checking API container.' >&2
    exit 2
  fi
  export DATABASE_URL
  # API 가 검토 전 미리보기 캐러셀을 보여 주면 추천도 같이 본다 (설정을 바꾸면 recommend.sh up 다시)
  local preview=()
  local enabled
  enabled=$(api_env CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED)
  [[ ${enabled,,} == true ]] && preview=(--preview checking)
  printf '%s\n' "Preview carousels included: $([[ ${#preview[@]} -gt 0 ]] && echo yes || echo no)"
  "${docker[@]}" run --network "$network" --memory 1536m --memory-swap 1536m --cpus 0.5 --pids-limit 64 \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL --security-opt no-new-privileges:true \
    --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
    -v "$context:/data/service_context.pkl.gz:ro" -e DATABASE_URL "$@" ${preview[@]+"${preview[@]}"}
}

case "${1:-status}" in
  build) "${docker[@]}" build -f model/Dockerfile -t "$image:$(git rev-parse --short=12 HEAD)" -t "$image:latest" model ;;
  preview) start --rm "$image:latest" "${args[@]}" --show 3 ;;
  up)
    # 새 가입자는 1초마다 확인: 앱이 온보딩 끝나고 약 1초 만에 피드를 열어서, 그 전에 추천이 있어야 첫 피드부터 추천 순서 (확인 한 번 약 0.06초)
    "${docker[@]}" rm -f "$name" >/dev/null 2>&1 || true
    start --detach --name "$name" --restart unless-stopped "$image:latest" "${args[@]}" --apply --every 1 --daily-at 06:00 ;;
  status) "${docker[@]}" ps -a --filter "name=^${name}$" ;;
  logs) "${docker[@]}" logs --tail 50 "$name" ;;
  stop) "${docker[@]}" rm -f "$name" ;;
  *) printf '%s\n' 'Usage: bash deploy/checking/recommend.sh {build|preview|up|status|logs|stop}' >&2; exit 2 ;;
esac
