# Next.js × Vercel 배포 테스트

GitHub와 Vercel의 자동 배포, Preview/Production 분리, 환경변수, 동적 경로와 API 통신을 확인하는 작은 테스트 프로젝트입니다.

## 포함된 테스트

- Git 브랜치와 커밋 정보 표시
- Preview/Production 환경변수 구분
- 내부 Next.js Route Handler `/api/health`
- 외부 FastAPI `/health` 선택 호출
- FastAPI 회원가입·로그인·현재 사용자·로그아웃 호출
- `credentials: "include"` 기반 HttpOnly 세션 쿠키 연동
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

배포 전 검증:

```bash
npm run typecheck
npm run build
```

## FastAPI 로컬 연동

백엔드와 프론트엔드를 각각 실행합니다.

```powershell
# 터미널 1: me_nu/backend
python -m uvicorn app.main:app --reload --host localhost --port 8000
```

```powershell
# 터미널 2: me_nu/frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

브라우저에서 `http://localhost:3000`을 열고 다음 순서로 확인합니다.

1. `API 호출하기`를 눌러 FastAPI `/health`가 성공하는지 확인합니다.
2. 인증 통합 테스트에서 회원가입을 실행합니다.
3. 같은 계정으로 로그인합니다.
4. `내 정보`에서 로그인한 사용자가 반환되는지 확인합니다.
5. 로그아웃 후 `내 정보`가 `HTTP 401`인지 확인합니다.

브라우저의 HttpOnly 쿠키가 정상 전달되려면 두 주소 모두 `localhost`를
사용해야 합니다. `localhost`와 `127.0.0.1`을 섞지 마세요.

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
호출할 수 있지만, 인증 테스트는 기본값인 `http://localhost:8000`의
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
