"""Loopback-only browser fixture. No production DB or runtime router changes.

Run from backend with the test dependencies and a dummy DATABASE_URL. Reuses
the HTTP tests' in-memory DB, real cookie session and test-only permission grant.
"""

import pytest
import uvicorn

from test_admin_content_api import api
from test_admin_review_queue import review_api


if __name__ == "__main__":
    with pytest.MonkeyPatch.context() as patch:
        fixture = api.__wrapped__(patch)
        try:
            state = review_api.__wrapped__(next(fixture))
            uvicorn.run(state.app, host="127.0.0.1", port=8197, log_level="warning")
        finally:
            fixture.close()
