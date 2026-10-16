# make_anime_id_list — 애니 번호(AniList id)와 제목 목록을 만든다
#
# 애니 에이전트가 콘텐츠를 만들 때 "이 글이 어떤 작품 글인지" AniList 번호를 같이 저장하게 하려고
# 넘겨주는 목록이다. 서비스의 작품 번호 기준은 AniList 번호다.
# - 재료: fetch_anilist.py 로 받은 파일 두 개 (MAL 인기 상위 + 2022년 이후 인기작)
# - 같은 작품은 하나로 합치고, 성인물은 뺀다
# - 칸: anilist_id, title_romaji, title_english, title_native (한국어 제목은 나중에)
#
#   python model/scripts/fetch_anilist.py               # 먼저 받기 (한 번만)
#   python model/scripts/fetch_anilist.py recent 1000
#   python model/scripts/make_anime_id_list.py
#   → model/data/anime_id_list.csv (커밋 안 함)

import csv
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "data"
SOURCES = [ROOT / "anilist" / "media.jsonl", ROOT / "anilist" / "media_recent.jsonl"]
OUT = ROOT / "anime_id_list.csv"


def main():
    works, adult = {}, 0
    for path in SOURCES:
        with open(path, encoding="utf-8") as f:
            for line in f:
                for m in json.loads(line)["media"]:
                    if m["isAdult"]:
                        adult += 1
                        continue
                    works[m["id"]] = m["title"]
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["anilist_id", "title_romaji", "title_english", "title_native"])
        for i in sorted(works):
            t = works[i]
            w.writerow([i, t["romaji"] or "", t["english"] or "", t["native"] or ""])
    print(f"작품 {len(works):,}편 (성인물 {adult}편 제외) → {OUT}")


if __name__ == "__main__":
    main()
