# main 온보딩 API 통합 검토

작성일: 2026-09-29. **커밋·push하지 않은 작업 폴더 변경 사항**입니다.

## 반영 범위

- stash `8d93eeed127c17235737b792db0de158cbb86657`에서 필요한 API 어댑터·북마크 조회·로컬 이미지 파일을 선택 복원했습니다. stash는 보존합니다.
- 현재 main의 온보딩 `ob-*` 화면, CSS, 폰트, 로그인 화면 디자인을 유지합니다. 로그인에는 로컬 시연 안내와 시작 동작을 연결했습니다.
- `NEXT_PUBLIC_CONSUMER_DATA_MODE=api`에서 서버 세션, 현재 정책 본문·버전, 실제 Topic/Subtopic을 사용합니다. 기본 `mock` 모드의 기존 화면과 탭별 세션 복원은 유지합니다.
- 추가 입력 없이 로컬 닉네임과 생년월일을 생성하여 온보딩을 저장합니다. 완료 후 홈·콘텐츠 상세·반응·저장 목록까지 실제 API로 연결됩니다.
- 저장 목록 API-041(`GET /api/v1/users/me/bookmarks`)을 복원하고 상세 응답에 nullable `imageUrl`을 추가했습니다. DB 마이그레이션은 없습니다.

## 임시 처리와 후속 결정

| 항목 | 현재 동작 | 이후 결정 |
| --- | --- | --- |
| 로그인 | `/consumer/login`에서 새 로컬 시연 계정 생성·로그인, 실제 SNS 인증 아님 | 실제 OAuth 연결 |
| 닉네임 | `로컬사용자` + 임의 문자열, 화면 내 재시도 시 재사용 | SNS에서 확인한 값 또는 별도 프로필 절차 |
| 생년월일 | 테스트값 `2000-01-01` | SNS가 실제 제공하는 정보·동의 범위 확인 후 교체 |
| 생성 허용 환경 | 프론트와 API 주소 모두 localhost/루프백일 때 | 운영에서 테스트값 자동 제출 금지 |
| 세부 관심사 최소 수 | 실제 API 1개, Mock 5개 | 제품 규칙과 카탈로그 데이터에 맞춰 확정 |
| 카탈로그 | 현재 DB에는 영화와 세부 관심사 2개 | 다른 분야 데이터 준비 |
| AI 질문·자연어 관심사 해석 | API 연결 미완료, Mock 응답으로 대체하지 않음 | 후속 API 연결 |

## 검토할 코드

- `frontend/lib/consumer-api/onboarding.ts`: 임시 프로필 생성, 현재 정책 버전과 숫자 ID로 제출
- `frontend/lib/consumer-api/local-demo.ts`, `auth.ts`: 로컬 시연 허용 조건, 가입·로그인과 부분 실패 재시도
- `frontend/components/login/login-screen.tsx`: 기존 로그인 디자인의 시연 버튼·안내, 세션 갱신 후 온보딩 이동
- `frontend/components/onboarding/onboarding-screen.tsx`: 입력 UI 추가 없이 제출, 저장 성공 후 세션 갱신 재시도
- `frontend/components/providers/consumer-session-provider.tsx`: 인증 상태 복원과 화면 이동
- `frontend/components/providers/consumer-flow-provider.tsx`: 실제 관심 분야 복원, 실패·이전 요청 처리
- `frontend/components/topics/add-topic-screen.tsx`: 분야 추가와 저장 후 조회 재시도
- `frontend/lib/consumer-api/{home,contents}.ts`: 피드·상세·반응·북마크 연결
- `backend/app/api/v1/users/me/bookmarks.py`: API-041 진입점

## 확인한 테스트

- TypeScript 검사 및 Next.js 빌드 성공
- 백엔드 관련 테스트 29개 통과: 인증 15개 + 북마크·콘텐츠 상세 14개
- 실제 브라우저: 개발용 새 계정 → 온보딩 → 필수 약관 2개만 동의 → 소비자 → 영화 → 영화 리뷰 1개 → 완료 → 홈
- DB 확인: 임시 닉네임, `2000-01-01`, 온보딩 완료 상태 저장. terms/privacy=true, advertising/marketing=false, 정책 버전 v1
- 홈 새로고침 후 서버 세션·분야 복구, 공개 콘텐츠 2개와 로컬 이미지 2개 로드 확인
- Agent 글 본문 조회, O 반응·좋아요 설정, 북마크 저장 후 저장 목록에서 조회 확인
- 코드 검토 중 발견한 온보딩 전 `user:null` 의존 오류와 활성 분야가 0개일 때의 복구 경로 수정
- 로컬 시연 로그인: 카카오 시연 버튼 → 새 계정 온보딩 → 홈 → 처음부터 시연하기 → 네이버 시연 버튼 → 새 계정의 약관 화면 재진입 확인
- 로컬 시연 어댑터: 환경 제한, SSR, 중복 클릭, 가입 응답 유실·로그인 실패 시 동일 계정 재시도, 완료 세션 거부 검증

추가 분야 저장은 현재 DB에 다른 분야가 없어 실제 브라우저로 완료하지 않았습니다. 저장 성공 직후 네트워크 장애 재시도와 사용자 전환 경합은 코드 검토로 확인했으며, 브라우저 장애 주입 테스트는 수행하지 않았습니다.

## 직접 확인하는 순서

1. `frontend/.env.local`: `NEXT_PUBLIC_CONSUMER_DATA_MODE=api`, `NEXT_PUBLIC_ENABLE_LOCAL_DEMO_LOGIN=true`, API 주소 `http://localhost:8000`.
2. 백엔드는 개발용 인증 활성화(`ENABLE_DEV_API=true`), 인증 우회 비활성화(`ENABLE_DEV_AUTH_BYPASS=false`). Windows 실행: backend 폴더에서 `.\.venv\Scripts\python.exe scripts/run_local_api.py`.
3. 프론트는 frontend 폴더에서 `npm run dev`. 주소는 `http://localhost:3000`으로 통일합니다.
4. `/consumer/login`에서 카카오 또는 네이버 시연 시작 버튼을 누르면 새 로컬 계정으로 로그인하고 `/consumer/onboarding`으로 이동합니다. 로그인 화면을 여는 것만으로는 계정이 생성되거나 현재 세션이 바뀌지 않습니다.
5. 온보딩 완료 후 `/consumer`에서 실제 DB 콘텐츠를 확인합니다. 홈 분야 전환 메뉴의 **처음부터 시연하기**로 다시 시작할 수 있습니다. 매번 새 시연 시작 시 새 계정을 생성하고 기존 사용자 데이터는 유지합니다. 같은 브라우저의 다른 localhost 탭도 새 세션 쿠키를 공유합니다.

로컬 시연 로그인은 프론트·API 주소 모두 루프백이고 명시적 환경 플래그가 켜졌을 때만 표시·실행됩니다. 환경 플래그의 기본값은 false이며 인증 우회는 활성화하지 않습니다. 로그인 화면에는 시연용임을 표시합니다. `/consumer/onboarding` 직접 접근 시 완료 계정이 홈으로 이동하는 기존 보호는 유지됩니다.

실행 안내는 `frontend/README.md`의 Consumer 온보딩 실제 API 연결 절에도 반영했습니다. 커밋 전 변경 내용을 검토한 뒤 승인 범위를 결정하면 됩니다.
