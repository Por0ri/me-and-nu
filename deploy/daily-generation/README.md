# 일일 생성 비활성 배포

`compose.inactive.yaml`은 이미지 반입과 비활성 컨테이너 준비용 **독립 구성**이다.
기존 `compose.runtime.yaml`·`compose.approved.yaml`과 합치지 않는다.

- Worker·Controller·importer 명령은 모두 `--help`다. 자동 수집·생성·적재를 실행하지 않는다.
- 네트워크는 `none`, 키·실제 DB/Redis 주소·호스트 파일 연결·공개 포트는 없다.
- importer의 `offline_not_used` 주소는 도움말의 Settings 검사에만 필요하며 실제 대상이 아니다.
- 이미지의 healthcheck를 끄고, 비 root 사용자·읽기 전용 파일시스템·`restart: no`를 사용한다.
- 최종 컨테이너는 `created` 또는 종료 상태로 둔다. 기존 API·추천·작업 DB·Redis·이력 파일을 바꾸지 않는다.

검증된 소스로 이미지를 빌드하고, `inactive.env.example`의 두 값만 정확한 로컬 이미지 ID
(`sha256:` + 소문자 16진수 64자리)로 채운 별도 파일을 준비한다. 예산·날짜·승인 해시나
자격증명을 이 파일에 넣지 않는다. Compose 자체는 태그를 거절하지 않으므로 배포 검사에서
두 값의 형식과 `docker image inspect` 결과를 대조해야 한다. 렌더링된 설정도
`test_inactive_deploy.validate_inactive_config(config, image_ids)`로 같은 계약을 검사할 수 있다.

아래 회귀는 합성 이미지 ID만 사용한다. Docker Compose CLI로 설정을 렌더링하며 daemon,
실제 이미지, 네트워크, DB, API 키는 사용하지 않는다.

```text
python -m unittest discover -s deploy/daily-generation -p test_inactive_deploy.py -v
```

도움말을 확인할 때도 이 독립 구성의 네트워크·mount 금지 조건을 유지한다. 컨테이너를
준비할 때는 `create`만 사용하고 자동 시작·재시작을 설정하지 않는다. 실제 활성화는 별도의
예산·실행 날짜·종료 시각·기존 이력·승인된 정책을 확인한 뒤 검토한다. 준비된 이미지가
있다는 사실만으로 유료 실행이나 서비스 적재가 승인된 것은 아니다.
