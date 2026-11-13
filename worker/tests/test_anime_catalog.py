"""Offline source/work requests are bound to current publisher and catalog data."""
import csv
import json
from pathlib import Path
from shutil import copyfile
import tempfile
import unittest
from unittest.mock import patch

from menu_worker import anime_catalog as catalog


class AnimeCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.enterContext(patch.object(catalog, "CATALOG_ROOT", self.root))
        self.sources = [
            {"id": "studio_a", "status": "active", "role": "primary", "collection": "html",
             "url": "https://publisher.org/series/a/index.html", "work_title_ko": "여름☆날", "work_title_original": "夏 日",
             "allowed_asset_hosts": ["cdn.publisher.org"]},
            {"id": "magazine", "status": "active", "role": "editorial", "collection": "html",
             "url": "https://publisher.org/magazine/"},
        ]
        self.write_registry()
        self.write_works([(81, "여름날", "夏日"), (82, "다른 작품", "冬")])

    def write_registry(self):
        (self.root / "sources.json").write_text(json.dumps({"version": 2, "sources": self.sources}), encoding="utf-8")
        mapping = {"types": {"news": "소식", "goods": "상품"}, "sources": {"studio_a": ["news"], "magazine": ["goods"]}}
        (self.root / "source_type_map.json").write_text(json.dumps(mapping), encoding="utf-8")

    def write_works(self, rows):
        with (self.root / "menu_works.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("menu_work_id", "title_ko", "title_native", "title_english", "title_romaji"))
            for work_id, ko, native in rows:
                writer.writerow((work_id, ko, native, "", ""))

    def validate(self, **overrides):
        args = {"source_id": "studio_a", "source_url": "https://publisher.org/series/a/123/index.html",
                "content_type": "news", "menu_work_id": 81, **overrides}
        return catalog.validate_anime_source(**args)

    def assert_rejected(self, code, **overrides):
        with self.assertRaises(catalog.AnimeInputError) as caught:
            self.validate(**overrides)
        self.assertEqual(caught.exception.code, code)

    def test_accepts_article_and_normalizes_origin_path_and_tracking(self):
        value = ("HTTPS://PUBLISHER.ORG:443/series/a/%31%32%33/index.html/"
                 "?UtM_custom=x&lang=ja&id=4&fbCLid=visitor&gCLiD=visitor#section")
        self.assertEqual(self.validate(source_url=value), "https://publisher.org/series/a/123/index.html?id=4&lang=ja")
        self.assertEqual(self.validate(source_url="https://publisher.org/series/a/index.html?id=42"),
                         "https://publisher.org/series/a/index.html?id=42")

    def test_raw_storage_retains_path_origin_order_and_encoding_but_removes_tracking_and_fragments(self):
        raw = "HTTPS://PUBLISHER.ORG:443/series/a/%31/index.html/?story=two%20words&extra=%7e"
        self.assertEqual(catalog.source_url_for_storage(raw), raw)
        self.assertEqual(catalog.source_url_for_storage(raw + "&%75TM_extra=SECRET#fragment"), raw)
        self.assertEqual(catalog.source_url_for_storage(raw + "#fragment"), raw)
        self.assertEqual(catalog.source_url_for_storage("https://publisher.org?p=4"), "https://publisher.org?p=4")
        self.assertEqual(catalog.source_url_for_storage("https://publisher.org#fragment"), "https://publisher.org/")
        self.assertEqual(catalog.source_url_for_storage("https://publisher.org?utm_id=1"), "https://publisher.org/")
        self.assertEqual(catalog.canonical_anime_url("https://publisher.org/?utm_token=SECRET#fragment"),
                         "https://publisher.org/")

    def test_preserves_public_query_identifiers_and_distinct_values(self):
        listing = "https://publisher.org/series/a/index.html"
        for key in ("slug", "story", "episode_id"):
            with self.subTest(key=key):
                self.assertEqual(self.validate(source_url=f"{listing}?{key}=one"), f"{listing}?{key}=one")
                self.assertNotEqual(self.validate(source_url=f"{listing}?{key}=one"),
                                    self.validate(source_url=f"{listing}?{key}=two"))
        article = "https://publisher.org/series/a/123"
        self.assertEqual(self.validate(source_url=article + "?z=2&unknown=kept&lang=ja#chapter"),
                         article + "?lang=ja&unknown=kept&z=2")

    def test_prefers_an_in_scope_article_hint_and_falls_back_to_the_request(self):
        article = "https://publisher.org/series/a/123/index.html"
        listing = "https://publisher.org/series/a/index.html"
        hint = "https://publisher.org/series/a/42/index.html"
        for request in (article, listing):
            self.assertEqual(self.validate(source_url=request, canonical_url=hint + "/?utm_x=y#details"), hint)
        for invalid_hint in ("not a URL", "https://other.org/series/a/story", "https://publisher.org/series/b/42",
                             hint + "?token=SECRET", "https://publisher.org/series/a/picture.png", listing, 42):
            with self.subTest(invalid_hint=invalid_hint):
                self.assertEqual(self.validate(source_url=article, canonical_url=invalid_hint), article)
                self.assert_rejected("source_path_mismatch", source_url=listing, canonical_url=invalid_hint)
        for request, error in (("https://other.org/series/a/article", "source_host_mismatch"),
                               ("https://publisher.org/series/b/42", "source_path_mismatch"),
                               ("https://publisher.org/series/a/picture.png", "source_path_mismatch"),
                               (article + "?token=SECRET", "invalid_source_query")):
            with self.subTest(request=request):
                self.assert_rejected(error, source_url=request, canonical_url=hint)

    def test_article_identity_cannot_escape_its_registered_scope(self):
        for url in ("https://publisher.org/series/b/123", "https://publisher.org/series/aa/123",
                    "https://publisher.org/series/a/", "https://publisher.org/series/a/index.html",
                    "https://publisher.org/series/a/index.htm?lang=ja", "https://publisher.org/series/a/picture.png",
                    "https://publisher.org/series/a/index.html?lang=ja&utm_new=x#id=9",
                    "https://publisher.org/series/a/index.html?id="):
            with self.subTest(url=url):
                self.assert_rejected("source_path_mismatch", source_url=url)
        for host in ("cdn.publisher.org", "x.publisher.org", "publisher.org.com"):
            with self.subTest(host=host):
                self.assert_rejected("source_host_mismatch", source_url=f"https://{host}/series/a/123")

    def test_broad_publisher_registration_does_not_cover_a_peer_endpoint(self):
        self.sources[0]["url"] = "https://publisher.org/"
        self.write_registry()
        self.assert_rejected("source_path_mismatch", source_url="https://publisher.org/magazine/123")
        self.assertEqual(self.validate(), "https://publisher.org/series/a/123/index.html")

    def test_rejects_unsafe_url_syntax_without_echoing_it(self):
        for url in ("https://user:SECRET@publisher.org/series/a/123", "https://publisher.org:8080/series/a/123",
                    "http://publisher.org/series/a/123",
                    "https://127.0.0.1/series/a/123", "https://x.local/series/a/123",
                    "https://publisher.org/series/a/%2e%2e/b", "https://publisher.org/series/a/x%2fy",
                    "https://publisher.org/series/a/x%255cy", "https://publisher.org/series/a/x%0dy",
                    "https://publisher.org/series/a/x%00", "https://publisher.org/series/a/x%zz",
                    "https://publisher.org/series/a/123?%74oken=SECRET", "https://publisher.org/series/a/123?apiKey=SECRET",
                    "https://publisher.org/series/a/123?session_id=SECRET", "https://publisher.org/series/a/123?access_token=SECRET",
                    "https://publisher.org/series/a/123?X-Amz-Credential=SECRET", "https://publisher.org/series/a/123?id=1&id=2"):
            with self.subTest(url=url), self.assertRaises(catalog.AnimeInputError) as caught:
                self.validate(source_url=url)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_source_policy_and_existing_work_are_independently_checked(self):
        self.assert_rejected("unknown_source", source_id="unregistered")
        self.assert_rejected("unsupported_content_type", content_type="goods")
        self.assert_rejected("unknown_work", menu_work_id=999)
        self.assert_rejected("invalid_menu_work_id", menu_work_id=True)
        self.assert_rejected("source_work_mismatch", menu_work_id=82)
        self.assert_rejected("source_work_unresolved", source_id="magazine", content_type="goods",
                             source_url="https://publisher.org/magazine/123")
        self.sources[0]["role"] = "discovery"
        self.write_registry()
        self.assert_rejected("unsupported_source")

    def test_unavailable_revoked_or_ambiguous_catalog_fails_closed(self):
        self.validate()
        self.sources[0]["status"] = "terms_review"
        self.write_registry()
        self.assert_rejected("source_inactive")
        self.sources[0]["status"] = "active"
        self.write_registry()
        self.write_works([(81, "여름날", "夏日"), (82, "중복 별칭", "夏 日")])
        self.assert_rejected("source_work_unresolved")
        (self.root / "menu_works.csv").unlink()
        self.assert_rejected("catalog_unavailable")


class ProducerReferenceTests(unittest.TestCase):
    def test_registered_anime_and_existing_work_match_the_producer_files(self):
        names = ("sources.json", "source_type_map.json", "menu_works.csv")
        if all((catalog.CATALOG_ROOT / name).is_file() for name in names):
            root = catalog.CATALOG_ROOT
        else:
            repo = Path(__file__).resolve().parents[2]
            producer = repo / "agents" / "ani_me;nu"
            files = (producer / "config" / names[0], producer / "config" / names[1],
                     repo / "catalog" / names[2])
            self.assertTrue(all(path.is_file() for path in files),
                            "The producer registry and work catalog must be available in the repo or Worker image")
            root = Path(self.enterContext(tempfile.TemporaryDirectory()))
            for path in files:
                copyfile(path, root / path.name)
        with patch.object(catalog, "CATALOG_ROOT", root):
            for source, url, kind, work in (
                ("one_piece_official", "https://one-piece.com/news/82812/index.html", "goods", 8),
                ("my_hero_academia_official", "https://heroaca.com/news/28707", "news", 5),
            ):
                with self.subTest(source=source):
                    self.assertEqual(catalog.validate_anime_source(source, url, kind, work), url)
            with self.assertRaises(catalog.AnimeInputError) as caught:
                catalog.validate_anime_source("kimetsu_official", "https://kimetsu.com/anime/news/10", "news", 2)
            self.assertEqual(caught.exception.code, "source_inactive")


if __name__ == "__main__":
    unittest.main()
