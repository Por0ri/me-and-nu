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


def test_allowlisted_cdn_redirects_robots_check_to_publisher_policy(monkeypatch) -> None:
    from ani_menu_agent.models import SourceEvidence

    source = SourceEvidence(
        id="anime_corner",
        title="Same-work review article",
        url="https://animecorner.me/cyberpunk-review/",
        publisher="Anime Corner",
        role="editorial",
        allowed_asset_hosts=["static.animecorner.me"],
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "static.animecorner.me":
            return httpx.Response(302, headers={"location": "https://animecorner.me/"})
        return httpx.Response(200, text="User-agent: *\nAllow: /")

    monkeypatch.setattr(
        "ani_menu_agent.agents.image.download._validate_public_https", lambda *_args: None
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _check_robots(
            client,
            "https://static.animecorner.me/2026/10/visual.png",
            source,
            {},
        )

    assert requests == [
        "https://static.animecorner.me/robots.txt",
        "https://animecorner.me/robots.txt",
    ]


def test_allowlisted_cdn_403_uses_publisher_robots_policy(monkeypatch) -> None:
    from ani_menu_agent.models import SourceEvidence

    source = SourceEvidence(
        id="prtimes_release_pages",
        title="Official press release",
        url="https://prtimes.jp/main/html/rd/p/000000001.000000001.html",
        publisher="PR TIMES",
        is_official=True,
        role="primary",
        allowed_asset_hosts=["prcdn.freetls.fastly.net"],
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "prcdn.freetls.fastly.net":
            return httpx.Response(403)
        return httpx.Response(
            200,
            text=(
                "User-agent: *\n"
                "Allow: /\n"
                "Disallow: /im/action.php?run=html&page=releaseimage\n"
            ),
        )

    monkeypatch.setattr(
        "ani_menu_agent.agents.image.download._validate_public_https", lambda *_args: None
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _check_robots(
            client,
            "https://prcdn.freetls.fastly.net/release_image/1/2/image.jpg",
            source,
            {},
        )

    assert requests == [
        "https://prcdn.freetls.fastly.net/robots.txt",
        "https://prtimes.jp/robots.txt",
    ]


def test_unrelated_cdn_robots_redirect_stays_blocked(monkeypatch) -> None:
    from ani_menu_agent.models import SourceEvidence

    source = SourceEvidence(
        id="anime_corner",
        title="Same-work review article",
        url="https://animecorner.me/cyberpunk-review/",
        publisher="Anime Corner",
        role="editorial",
        allowed_asset_hosts=["static.animecorner.me"],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://another.example/"})

    monkeypatch.setattr(
        "ani_menu_agent.agents.image.download._validate_public_https", lambda *_args: None
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImageDownloadError, match="robots.txt"):
            _check_robots(
                client,
                "https://static.animecorner.me/2026/10/visual.png",
                source,
                {},
            )
