from typing import Literal

from pydantic import ConfigDict

from app.schemas.common import CamelModel


class OAuthLoginRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["signin", "reauth", "restore"] = "signin"


class OAuthLoginResponse(CamelModel):
    authorization_url: str
