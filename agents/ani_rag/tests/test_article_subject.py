from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
from urllib.parse import quote

from ani_rag.article_subject import ann_interview_subject, fandom_theme_song, wiki_subject
from ani_rag.catalog import LocalWorkCatalog
from ani_rag.interfaces import WorkIdentity, WorkResolution
from ani_rag.models import WorkLinkStatus
from ani_rag.web_discovery import (
    WebCandidateAssessmentWorker, WebCandidateHit, _candidate_assessment_body, _candidate_rag_query,
    _firsthand_interview_publisher,
)
from ani_rag.article_credits import article_credits
from ani_rag.models import RagContentType


_ANN = "https://www.animenewsnetwork.com"
_ARTICLE = _ANN + "/feature/2019-08-28/interview-demon-slayer-producer-yuma-takahashi/.149177"
_LABEL = "Demon Slayer: Kimetsu no Yaiba"
_LINK = _ANN + "/encyclopedia/anime.php?id=21101"
_HEAD = "Interview: Demon Slayer producer Yuma Takahashi"
_BODY = (
    f"# {_HEAD}\n\nby Jacob Chapman, Aug 28th 2019\n\n"
    f"![](https://example.org/poster.jpg)[{_LABEL}]({_LINK}) is an anime series. "
    f"We talked to producer [Yūma Takahashi]({_ANN}/encyclopedia/people.php?id=88219)."
)


