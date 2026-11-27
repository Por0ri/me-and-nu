import unittest

from ani_rag.web_discovery import _reference_version
from ani_rag.youtube_video import youtube_video_body


TITLE = "Interview: Attack on Titan Staff @ Sakura-Con 2014"
DESCRIPTION = "The-O Network interviewed the voice of Mikasa at Sakura-Con 2014."


class YouTubeWatchPageTests(unittest.TestCase):
    def test_watch_page_uses_expanded_description_and_excludes_player_comments_and_other_videos(self):
        page = f"""Error 401 (Bad Request)
## {TITLE}
Up next
[GTA 6 jet](https://www.youtube.com/watch?v=other)
# {TITLE}
148,526 views • May 7, 2014
{DESCRIPTION}
…more
Show transcript
# {TITLE}
148,526 views148K views
May 7, 2014
![](https://yt3.ggpht.com/commenter)
The voice actress is so pretty!
Reply
## {TITLE}
1.9KLikes
148KViews
2014May 7
{DESCRIPTION}
Yui Ishikawa
Kyoji Asano
...more
Show transcript
## Video details
### [Other interview](https://www.youtube.com/watch?v=another)
"""
        self.assertEqual(youtube_video_body(TITLE + " - YouTube", page),
                         DESCRIPTION + "\nYui Ishikawa\nKyoji Asano")

    def test_transcript_requires_actual_timed_cues_for_this_video(self):
        page = f"# {TITLE}\n## Description\n{DESCRIPTION}\n## Transcript\n0:00\nFirst statement.\n1:03 Second statement."
        comments = "\n## Comments\n0:12\nSomeone else's opinion.\n0:27\nAnother comment."
        self.assertEqual(youtube_video_body(TITLE, page + comments),
                         DESCRIPTION + "\n\nFirst statement.\nSecond statement.")
        for body in (f"# {TITLE}\nFollow along using the transcript.\nShow transcript",
                     f"# {TITLE}\n## Transcript\nNo transcript available",
                     f"# {TITLE}\n## Description\n{TITLE}\n## Comments\n## Transcript\n0:00 Hi\n1:01 Bye",
                     f"# {TITLE}\n## Description\nError 401 (Bad Request)",
                     "## An unrelated video\n## Description\nSomeone else's description"):
            with self.subTest(body=body):
                self.assertEqual(youtube_video_body(TITLE, body), "")

    def test_scoped_assessment_version_keeps_other_sources_and_channel_pages_stable(self):
        def version(url):
            return _reference_version("refs", {"candidate": {"url": url}})
        baseline = version("https://example.org/video")
        self.assertEqual(version("https://www.youtube.com/@tononet"), baseline)
        self.assertEqual(version("https://www.youtube.com.example.org/watch?v=abc"), baseline)
        for url in ("https://youtu.be/abc", "https://m.youtube.com/watch?v=abc"):
            self.assertEqual(version(url), baseline + ":youtube-video-body-v1")
