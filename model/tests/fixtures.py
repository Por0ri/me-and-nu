"""Small deterministic synthetic world, never a production model or quality benchmark."""

from datetime import date, datetime, timedelta

import numpy as np

from model.algo import anime_ranker as ar, content_v1 as v1, service
from model.algo.preferences import load_chip_features


def build_synthetic_world(catalog_size: int = 320):
    """Return run_once's (ctx, config, catalog_ids, chips), with work IDs 1..N."""
    if catalog_size <= ar.N_RECENT_CANDIDATES:
        raise ValueError("Synthetic catalog must exceed the unchanged recent-candidate count (300).")
    anime = {i: {"title": f"synthetic-{i}", "genres": {"Action"}, "start_date": "2020-01-01"}
             for i in range(1, catalog_size + 1)}
    features = {i: {"genres": {"Action"}, "tags": {"Isekai": 1.0, "Body Swapping": 0.5},
                    "cats": {"Isekai": "Theme-Fantasy", "Body Swapping": "Theme-Fantasy"}, "adult": False}
                for i in anime}
    ctx = ar.build_context(anime, {i: i for i in anime}, {i: catalog_size - i + 1 for i in anime}, features)
    ctx["recent_cum"], ctx["recent_d0"] = np.zeros((2, catalog_size), dtype=np.int32), 0
    history = ar.History(np.zeros(catalog_size, dtype=np.int64), np.arange(1, catalog_size + 1),
                         np.full(catalog_size, ar.STATUS["completed"]), np.zeros(catalog_size), np.zeros(catalog_size))
    ar.build_cowatch(ctx, [history], n_items=catalog_size)
    ar.build_ease(ctx)
    ctx.update(rep_of={i: i for i in anime}, work_of={i: i for i in anime},
               works={i: {"title": f"synthetic-{i}"} for i in anime})
    return (ctx, v1.Config(logs=service.LOGS), ctx["ids"][ctx["cw_rows"]],
            load_chip_features(service.MODEL / "catalog" / "chips.csv"))


def synthetic_ranking(world=None):
    from model.scripts.daily_recommend import recommend
    world = world or build_synthetic_world()
    ctx, config, ids, _ = world
    today = date(2026, 10, 29)
    posts = [{"id": i, "type": "news", "title": f"synthetic-{i}", "published": today,
              "until": today + timedelta(days=1), "menu_work_id": i} for i in range(1, 5)]
    user = {"works": [1], "genres": ["Action"], "themes": ["Isekai"], "answers": ["news"]}
    ranked = recommend(user, [], ctx, service.make_feed(posts, ctx), posts, today.toordinal() - 719163,
                       ar.to_seconds(datetime(2026, 10, 29)), config, ids)
    return posts, {1: ranked}, today
