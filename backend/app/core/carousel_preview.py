"""One explicit shared-test boundary; never infer preview access from a DB name."""
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


CHECKING_DATABASE = "me_nu_checking"
CHECKING_FRONTEND = "https://checking.me-nu.kr"
CHECKING_API = "https://api.checking.me-nu.kr"


def checking_preview_database(config, database_url: str | None = None) -> str | None:
    """Allow only the dedicated Checking container and its configured origins.

    Rechecked on each import/read so toggling the flag off hides existing preview
    rows without rewriting their original QC evidence or treating them as approved.
    """
    if (not config.checking_carousel_test_preview_enabled
            or config.menu_deployment_env != "checking"
            or config.oauth_frontend_origin != CHECKING_FRONTEND
            or config.oauth_public_api_origin != CHECKING_API
            or set(config.cors_origins) != {CHECKING_FRONTEND, CHECKING_API}
            or config.enable_dev_api or config.enable_dev_auth_bypass
            or not config.session_cookie_secure
            or config.event_data_source == "production"):
        return None
    # Validate both the configured DB and any explicit importer target. A URL
    # override must never redirect this shared-preview permission elsewhere.
    for value in (config.database_url, database_url or config.database_url):
        try:
            url = make_url(value)
            if (url.get_backend_name() != "postgresql" or url.host != "postgres"
                    or url.port != 5432 or url.username != CHECKING_DATABASE
                    or url.database != CHECKING_DATABASE or url.query):
                return None
        except (ArgumentError, TypeError, ValueError):
            return None
    return CHECKING_DATABASE