class ArticleSubjectTests(TestCase):
    def test_feature_interview_uses_its_linked_lead_after_an_image(self):
        registry = SimpleNamespace(approved_sources=())
        page = SimpleNamespace(title=_HEAD, body=_BODY, final_url=_ARTICLE)
        self.assertTrue(_firsthand_interview_publisher(_ARTICLE, registry, page=page))
        credits = article_credits(RagContentType.PEOPLE, _BODY)
        self.assertIn(("producer", "Yūma Takahashi"), [(c.role, c.name) for c in credits])
        page.body = "# Feature\n\nRead this older interview: " + _BODY
        self.assertFalse(_firsthand_interview_publisher(_ARTICLE, registry, page=page))

    def test_ann_interview_lead_requires_both_focal_links_and_a_matching_headline(self):
        self.assertEqual(ann_interview_subject(_HEAD, _ARTICLE, _BODY), (_LABEL, _LINK))
        alternate = "An Interview with Yuma Takahashi, producer of Demon Slayer"
        story = (f'**[{_LABEL}]({_LINK} "Anime encyclopedia")** returns this year. '
                 f'We met producer [Yūma Takahashi]({_ANN}/encyclopedia/people.php?id=88219).')
        self.assertEqual(ann_interview_subject(alternate, _ARTICLE, story), (_LABEL, _LINK))
        for body, title in (
            (_BODY.replace("Yūma Takahashi", "Another Person"), _HEAD),
            (_BODY.replace("producer [Yūma", "actor [Yūma"), _HEAD),
            (_BODY.replace("![]", "Another work is the topic.\n\n![]"), _HEAD),
            (_BODY + f" And [Other series]({_ANN}/encyclopedia/anime.php?id=888).", _HEAD),
            (_BODY.replace(_HEAD, _HEAD.replace("Demon Slayer", "Demon Slayer Mugen Train")),
             _HEAD.replace("Demon Slayer", "Demon Slayer Mugen Train")),
        ):
            with self.subTest(title=title, body=body):
                self.assertIsNone(ann_interview_subject(title, _ARTICLE, body))

    def test_character_index_needs_same_self_and_parent_pages_and_anime_classification(self):
        subject = "진격의 거인"
        title = subject + "/등장인물"
        wiki = "https://namu.wiki/w/"
        url = wiki + quote(title)
        parent = wiki + quote(subject)
        category = wiki + quote("분류:애니메이션/등장인물")
        body = (f"# [{title}]({url})\n\n분류\n\n- [애니메이션/등장인물]({category})\n\n"
                f'상위 문서: [{subject}]({parent} "{subject}")')
        self.assertEqual(wiki_subject(title + " - 나무위키", url, body), subject)
        for bad in (body.replace(category, wiki + "Other"), body.replace("애니메이션", "만화"),
                    body.replace(parent + ' "', parent + '_manga "'), body.replace("# [", "## [")):
            self.assertIsNone(wiki_subject(title, url, bad))

    def test_soundtrack_requires_album_heading_and_explicit_anime_credit(self):
        url = "https://hiroyuki-sawano.fandom.com/wiki/Attack_on_Titan_Original_Soundtrack"
        album = '"Attack on Titan" Original Soundtrack'
        title = album + " | Hiroyuki Sawano Wiki | Fandom"
        body = (f'# {album}\n\n**{album}** is an album containing background music from the TV anime series '
                '[Attack on Titan](https://attackontitan.fandom.com/wiki/Attack_on_Titan_Wiki) Season One.')
        self.assertEqual(wiki_subject(title, url, body), "Attack on Titan")
        for bad in (body.replace("TV anime series", "live-action film"),
                    body.replace("Titan_Wiki", "Titan_Junior_High_Wiki"),
                    body.replace("Season One", "Junior High"), body.replace("# ", "## ")):
            self.assertIsNone(wiki_subject(title, url, bad))

    def test_theme_song_requires_same_wiki_and_an_explicit_work_credit_in_the_lead(self):
        song = "Chizu ga Nakute mo Modoru Kara"
        work = "One-Punch Man"
        title = f"{song} | {work} Wiki | Fandom"
        base = "https://onepunchman.fandom.com/wiki/"
        url = base + song.replace(" ", "_")
        home = base + "One-Punch_Man_Wiki"
        head = f"[{work} Wiki]({home})\n\n# {song}\n\n[Edit]({url}?action=edit)\n\n"
        intro = (f"**{song}** ( _地図が無くても戻るから_, _No map_) is a single from singer "
                 f"[Makoto Furukawa](https://example.org/singer). It was used as the ending theme song for "
                 f"the {work} anime adaptation, being featured through the whole 2nd season.")
        self.assertEqual(wiki_subject(title, url, head + intro), work)
        self.assertEqual(wiki_subject(title, url, head + f"{song} is the opening theme of the {work} anime."), work)
        for candidate_url, body in (
            (url + "_Album", head + intro), (url, head.replace("# " + song, "# Another song") + intro),
            (url, head.replace(home, base + "Music_Wiki") + intro),
            (url, head.replace("onepunchman.fandom.com", "music.fandom.com") + intro),
            (url.replace("onepunchman.", "music."), head.replace("onepunchman.", "music.") + intro),
            (url, head + intro.replace("the One-Punch Man anime", "the Mob Psycho 100 anime")),
            (url, head + intro.replace("It was used", "Its promotional video said it could be used")),
            (url, head + intro.replace("anime adaptation,", "anime and the Mob Psycho 100 anime,")),
            (url, head + "[Another song](https://example.org/music) was featured here.\n\n" + intro),
            (url, head + "## Navigation\n\n" + intro),
        ):
            with self.subTest(url=candidate_url, body=body):
                self.assertIsNone(wiki_subject(title, candidate_url, body))


