from pathlib import Path

import httpx
import pytest

from ani_menu_agent.agents.image.download import ImageDownloadError, _check_robots
from ani_menu_agent.models import ContentPackage

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generation_input.json"


def test_image_batch_reuses_robots_policy_but_still_enforces_it(monkeypatch) -> None:
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    source = package.sources[0]
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /private")

    monkeypatch.setattr(
        "ani_menu_agent.agents.image.download._validate_public_https", lambda *_args: None
    )
    cache = {}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _check_robots(client, "https://example.com/assets/one.jpg", source, cache)
        _check_robots(client, "https://example.com/assets/two.jpg", source, cache)
        with pytest.raises(ImageDownloadError, match="robots.txt"):
            _check_robots(client, "https://example.com/private/three.jpg", source, cache)

    assert requests == ["https://example.com/robots.txt"]
