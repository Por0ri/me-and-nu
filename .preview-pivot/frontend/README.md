# me;nu 공개 콘텐츠 브라우저

FastAPI·PostgreSQL V1 API의 공개 피드와 콘텐츠 상세를 브라우저에서 볼 수 있습니다. 개발용 API 점검 화면도 홈 하단에 유지합니다.

## Consumer 온보딩 실제 API 연결

최신 `main`의 온보딩 화면을 유지하고, `NEXT_PUBLIC_CONSUMER_DATA_MODE=api`이면 실제 FastAPI·PostgreSQL을 사용합니다.

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_CONSUMER_DATA_MODE=api
NEXT_PUBLIC_ENABLE_LOCAL_DEMO_LOGIN=true
```

`frontend/.env.local`에 설정하고 Next.js를 재시작하세요. 백엔드는 `ENABLE_DEV_API=true`, `ENABLE_DEV_AUTH_BYPASS=false`로 실행합니다.

1. `http://localhost:3000/consumer/login`을 엽니다. 기존 계정의 온보딩 완료 여부와 관계없이 시연용 로그인 화면을 볼 수 있습니다.
2. **카카오로 시연 시작** 또는 **네이버로 시연 시작**을 누르면 새 로컬 시연 계정으로 로그인하고 온보딩으로 이동합니다. 실제 SNS 인증이나 SNS 개인정보 조회는 수행하지 않습니다. 서버의 현재 약관에 동의한 뒤 소비자 유형·분야·세부 관심사를 선택합니다.
3. API 모드는 실제 카탈로그의 세부 관심사 **1개 이상**을 선택하면 완료할 수 있습니다. Mock 화면의 최소 5개 선택 규칙은 유지합니다.
4. 완료하면 선택한 정책 버전·동의·Topic·Subtopic이 서버에 저장되고 `/consumer` 홈으로 이동합니다.
5. 새로고침 후에도 서버 세션·관심 분야가 복구됩니다. 홈·상세·O/X·좋아요·북마크·저장 목록도 실제 API를 사용합니다. API 오류를 Mock 데이터로 대체하지 않습니다.
6. 홈 우측 상단 분야 전환 메뉴의 **처음부터 시연하기**를 누르면 로그인 화면으로 돌아갑니다. 다음 시연 시작 버튼에서 새 계정을 생성하며 기존 계정 데이터를 초기화하지 않습니다.

시연 로그인은 `NEXT_PUBLIC_ENABLE_LOCAL_DEMO_LOGIN=true`이고 프론트·API 주소가 모두 루프백일 때만 사용합니다. 기존 개발용 가입·로그인 API를 사용하며, 이메일·비밀번호 입력이 필요 없습니다. 생성한 자격은 메모리에만 보관하며 페이지 새로고침 후 같은 시연 계정의 재로그인은 제공하지 않습니다. 같은 브라우저의 localhost 탭은 세션 쿠키를 공유하므로 새 시연을 시작하면 다른 탭도 새 계정의 세션을 사용합니다. 시연 플래그를 끄면 기존 `/#api-test`의 수동 개발용 로그인으로 API를 확인할 수 있습니다.

**임시 프로필:** 추가 입력 없이 닉네임 `로컬사용자{임의 문자열}`, 생년월일 `2000-01-01`을 생성합니다. 같은 화면에서 제출을 재시도하면 생성된 값을 재사용합니다. 온보딩 전 세션은 `user: null`이므로 사용자 ID에 의존하지 않습니다. 프론트와 API 주소가 모두 루프백(`localhost`, `127.0.0.1`, `[::1]`)일 때만 허용합니다. 구현 위치는 `lib/consumer-api/onboarding.ts`의 `createLocalOnboardingProfile`입니다. 이 값은 실제 SNS 개인정보가 아니며, 향후 소셜 로그인에서 확인된 정보로 교체해야 합니다.

실제 소셜 로그인 연결은 후속 작업입니다. 로컬 시연이 활성화된 API 모드에서 비로그인 상태로 `/consumer`에 진입하면 로그인 화면으로 이동합니다. 자연어 관심사 해석·AI 대화는 아직 연결되지 않았습니다.

