from ani_menu_agent.generation import _card_count_requirement


def test_card_count_requirement_always_names_a_valid_exact_count() -> None:
    requirement = _card_count_requirement(image_count=8, fact_count=8)

    assert "exactly 5 cards" in requirement
    assert "Do not return fewer than 3 cards" in requirement


def test_card_count_requirement_blocks_when_images_cannot_fill_minimum_deck() -> None:
    requirement = _card_count_requirement(image_count=1, fact_count=8)

    assert "Do not generate yet" in requirement
    assert "minimum is 3 cards" in requirement
    assert "same-work images from the crawler" in requirement


def test_card_count_requirement_still_blocks_when_cover_has_no_source_image() -> None:
    requirement = _card_count_requirement(image_count=0, fact_count=8)

    assert "Do not generate yet" in requirement
    assert "cover" in requirement


def test_card_count_requirement_never_requests_more_cards_than_unique_images():
    requirement = _card_count_requirement(image_count=3, fact_count=8)

    assert "exactly 3 cards" in requirement
