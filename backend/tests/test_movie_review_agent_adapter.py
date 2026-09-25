from types import SimpleNamespace

import pytest

from app.integrations.movie_review_agent import MovieReviewAgentAdapter


@pytest.mark.asyncio
async def test_adapter_fetches_material_without_blocking_async_interface():
    calls = []

    def fetch_material(title, year):
        calls.append((title, year))
        return {"title": title, "year": year}

    async def produce(material):
        return {"상태": "재료부족", "로그": [], "재료": material}

    adapter = MovieReviewAgentAdapter(
        SimpleNamespace(
            VERSION="V1.8",
            fetch_material=fetch_material,
            produce=produce,
        )
    )

    material = await adapter.fetch_material("괴물", 2006)

    assert material == {"title": "괴물", "year": 2006}
    assert calls == [("괴물", 2006)]
    assert adapter.version == "V1.8"


@pytest.mark.asyncio
async def test_adapter_passes_material_to_real_producer_interface():
    received = None

    def fetch_material(title, year):
        return {"title": title, "year": year}

    async def produce(material):
        nonlocal received
        received = material
        return {"상태": "대상아님", "이유": "테스트", "로그": []}

    adapter = MovieReviewAgentAdapter(
        SimpleNamespace(
            VERSION="V1.8",
            fetch_material=fetch_material,
            produce=produce,
        )
    )
    material = {"title": "괴물", "year": 2006}

    output = await adapter.produce(material)

    assert received is material
    assert output["상태"] == "대상아님"
