from ani_menu_agent.sources.page_collector import _is_decorative_image_url


def test_decorative_site_assets_are_excluded_from_image_candidates() -> None:
    assert _is_decorative_image_url("https://orb-cafe.com/assets/images/common/logo.svg")
    assert _is_decorative_image_url("https://orb-cafe.com/assets/icon_search.png")
    assert not _is_decorative_image_url(
        "https://orb-cafe.com/wp-content/uploads/frieren_main_info_kv.webp"
    )