class CatalogArticleTests(IsolatedAsyncioTestCase):
    async def test_translated_wiki_requires_a_catalog_host_and_anime_link_in_the_song_credit(self):
        song = "Seishun Satsubatsuron"
        work = "Assassination Classroom"
        title = f"{song} | {work} Wiki | Fandom"
        base = "https://ansatsukyoshitsu.fandom.com/wiki/"
        url = base + "Seishun_Satsubatsuron"
        anime = base + "Assassination_Classroom_(anime)"
        work_link = f'[{work}]({anime} "{work} (anime)")'
        header = (f"[{work} Wiki]({base}Assassination_Classroom_Wiki)\n\n# {song}\n\n"
                  f"[Edit]({url}?action=edit)\n\n"
                  f"| _{song}_ |\n| --- |\n| **Artist** | **3-nen E-gumi Utatan** |\n"
                  "| Anime Debut | Episode 1 |\n| Next | Another song |\n\n")
        intro = (f"**{song}** (青春サツバツ論, _**{song}**_) is the first opening theme of the "
                 f"{work_link} TV anime series. The opening is sung by character VAs as a group.")
        catalog = LocalWorkCatalog()
        fallback = AsyncMock(return_value=WorkResolution(WorkLinkStatus.UNRESOLVED))
        worker = WebCandidateAssessmentWorker(None, None, SimpleNamespace(resolve=fallback), None, None,
                                             edition_catalog=catalog)
        noisy_page = (f"Sidebar song of the month\n\n{header}{intro}\n\n## Contents\n\n- CD\n\n"
                      "## CD\n\n1. Unrelated bonus track\n\n## Lyrics\n\nUnrelated lyrics")
        page = SimpleNamespace(title=title, body=noisy_page, final_url=url)
        identity = WorkIdentity((title,), {}, source_url=url, source_text=page.body, seed_menu_work_ids=(7,))
        result = await worker._resolve_article(page, identity)
        self.assertEqual(result.menu_work_ids, (17,))
        fallback.assert_not_awaited()
        song_article = fandom_theme_song(title, url, noisy_page, catalog)
        hit = WebCandidateHit(title, url, snippet="Sidebar song of the month")
        for text in (_candidate_assessment_body(page, song=song_article), _candidate_rag_query(page, hit, song=song_article)):
            for needed in ("first opening theme", work, "3-nen E-gumi Utatan", "Episode 1"):
                self.assertIn(needed, text)
            for unrelated in ("Sidebar", "Another song", "bonus track", "Lyrics", "Contents", "action=edit"):
                self.assertNotIn(unrelated, text)

        for article_url, body in (
            (url, header + intro.replace(work_link, work)),
            (url, header + intro.replace(anime, base + "Another_series_(anime)")),
            (url, header + intro.replace("_(anime)", "_(manga)")),
            (url, header + intro.replace(anime, anime.replace("ansatsukyoshitsu.", "anotherwiki."))),
            (url, header + intro.replace("the first opening", "a cover of the first opening")),
            (url, header + intro.replace(f"**{song}** (", "**Another song** (")),
            (url, header + f"| Next | {work_link} |\n\n" + intro.replace(work_link, work)),
            (url, header + "## Related songs\n\n" + intro),
            (url.replace("ansatsukyoshitsu", "ansatsukyoshitsufans"),
             (header + intro).replace("ansatsukyoshitsu", "ansatsukyoshitsufans")),
        ):
            with self.subTest(url=article_url, body=body):
                self.assertIsNone(wiki_subject(title, article_url, body, catalog=catalog))

    async def test_db_linked_work_is_resolved_independently_of_search_seed_and_model(self):
        fallback = AsyncMock(return_value=WorkResolution(WorkLinkStatus.UNRESOLVED))
        lookup = AsyncMock(return_value=(2,))
        worker = WebCandidateAssessmentWorker(
            None, None, SimpleNamespace(resolve=fallback), None, None,
            page_cache=SimpleNamespace(linked_ann_work_ids=lookup), edition_catalog=LocalWorkCatalog(),
        )
        page = SimpleNamespace(title=_HEAD, body=_BODY, final_url=_ARTICLE)
        # An unrelated search seed cannot turn the linked Demon Slayer interview into Attack on Titan.
        identity = WorkIdentity((_HEAD,), {}, source_text=_BODY, source_url=_ARTICLE, seed_menu_work_ids=(1,))
        result = await worker._resolve_article(page, identity)
        self.assertEqual(result.menu_work_ids, (2,))
        lookup.assert_awaited_once_with(_LABEL, _LINK)
        fallback.assert_not_awaited()
        lookup.return_value = (1,)
        result = await worker._resolve_article(page, identity)
        self.assertEqual(result.status, WorkLinkStatus.AMBIGUOUS)
        fallback.assert_not_awaited()
        lookup.return_value = ()
        self.assertEqual((await worker._resolve_article(page, identity)).status, WorkLinkStatus.UNRESOLVED)
        fallback.assert_awaited_once()
