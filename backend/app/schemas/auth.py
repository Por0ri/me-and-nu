from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

Nickname = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=50),
]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    nickname: Nickname
    role: Literal["consumer", "creator"] = "consumer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str | None
    nickname: str
    role: str


class LoginResponse(BaseModel):
    message: str
    user: UserResponse
