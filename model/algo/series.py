# series — 같은 시리즈 작품끼리 묶는다
#
# 두 가지 중 하나라도 맞으면 같은 시리즈로 본다 (EXP-03)
# 1) 속편·전편 관계. 시즌끼리 묶인다: 노라가미 → 노라가미 아라가토
# 2) 제목 앞부분이 같음: "One Piece Movie 14: Stampede", "One Piece Film: Strong World" → "one piece"
# 외전·원작 같은 다른 관계는 안 쓴다. 콜라보 특별편이 원피스·나루토·드래곤볼을 한 덩어리로 엮어 버린다.

import re

SEASON_RELATIONS = {"Sequel", "Prequel"}
_TITLE_TAIL = re.compile(
    r"\s+(\d+(st|nd|rd|th)\s+season|season\s*\d+|part\s*\d+|final\s+season|the\s+movie|"
    r"movie(\s*\d+)?|film|ovas?|ona|specials?|[ivx]+|\d+)$", re.I)


def series_key(title):
    head = title.split(":")[0]
    if len(head.strip()) <= 3:  # "Re:Zero", "Re:CREATORS" 처럼 콜론 앞이 너무 짧으면 자르지 않는다 (다른 작품끼리 묶임)
        head = title
    t = re.sub(r"^gekijouban\s+", "", head, flags=re.I)
    prev = None
    while prev != t:  # 꼬리를 하나씩 떼어 낸다: "Shingeki no Kyojin Season 3 Part 2" → "Shingeki no Kyojin"
        prev = t
        t = _TITLE_TAIL.sub("", t).strip(" -'°.!")
    return t.lower() if len(t) >= 2 else title.lower()


def build_series(titles, sequel_pairs):
    """titles: 작품 id → 제목, sequel_pairs: 속편·전편 관계인 (작품, 작품) 목록 → 작품 id → 시리즈 대표 id"""
    parent = {}

    def root(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        a, b = root(a), root(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for a, b in sequel_pairs:
        union(a, b)
    first = {}
    for a in sorted(titles):
        k = series_key(titles[a])
        if k in first:
            union(a, first[k])
        else:
            first[k] = a
    return {a: root(a) for a in parent}
