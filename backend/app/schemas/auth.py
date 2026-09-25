from typing import Annotated, Literal

from pydantic import EmailStr, Field, StringConstraints

from app.schemas.common import CamelModel

Nickname = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=50),
]


class RegisterRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    nickname: Nickname
    role: Literal["consumer", "creator"] = "consumer"


class LoginRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserResponse(CamelModel):
    id: int
    email: str | None
    nickname: str
    account_type: str


class LoginResponse(CamelModel):
    message: str
    user: UserResponse


class SessionUser(CamelModel):
    id: int
    account_type: str


class SessionResponse(CamelModel):
    authenticated: bool
    session_state: Literal["anonymous", "onboarding_pending", "active"]
    user: SessionUser | None
    onboarding_completed: bool
    csrf_token: str
