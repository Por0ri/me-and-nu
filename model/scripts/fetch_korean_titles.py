# fetch_korean_titles — 위키데이터에서 애니 한국어 제목을 받는다
#
# 위키데이터(CC0, 누구나 자유롭게 사용 가능)에는 작품마다 MAL 번호(P4086)·AniList 번호(P8729)가 붙어 있고
# 한국어 이름이 있는 작품이 많다. 시리즈 전체 항목(예: "진격의 거인")에 붙어 있는 경우가 많아서
# me;nu 작품 번호(시리즈 단위)에 한국어 제목을 붙이는 데 쓴다 (make_work_catalog.py).
#
#   python model/scripts/fetch_korean_titles.py   → model/data/wikidata_ko.json (커밋 안 함)

import json
import pathlib
import urllib.parse
import urllib.request

OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "wikidata_ko.json"
URL = "https://query.wikidata.org/sparql"
QUERY = """
SELECT ?item ?mal ?anilist ?ko WHERE {
  { ?item wdt:P4086 ?mal . } UNION { ?item wdt:P8729 ?anilist . }
  ?item rdfs:label ?ko . FILTER(LANG(?ko) = "ko")
}
"""


def main():
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode({"query": QUERY, "format": "json"}), headers={
        "Accept": "application/sparql-results+json", "User-Agent": "menu-recommender-research/0.1 (school project)"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        rows = json.load(resp)["results"]["bindings"]
    out = {"mal": {}, "anilist": {}}   # 번호 → [{item: 위키데이터 항목, ko: 한국어 이름}]
    for b in rows:
        hit = {"item": b["item"]["value"].rsplit("/", 1)[-1], "ko": b["ko"]["value"]}
        for key in ("mal", "anilist"):
            if key in b:
                out[key].setdefault(b[key]["value"], []).append(hit)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8", newline="\n")
    print(f"한국어 이름: MAL 번호 {len(out['mal']):,}개 · AniList 번호 {len(out['anilist']):,}개 → {OUT}")


if __name__ == "__main__":
    main()