**API 시연 데이터 준비:** 기본 정책·데모 사용자 seed에 더해, `backend`에서 다음 명령으로 Agent 콘텐츠와 온보딩 관심사를 적재해야 합니다. 기본 seed만 있는 DB에는 현재 화면의 7개 선택지가 모두 준비되어 있지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m app.seeds.agent_contents --git-ref f645df0 --apply
.\.venv\Scripts\python.exe -m app.seeds.agent_interests --git-ref f645df0 --apply
```

영화·음악·애니메이션별로 현재 콘텐츠에 근거한 임시 관심사 7개를 표시하며, 실제 API에서 조회한 DB ID로 저장합니다. 온보딩과 분야 추가 화면에서 동일한 목록을 사용합니다. [임시 분류 목록·근거·팀 협의 사항](../docs/PROVISIONAL_ONBOARDING_INTERESTS.md)을 참고하세요. PostgreSQL 데이터 자체는 Git 커밋에 포함되지 않으므로 다른 환경에서는 위 seed를 실행해야 합니다.

콘텐츠 `imageUrl`이 `/local-content/...`이면 `frontend/public/local-content`의 로컬 이미지를 표시합니다. 포함된 이미지 파일만으로 DB의 콘텐츠 이미지 경로를 자동 변경하지는 않습니다.

## Consumer Mock

- 시작 URL: `http://localhost:3000/consumer/login`. Mock 로그인과 온보딩을 완료하면 `/consumer` 홈으로 이동합니다.
- `/consumer/contents/[contentId]`, `/consumer/saved`, `/consumer/topics/new`에서 상세·저장 목록·분야 추가를 확인합니다.
- 실제 FastAPI integration이 아닌 presentation Mock입니다. 기존 `/`의 실제 피드·API 점검 화면과 숫자 ID 기반 상세 화면은 유지합니다.
- Mock 로그인/Session 상태와 Topic flow의 `initialHomeTopicId`·`availableTopicIds`는 `sessionStorage`에 보관하여 같은 탭의 F5에서 복구합니다. 실제 인증이나 Backend persistence를 의미하지 않습니다.
- Home의 Card/List View preference는 기존대로 `localStorage`에 보관합니다.
- Bookmark·Like·O/X·AI intent 등 기타 Mock business state는 여전히 메모리 기반이며 F5 시 초기화될 수 있습니다.
- 원본 baseline: `e8bbf2d8d27f1c01c869ea4eb2053efcae6f24d2`. Home Final `642fbf4`를 적용했으며 Movie 콘텐츠와 thumbnail 각 5개, Topic popover, Card 공유·저장·좋아요를 포함합니다.

## 포함된 테스트

- 홈·Topic·Subtopic별 공개 콘텐츠 피드와 상세 화면
- AI 생성, 검토 필요, 개발용 미리보기 표시
- 내부 Next.js Route Handler `/api/health`
- 외부 FastAPI `/health` 선택 호출
- 개발용 가입·로그인 → 세션 → 정책·Topic/Subtopic → 온보딩 → 프로필·구독 → 피드·상세 → O/X·좋아요·북마크 → 로그아웃
- `credentials: "include"` 기반 HttpOnly 세션 쿠키와 메모리의 CSRF 토큰
- 동적 경로 `/deployments/[id]`
- 404 및 런타임 오류 화면
- Tailwind CSS 4 적용

## 로컬 실행

Node.js 20.9 이상을 권장합니다.

```bash
npm install
cp .env.example .env.local
npm run dev
```

브라우저에서 `http://localhost:3000`을 엽니다.

## 공개 콘텐츠 확인

- `http://localhost:3000/`: 내 첫 활성 Topic의 홈 피드. 관심사가 여러 개면 Topic을 선택할 수 있습니다.
- `http://localhost:3000/topics/{topicId}`: 해당 Topic 전체 피드.
- `http://localhost:3000/topics/{topicId}/subtopics/{subtopicId}`: 세부 토픽 피드. 영화 Agent 글은 **영화 리뷰** Subtopic에서 확인합니다.
- 피드 카드를 선택하면 `/contents/{contentId}?topicId={topicId}`로 이동해 AI 글의 본문이나 외부 글의 발췌와 출처 링크를 볼 수 있습니다.

피드와 상세는 현재 로그인 사용자의 활성 Topic을 사용합니다. 로컬 인증 우회가 켜져 있으면 준비된 개발용 사용자로 바로 조회할 수 있습니다. 새 Agent 글을 발행한 뒤 화면의 **새로고침**을 누르세요. 검토가 필요한 미리보기 글에는 배지가 표시됩니다.

배포 전 검증:

```bash
npm run typecheck
npm run build
```

## FastAPI 로컬 연동

백엔드와 프론트엔드를 각각 실행합니다.

