"""AnimeThemes 곡(로마자 제목)과 쇼보이 주제가(일본어 원제 · 작사 · 작곡)를 잇기. 밖으로 보내는 요청 없음

  uv run python -m ani_rag.link_theme_songs        (agents/rag 폴더에서)

- OP/ED 번호는 출처마다 달라서(예: 프리렌 ED1) 번호로만 맞추지 않는다. 같은 시즌 · 같은 종류(OP/ED) 안에서
  1) 제목이 글자 그대로 같으면 (영어 제목 곡)
  2) 가수가 AnimeThemes 가수의 별칭과 같고 그 가수의 곡이 양쪽에 하나씩이면
  3) 그 시즌의 그 종류가 양쪽 다 딱 한 곡이면
  잇는다. 그래도 안 되면 잇지 않는다
- 이 스크립트가 맡는 칸 (출처 syoboi, 곡에 붙음): song_title_ja · song_credit
"""

from __future__ import annotations

import sys
import time
from collections import Counter, defaultdict

import psycopg

from ani_rag.config import load_settings
from ani_rag.store import Store, norm

SYOBOI = "syoboi"
FIELDS = ("song_title_ja", "song_credit")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    stats: Counter = Counter()
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        cur.execute("""select f.entity_id, f.value->>'type', f.value_entity_id, s.name_romaji
                       from dict.fact f join dict.entity s on s.entity_id = f.value_entity_id
                       where f.source_id = 'animethemes' and f.field = 'theme_song'""")
        at_songs: dict[tuple[int, str], list[tuple[int, str]]] = defaultdict(list)
        for eid, kind, sid, title in cur.fetchall():
            at_songs[(eid, kind)].append((sid, title or ""))
        cur.execute("""select f.entity_id, a.alias from dict.fact f join dict.alias a on a.entity_id = f.value_entity_id
                       where f.source_id = 'animethemes' and f.field = 'performer'""")
        singer_names: dict[int, set[str]] = defaultdict(set)
        for sid, alias in cur.fetchall():
            singer_names[sid].add(norm(alias))
        cur.execute("""select f.entity_id, f.value, f.source_url, f.fetched_at from dict.fact f
                       where f.source_id = 'syoboi' and f.field = 'theme_song_ja'""")
        sy_songs: dict[tuple[int, str], list[tuple[dict, str, object]]] = defaultdict(list)
        for eid, value, url, at in cur.fetchall():
            sy_songs[(eid, value["type"])].append((value, url, at))

        def link(sid: int, theme: dict, url: str, at, why: str) -> None:
            store.fact(sid, "song_title_ja", theme["title"], at, url, SYOBOI)
            credit = {k: theme[k] for k in ("lyricist", "composer", "arranger", "singer") if theme.get(k)}
            if credit:
                store.fact(sid, "song_credit", credit, at, url, SYOBOI)
            stats[why] += 1

        for key, songs in at_songs.items():
            themes = list(sy_songs.get(key, []))
            if not themes:
                stats["쇼보이에 없음"] += len(songs)
                continue
            left = list(songs)
            for song in list(left):  # 1) 제목이 그대로 같음
                hit = [t for t in themes if norm(t[0]["title"]) == norm(song[1])]
                if len(hit) == 1:
                    link(song[0], hit[0][0], hit[0][1], hit[0][2], "제목 같음")
                    themes.remove(hit[0])
                    left.remove(song)
            for song in list(left):  # 2) 가수가 같음
                hit = [t for t in themes if norm(t[0].get("singer")) in singer_names.get(song[0], set())]
                same_singer = [s for s in left if singer_names.get(s[0], set()) & singer_names.get(song[0], set())]
                if len(hit) == 1 and len(same_singer) == 1:
                    link(song[0], hit[0][0], hit[0][1], hit[0][2], "가수 같음")
                    themes.remove(hit[0])
                    left.remove(song)
            if len(left) == 1 and len(themes) == 1:  # 3) 양쪽 다 한 곡
                link(left[0][0], themes[0][0], themes[0][1], themes[0][2], "양쪽 다 한 곡")
                left = []
            stats["못 이음"] += len(left)
        upserted, deleted = store.write_facts(SYOBOI, FIELDS)
        total = store.count("select count(distinct entity_id) from dict.fact where field = 'song_title_ja'")
        songs_n = store.count("select count(*) from dict.entity where kind = 'song'")

    print("잇기: " + " · ".join(f"{k} {v:,}" for k, v in stats.items()))
    print(f"일본어 원제가 붙은 곡 {total:,} / {songs_n:,}")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
