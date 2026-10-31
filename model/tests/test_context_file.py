import gzip
import pickle
import tempfile
import unittest
from pathlib import Path

import numpy as np

from model.algo import anime_ranker as ar, service
from model.algo.preflight import check_inputs
from model.scripts.save_context import diff


def tiny_ctx():
    # 작품 4편 · 같이 본 기록 유저 3명 (숫자만)
    return {"ids": np.arange(4), "cw_users": 3, "cw_count": np.eye(4, dtype=np.float32),
            "cw_sim": {"ease": np.ones((4, 4))}, "rep_of": {1: 10}, "no_record": {2}, "entries": [{"anilist_id": 10}]}


class ContextFileTests(unittest.TestCase):
    def test_round_trip_is_identical(self):
        ctx = tiny_ctx()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ctx.pkl.gz"
            service.save_context(ctx, path)
            self.assertEqual(diff(ctx, service.load_context(path)), [])

    def test_user_rows_are_refused(self):
        history = ar.History(*(np.zeros(2),) * 5)
        for bad in ({"history": history}, {"histories": [history]}, {"per_user": np.zeros((3, 4))}):
            with self.subTest(bad=list(bad)), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "ctx.pkl.gz"
                with self.assertRaises(ValueError):
                    service.save_context({**tiny_ctx(), **bad}, path)
                self.assertFalse(path.exists())

    def test_other_version_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.pkl.gz"
            with gzip.open(path, "wb") as f:
                pickle.dump({"version": 0, "ctx": tiny_ctx()}, f)
            with self.assertRaises(ValueError):
                service.load_context(path)

    def test_preflight_needs_only_the_context_file(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            path = folder / "ctx.pkl.gz"
            service.save_context(tiny_ctx(), path)
            self.assertEqual(check_inputs(mal_dir=folder / "no_mal", context=path)["missing_files"], [])
            missing = check_inputs(mal_dir=folder / "no_mal", context=folder / "none.pkl.gz")
            self.assertFalse(missing["ready"])
            self.assertEqual(missing["missing_files"], [str(folder / "none.pkl.gz")])


if __name__ == "__main__":
    unittest.main()
