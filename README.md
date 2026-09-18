# me_nu 로컬 통합 테스트

Next.js 프론트엔드와 FastAPI·PostgreSQL 백엔드를 로컬에서 연동하는
모노레포입니다. 인증은 HttpOnly `session_id` 쿠키를 사용합니다.

## 실행 전 준비

- PostgreSQL과 `fastapi_test_db` 실행
- `backend/.env`에 실제 `DATABASE_URL` 설정
- Node.js 20.9 이상
- Python 가상환경과 백엔드 의존성 설치

프론트엔드와 백엔드 모두 `localhost`를 사용합니다. `localhost`와
`127.0.0.1`을 섞으면 브라우저의 SameSite 쿠키 판정으로 인증 테스트가
실패할 수 있습니다.

## 백엔드 실행

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
python -m uvicorn app.main:app --reload --host localhost --port 8000
```

확인 주소:

- `http://localhost:8000/health`
- `http://localhost:8000/health/db`
- `http://localhost:8000/docs`

## 프론트엔드 실행

새 PowerShell 창에서 실행합니다.

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

브라우저에서 `http://localhost:3000`을 엽니다.

## 인증 연동 테스트 순서

1. Health Check의 `API 호출하기`가 성공해야 합니다.
2. 인증 통합 테스트에서 아직 사용하지 않은 이메일로 회원가입합니다.
3. 같은 이메일과 비밀번호로 로그인합니다.
4. `내 정보`가 로그인한 사용자를 반환해야 합니다.
5. 로그아웃합니다.
6. 다시 `내 정보`를 호출했을 때 `HTTP 401`이어야 합니다.

## 자동 검증

```powershell
cd backend
python -m pytest -v
```

```powershell
cd frontend
npm run typecheck
npm run build
```

성공 기준은 백엔드 테스트 실패 0건, TypeScript 오류 0건, Next.js 빌드
성공입니다.

## 로컬 CORS와 쿠키 설정

- 허용 Origin: `http://localhost:3000`, `http://127.0.0.1:3000`
- Credential 허용: 활성화
- 프론트엔드 요청: `credentials: "include"`
- 로컬 쿠키: `HttpOnly`, `SameSite=Lax`, `Secure=False`

운영 배포에서는 HTTPS를 적용하고 쿠키의 `Secure` 설정과 실제 프론트엔드
Origin을 환경변수로 지정해야 합니다.
