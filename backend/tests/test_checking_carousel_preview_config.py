"""Checking preview must opt in without granting any production preview path."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.carousel_preview import checking_preview_database


CHECKING_CONFIG = {
    "database_url": "postgresql+psycopg://me_nu_checking:synthetic@postgres:5432/me_nu_checking",
    "menu_deployment_env": "checking",
    "checking_carousel_test_preview_enabled": True,
    "oauth_frontend_origin": "https://checking.me-nu.kr",
    "oauth_public_api_origin": "https://api.checking.me-nu.kr",
    "frontend_origins": "https://checking.me-nu.kr,https://api.checking.me-nu.kr",
    "enable_dev_api": False,
    "enable_dev_auth_bypass": False,
    "session_cookie_secure": True,
    "event_data_source": "automated_test",
}


def configure_checking(monkeypatch, config):
    for name, value in CHECKING_CONFIG.items():
        monkeypatch.setattr(config, name, value)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(name, raising=False)


def test_preview_is_off_by_default_and_explicit_checking_is_valid():
    assert not Settings(_env_file=None, database_url="postgresql://unused").checking_carousel_test_preview_enabled
    config = Settings(_env_file=None, **CHECKING_CONFIG)
    assert checking_preview_database(config) == "me_nu_checking"


@pytest.mark.parametrize("override", [
    {"menu_deployment_env": "production"}, {"menu_deployment_env": "labs"},
    {"event_data_source": "production"}, {"enable_dev_api": True}, {"enable_dev_auth_bypass": True},
    {"session_cookie_secure": False}, {"oauth_frontend_origin": "https://me-nu.kr"},
    {"oauth_public_api_origin": "https://api.labs.me-nu.kr"},
    {"frontend_origins": "https://checking.me-nu.kr,https://api.checking.me-nu.kr,https://me-nu.kr"},
    {"database_url": "postgresql://me_nu_checking:p@db.example.com:5432/me_nu_checking"},
    {"database_url": "postgresql://me_nu_checking:p@postgres:5432/me_nu"},
    {"database_url": "postgresql://me_nu:p@postgres:5432/me_nu_checking"},
    {"database_url": "postgresql://me_nu_checking:p@postgres:5433/me_nu_checking"},
    {"database_url": "postgresql://me_nu_checking:p@postgres:5432/me_nu_checking?hostaddr=10.0.0.1"},
])
def test_invalid_checking_preview_configuration_fails_closed(override):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **(CHECKING_CONFIG | override))
    assert "synthetic" not in str(error.value)


def test_importer_target_override_cannot_reuse_checking_permission():
    config = Settings(_env_file=None, **CHECKING_CONFIG)
    assert checking_preview_database(config, "postgresql://me_nu_checking:p@other:5432/me_nu_checking") is None
    config.checking_carousel_test_preview_enabled = False
    assert checking_preview_database(config) is None