```powershell
# 터미널 1: me_nu/backend (Windows / psycopg)
.\.venv\Scripts\python.exe scripts/run_local_api.py
```

```powershell
# 터미널 2: me_nu/frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

브라우저에서 `http://localhost:3000`을 열고 다음 순서로 확인합니다.

1. `API 호출하기`를 눌러 FastAPI `/health`가 성공하는지 확인합니다.
2. V1 로컬 통합 테스트에서 개발용 회원가입 후 같은 계정으로 로그인합니다. 첫 변경 요청 전 익명 세션과 CSRF 토큰은 자동 조회합니다.
3. 세션, 정책, Topic·Subtopic을 조회하고 실제 ID와 정책 버전으로 온보딩합니다.
4. 프로필·내 Topic·구독, 피드·콘텐츠 상세를 조회합니다.
5. 콘텐츠에 O/X, 좋아요, 북마크를 설정·취소한 뒤 피드와 상세 상태를 확인합니다.
6. 로그아웃 후 보호 API가 `HTTP 401`인지 확인합니다.

브라우저의 HttpOnly 쿠키가 정상 전달되려면 두 주소 모두 `localhost`를
사용해야 합니다. `localhost`와 `127.0.0.1`을 섞지 마세요. 백엔드 실행 전
`alembic upgrade head`와 `python -m app.seeds.v1_local`도 실행합니다.
Swagger 수동 절차는 [가이드](../docs/SWAGGER_TEST_GUIDE.md)에 있습니다.

## Vercel 연결

1. 이 폴더를 GitHub 저장소에 Push합니다.
2. Vercel에서 **New Project → Import Git Repository**를 선택합니다.
3. Framework Preset이 `Next.js`인지 확인합니다.
4. 저장소 전체가 아니라 하위 폴더로 올렸다면 Root Directory를 이 프로젝트 폴더로 지정합니다.
5. 첫 배포를 실행합니다.

## 환경변수 설정

Vercel의 Project Settings → Environment Variables에 다음 값을 등록합니다.

| 변수 | Preview 예시 | Production 예시 |
| --- | --- | --- |
| `NEXT_PUBLIC_DEPLOY_ENV` | `preview` | `production` |
| `NEXT_PUBLIC_API_BASE_URL` | 테스트 FastAPI 주소 | 운영 FastAPI 주소 |

로컬 인증 연동에서는 다음 값을 사용합니다.

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

`NEXT_PUBLIC_API_BASE_URL`을 비워 두면 Health Check는 내부 `/api/health`를
호출할 수 있지만, V1 테스트는 기본값인 `http://localhost:8000`의
FastAPI를 호출합니다.

외부 FastAPI를 연결할 때는 주소에 `/health`를 제외한 Base URL을 입력합니다.

```env
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
```

FastAPI 예시:

```python
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://YOUR-PROJECT.vercel.app"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "fastapi",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
```

Preview URL은 배포마다 달라질 수 있습니다. 인증을 포함한 실제 연동 단계에서는 허용 Origin을 무작정 `*`로 두지 말고, 고정 Preview 도메인 또는 서버 측 프록시 방식을 검토하세요.

## 권장 Git 테스트 순서

```bash
git checkout -b feature/vercel-test
```

1. `app/page.tsx`의 문구 하나를 수정하고 Push합니다.
2. Vercel이 만든 Preview URL에서 변경 내용을 확인합니다.
3. GitHub PR의 Vercel 배포 상태를 확인합니다.
4. 동적 페이지로 이동한 후 새로고침합니다.
5. API 호출 버튼을 눌러 응답을 확인합니다.
6. PR을 `main`에 병합하고 Production URL이 갱신되는지 확인합니다.
7. Vercel Deployments에서 직전 정상 배포로 Rollback을 시험합니다.

## 실패 배포 테스트

기능 브랜치에서만 아래처럼 타입 오류를 임시로 넣고 Push해 봅니다.

```ts
const buildFailure: number = "failure";
```

Preview 빌드가 실패하고 기존 Production이 유지되면 정상입니다. 확인 후 해당 코드는 즉시 제거합니다.

## 합격 기준

- 기능 브랜치 Push 시 Preview URL 생성
- PR에 배포 상태 표시
- `main` 병합 시 Production 자동 배포
- Preview와 Production의 환경값 분리
- 동적 경로 직접 접근 및 새로고침 성공
- 내부 또는 외부 `/health` 호출 성공
- 실패한 Preview가 Production에 영향 없음
- 이전 Production 배포로 Rollback 성공
