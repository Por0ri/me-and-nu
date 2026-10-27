"""Keep OAuth callback credentials out of Uvicorn access logs."""

import logging
import re


_CALLBACK_PATH = re.compile(r"/api/v1/auth/login/[^/]+/callback/?")


class OAuthCallbackAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Both Uvicorn HTTP implementations log client, method, path, version,
        # and status as positional arguments. Only replace the logged path;
        # the ASGI request must retain its code/state for OAuth validation.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, separator, _ = args[2].partition("?")
            if separator and _CALLBACK_PATH.fullmatch(path):
                record.args = (*args[:2], path, *args[3:])
        return True


def install_oauth_access_log_filter() -> None:
    """Keep access logs enabled and install once, including app factory use."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, OAuthCallbackAccessLogFilter) for item in logger.filters):
        logger.addFilter(OAuthCallbackAccessLogFilter())
