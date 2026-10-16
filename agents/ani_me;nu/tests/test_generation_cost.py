from ani_menu_agent.generation import estimate_gpt_6_luna_cost


def test_gpt_6_luna_cost_includes_cache_reads_and_writes() -> None:
    cost = estimate_gpt_6_luna_cost(
        input_tokens=2_000,
        cached_input_tokens=400,
        cache_write_tokens=600,
        output_tokens=800,
    )
    assert cost == 0.000579
