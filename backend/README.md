# FastAPI Backend Test

FastAPI, SQLAlchemy 2.0 async, PostgreSQL, Alembic으로 구성한 로컬 백엔드입니다.
사용자는 PostgreSQL에 저장하고 로그인 세션과 콘텐츠는 아직 메모리에 저장합니다.

## 현재 구현 범위

- 기능별 Router 분리 및 Pydantic 요청·응답 검증
- 상태 확인 및 PostgreSQL 연결 확인 API
- 콘텐츠 CRUD(Mock 메모리 저장소)
- PostgreSQL `users` 테이블과 비동기 Repository/Service
- 회원가입, 로그인, 현재 사용자 조회, 로그아웃
- Argon2 비밀번호 해시 저장
- Session ID + HttpOnly Cookie 인증(Mock 메모리 세션)
- Alembic 비동기 마이그레이션과 upgrade/downgrade 검증
- pytest API·서비스·모델·마이그레이션 테스트

아직 Redis, 콘텐츠 DB 전환, Docker, 외부 배포는 포함하지 않습니다.

## 프로젝트 위치와 환경변수

아래 명령은 모두 `backend` 디렉터리에서 실행합니다. `.env.example`을 복사하고
로컬 PostgreSQL 접속 정보로 바꿉니다. `.env`는 Git에 올리지 않습니다.

```powershell
Copy-Item .env.example .env
```

```dotenv
DATABASE_URL=postgresql+psycopg://DB_USER:DB_PASSWORD@localhost:5432/DB_NAME
```

## Windows PowerShell 실행

이미 정상 동작하는 `.venv`가 있다면 새로 만들지 않아도 됩니다.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
alembic upgrade head
fastapi dev app\main.py
```

확인 주소:

- Swagger UI: http://127.0.0.1:8000/docs
- 서버 상태: http://127.0.0.1:8000/health
- DB 상태: http://127.0.0.1:8000/health/db
- 콘텐츠: http://127.0.0.1:8000/api/v1/contents

## 인증 API

| 메서드 | 경로 | 성공 기준 |
|---|---|---|
| `POST` | `/api/v1/auth/register` | `201`, 사용자 생성, 비밀번호 해시 저장 |
| `POST` | `/api/v1/auth/login` | `200`, `session_id` HttpOnly 쿠키 발급 |
| `GET` | `/api/v1/auth/me` | 로그인 쿠키가 있으면 `200`, 없으면 `401` |
| `POST` | `/api/v1/auth/logout` | `200`, 세션과 쿠키 제거 |

회원가입 예시:

```powershell
$body = @{
  email = "test@example.com"
  password = "test1234"
  nickname = "테스터"
  role = "consumer"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/auth/register" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

로그인 후 같은 PowerShell 세션에서 `/me`를 확인하는 예시:

```powershell
$loginBody = @{
  email = "test@example.com"
  password = "test1234"
} | ConvertTo-Json

$webSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/auth/login" `
  -Method Post `
  -ContentType "application/json" `
  -Body $loginBody `
  -WebSession $webSession

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/auth/me" `
  -WebSession $webSession
```

## 자동 테스트

가상환경의 실행 파일 경로가 바뀐 경우 `pytest` 실행 파일 대신 모듈 방식이
안전합니다.

```powershell
python -m pytest -v
```

성공 기준은 모든 테스트가 `PASSED`이고 실패와 오류가 0건인 것입니다.

## 로컬 수동 검증 체크리스트

- [ ] `GET /health`가 `200`, `{"status":"ok"}`를 반환한다.
- [ ] `GET /health/db`가 `200`을 반환한다.
- [ ] `POST /api/v1/auth/register`가 `201`을 반환하고 `users` 테이블에 1행이 생긴다.
- [ ] 저장된 `hashed_password`가 입력한 평문 비밀번호와 다르다.
- [ ] 같은 이메일 재가입은 `409`를 반환하고 행이 추가되지 않는다.
- [ ] 올바른 `/api/v1/auth/login`은 `200`과 HttpOnly `session_id` 쿠키를 반환한다.
- [ ] 틀린 비밀번호 로그인은 `401`을 반환한다.
- [ ] 로그인 쿠키로 `/api/v1/auth/me`를 호출하면 해당 사용자가 반환된다.
- [ ] 로그아웃 후 `/api/v1/auth/me`는 `401`을 반환한다.
- [ ] 서버 재시작 뒤 사용자는 DB에 남고, 기존 메모리 세션은 만료된다.

## 데이터베이스 마이그레이션

마이그레이션 파일은 이미 `alembic\versions`에 포함되어 있습니다. 새로 초기
마이그레이션을 생성하지 말고 다음 명령으로 적용합니다.

```powershell
alembic upgrade head
alembic current
```

되돌리기와 재적용 검증:

```powershell
alembic downgrade -1
alembic upgrade head
alembic current
```

`alembic current`가 최신 리비전을 출력하면 성공입니다.

## 현재 제약

로그인 세션은 프로세스 메모리에 있으므로 서버를 재시작하면 모두 사라집니다.
사용자 레코드는 PostgreSQL에 유지됩니다. 다음 단계에서 세션 저장소를 Redis로
교체해도 Router와 쿠키 계약은 그대로 유지할 수 있습니다.
