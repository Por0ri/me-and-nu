import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(name, raising=False)


def settings_for(**overrides):
    return Settings(_env_file=None, database_url="postgresql://unused", **overrides)


def enabled_provider(provider="kakao", **overrides):
    return settings_for(**{
        f"{provider}_oauth_enabled": True,
        f"{provider}_client_id": "example-client-id",
        f"{provider}_client_secret": "example-client-secret",
        **overrides,
    })


def test_oauth_defaults_preserve_existing_local_startup():
    config = settings_for()
    assert config.kakao_oauth_enabled is config.naver_oauth_enabled is False
    assert config.kakao_client_id is config.naver_client_id is None
    assert config.oauth_state_ttl_seconds == 600
    assert config.session_expire_minutes == 30
    assert config.session_cookie_secure is False
    assert config.oauth_frontend_origin == ""


@pytest.mark.parametrize("provider", ["kakao", "naver"])
def test_each_provider_can_be_enabled_independently(provider):
    config = enabled_provider(provider)
    assert isinstance(getattr(config, f"{provider}_client_secret"), SecretStr)
    assert config.oauth_callback_url(provider) == f"http://localhost:8000/api/v1/auth/login/{provider}/callback"
    assert "example-client-id" not in repr(config)
    assert "example-client-secret" not in repr(config)
    assert config.oauth_frontend_origin == ""


def test_enabling_second_provider_also_requires_its_credentials():
    with pytest.raises(ValidationError) as error:
        enabled_provider(naver_oauth_enabled=True)
    assert "NAVER_CLIENT_ID" in str(error.value)
    assert "NAVER_CLIENT_SECRET" in str(error.value)
    config = enabled_provider(
        naver_oauth_enabled=True, naver_client_id="naver-id", naver_client_secret="naver-secret",
    )
    assert config.kakao_oauth_enabled is config.naver_oauth_enabled is True


@pytest.mark.parametrize("provider", ["kakao", "naver"])
@pytest.mark.parametrize("field", ["client_id", "client_secret"])
@pytest.mark.parametrize("missing", [None, "", "   "])
def test_enabled_provider_requires_nonblank_credentials(provider, field, missing):
    with pytest.raises(ValidationError) as error:
        enabled_provider(provider, **{f"{provider}_{field}": missing})
    assert f"{provider}_{field}".upper() in str(error.value)
    assert "example-client-id" not in str(error.value)
    assert "example-client-secret" not in str(error.value)


@pytest.mark.parametrize("seconds", [0, -1, 1801])
def test_oauth_state_expiry_is_bounded(seconds):
    with pytest.raises(ValidationError):
        settings_for(oauth_state_ttl_seconds=seconds)


@pytest.mark.parametrize("seconds", [1, 1800])
def test_oauth_state_expiry_accepts_valid_boundaries(seconds):
    assert settings_for(oauth_state_ttl_seconds=seconds).oauth_state_ttl_seconds == seconds


@pytest.mark.parametrize("setting", ["oauth_frontend_origin", "oauth_public_api_origin"])
@pytest.mark.parametrize("origin", [
    "http://example.com", "ftp://localhost", "https://example.com/path",
    "https://example.com?next=secret", "https://example.com#fragment",
    "https://example.com?", "https://example.com#",
    "https://secret-user:secret-password@example.com", "https://example.com:bad",
    "https://example.com:65536", "https://example.com:", "https://example.com:0",
    "https://", "//localhost:3000", "http://localhost.evil.example",
    "https://example.com\\path", "https://example.com\n", "https://example.com//",
])
def test_enabled_oauth_rejects_invalid_origins_without_echoing_values(setting, origin):
    with pytest.raises(ValidationError) as error:
        enabled_provider(**{setting: origin})
    message = str(error.value)
    assert setting.upper() in message
    assert "secret-user" not in message
    assert "secret-password" not in message
    assert "example-client-secret" not in message
    assert "example-client-id" not in message


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_loopback_origins_allow_http_and_normalize_trailing_slash(host):
    frontend = f"http://{host}:3000"
    api = f"http://{host}:8000"
    config = enabled_provider(
        oauth_frontend_origin=frontend + "/", oauth_public_api_origin=api + "/",
        frontend_origins=frontend,
    )
    assert config.oauth_frontend_origin == frontend
    assert config.oauth_public_api_origin == api
    assert config.oauth_callback_url("kakao").startswith(api + "/api/v1/")


def test_frontend_oauth_origin_must_be_in_cors_origins():
    with pytest.raises(ValidationError, match="OAUTH_FRONTEND_ORIGIN.*FRONTEND_ORIGINS"):
        enabled_provider(oauth_frontend_origin="http://localhost:3000", frontend_origins="http://localhost:3001")


def test_cors_origin_must_match_the_actual_browser_origin():
    with pytest.raises(ValidationError, match="OAUTH_FRONTEND_ORIGIN.*FRONTEND_ORIGINS"):
        enabled_provider(oauth_frontend_origin="http://localhost:3000", frontend_origins="http://localhost:3000/")


def test_backend_only_oauth_does_not_require_a_frontend_or_cors_origin():
    config = enabled_provider(frontend_origins="")
    assert config.oauth_frontend_origin == ""
    assert config.cors_origins == []
    assert config.oauth_callback_url("kakao") == "http://localhost:8000/api/v1/auth/login/kakao/callback"


def test_backend_only_remote_api_works_with_secure_cookie():
    config = enabled_provider(
        oauth_public_api_origin="https://api.example.com/",
        oauth_frontend_origin="",
        frontend_origins="",
        session_cookie_secure=True,
    )
    assert config.oauth_frontend_origin == ""
    assert config.oauth_callback_url("kakao") == "https://api.example.com/api/v1/auth/login/kakao/callback"


def test_backend_only_remote_api_still_requires_secure_cookie():
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE"):
        enabled_provider(
            oauth_public_api_origin="https://api.example.com", oauth_frontend_origin="", frontend_origins="",
        )


def test_backend_only_oauth_still_requires_a_valid_api_origin():
    with pytest.raises(ValidationError, match="OAUTH_PUBLIC_API_ORIGIN"):
        enabled_provider(oauth_public_api_origin="", frontend_origins="")


@pytest.mark.parametrize("remote_setting", ["oauth_frontend_origin", "oauth_public_api_origin"])
def test_either_nonloopback_origin_requires_secure_cookie(remote_setting):
    overrides = {remote_setting: "https://example.com"}
    if remote_setting == "oauth_frontend_origin":
        overrides["frontend_origins"] = "https://example.com"
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE"):
        enabled_provider(**overrides)


def test_remote_https_origins_work_with_secure_cookie_and_cors():
    config = enabled_provider(
        oauth_frontend_origin="https://app.example.com/",
        oauth_public_api_origin="https://api.example.com/",
        frontend_origins="http://localhost:3000, https://app.example.com",
        session_cookie_secure=True,
    )
    assert config.oauth_callback_url("naver") == "https://api.example.com/api/v1/auth/login/naver/callback"


def test_callback_url_does_not_accept_arbitrary_provider_paths():
    with pytest.raises(ValueError, match="Unsupported OAuth provider"):
        settings_for().oauth_callback_url("../outside")
