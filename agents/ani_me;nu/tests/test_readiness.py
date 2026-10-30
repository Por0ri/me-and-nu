from ani_menu_agent.readiness import (
    is_automated_humor_post,
    supported_verified_fact_type,
)


def test_verified_facts_are_counted_only_for_their_fact_type() -> None:
    source_types = {"one_piece_official": ["news", "event", "goods", "character"]}

    assert supported_verified_fact_type(
        "character_profile",
        {"one_piece_official"},
        {"one_piece_official"},
        source_types,
    ) == "character"
    assert supported_verified_fact_type(
        "general",
        {"one_piece_official"},
        {"one_piece_official"},
        source_types,
    ) is None
    assert supported_verified_fact_type(
        "character_profile",
        {"one_piece_official"},
        {"one_piece_official"},
        {"one_piece_official": ["news", "event"]},
    ) is None


def test_aggregate_trend_snapshot_is_not_counted_as_a_usable_humor_post() -> None:
    payload = {
        "source_id": "mastodon_social_anime",
        "status": "trend_signal",
        "summary": "#anime and #meme were frequently used",
    }

    assert not is_automated_humor_post(
        "trend_source", "trend_only:kr_first", payload, {"mastodon_social_anime"}
    )


def test_captured_discussion_post_from_approved_source_is_counted() -> None:
    payload = {
        "source_ids": ["mastodon_social_anime"],
        "statement": "A captured discussion topic with enough context to summarize for readers.",
        "details": {"source_url": "https://mastodon.social/@fan/123"},
    }

    assert is_automated_humor_post(
        "humor_idea", "trend_only:kr_first", payload, {"mastodon_social_anime"}
    )


def test_unapproved_or_non_post_humor_records_are_not_counted() -> None:
    payload = {
        "source_ids": ["theqoo_anime_trend"],
        "statement": "A captured discussion topic with enough context to summarize for readers.",
        "details": {"source_url": "https://theqoo.net/123"},
    }

    assert not is_automated_humor_post(
        "humor_idea", "trend_only:kr_first", payload, {"mastodon_social_anime"}
    )
    assert not is_automated_humor_post(
        "humor_idea", "needs_editorial_review", payload, {"theqoo_anime_trend"}
    )
