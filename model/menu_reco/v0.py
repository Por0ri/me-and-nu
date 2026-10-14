# v0 추천 — 규칙 점수로 콘텐츠 순서를 정한다
#
# 가장 단순한 추천이다. 학습은 하지 않는다. 목표는 처음부터 끝까지 한 번 돌아가는 것.
# 이후 버전(v0.5)은 이 파일의 점수 계산을 하나씩 바꿔 가며 채점기로 비교한다.
#
# 흐름: 후보 거르기 → 점수 매기기 → 순서 섞기 → 스포일러 표시
#
#   python -m model.menu_reco.v0            # 샘플 유저 6명 추천 결과 출력
#
# 가중치는 모두 가설이다. 유저 테스트 로그가 쌓이면 다시 맞춘다.

import json
import math
import pathlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

# ## 1. 설정
# 점수 가중치. 숫자가 클수록 그 이유가 순위에 크게 작용한다.
ALGORITHM_VERSION = "v0"

W_TYPE = 1.0        # 유저가 고른 콘텐츠 유형과 같으면
W_GENRE = 1.0       # 유저가 고른 장르와 겹치는 비율만큼
W_WORK = 1.5        # 유저가 좋아하는 작품의 콘텐츠면
W_FRESH_NEWS = 0.8  # 소식: 새 글일수록 (7일마다 약 1/e로 줄어듦)
W_FRESH_OTHER = 0.2 # 그 밖의 유형: 아주 약하게만

# 반응별 신호 세기. 같은 작품의 다른 콘텐츠에 이만큼 더하거나 뺀다
FEEDBACK_WEIGHT = {"like": 0.5, "save": 0.8, "more": 1.0, "less": -1.5}
FEEDBACK_TYPE_SHARE = 0.3  # 반응은 같은 유형에도 30%만큼 번진다

SPOILER_BLUR_LEVEL = 2     # 이 등급 이상이면 흐리게
TOP_N = 10


# ## 2. 데이터 모양
# 콘텐츠·유저는 dict 로 받는다 (샘플 JSON, 나중에는 DB 조회 결과).
@dataclass
class Scored:
    content: dict
    score: float
    parts: dict = field(default_factory=dict)  # 이유별 점수 (추천 이유 문구에 씀)
    blurred: bool = False


def _parse_dt(s):
    return datetime.fromisoformat(s) if s else None


# ## 3. 후보 거르기
# 노출하면 안 되는 콘텐츠를 뺀다: 게시중 아님, 기간 끝남, 이미 봄.
def is_candidate(c, user, now):
    if c.get("status") != "게시중":
        return False
    if c["content_id"] in set(user.get("seen_content_ids", [])):
        return False
    ev = c.get("event")
    if ev and ev.get("ends_on") and date.fromisoformat(ev["ends_on"]) < now.date():
        return False
    exp = _parse_dt(c.get("expires_at"))
    if exp and exp < now:
        return False
    return True


# ## 4. 반응에서 취향 뽑기
# 유저가 남긴 좋아요·저장·더볼래요·그만볼래요를 작품별, 유형별 점수로 모은다.
def feedback_affinity(user, contents_by_id):
    work, ctype = {}, {}
    for fb in user.get("feedback", []):
        c = contents_by_id.get(fb["content_id"])
        if not c:
            continue
        w = FEEDBACK_WEIGHT.get(fb["action"], 0.0)
        e = c.get("primary_entity_id")
        if e:
            work[e] = work.get(e, 0.0) + w
        ctype[c["content_type"]] = ctype.get(c["content_type"], 0.0) + w * FEEDBACK_TYPE_SHARE
    return work, ctype


# ## 5. 점수 매기기
def score_content(c, user, entities, aff_work, aff_type, now):
    parts = {}
    if c["content_type"] in user.get("content_types", []):
        parts["유형"] = W_TYPE

    ent = entities.get(c.get("primary_entity_id"))
    genres = set(ent["attributes"]["genres"]) if ent else set()
    liked = set(user.get("genres", []))
    if genres and liked:
        overlap = len(genres & liked) / len(liked)
        if overlap:
            parts["장르"] = W_GENRE * overlap

    if c.get("primary_entity_id") in set(user.get("favorite_works", [])):
        parts["작품"] = W_WORK

    age_days = max((now - _parse_dt(c["published_at"])).total_seconds() / 86400, 0)
    if c["content_type"] == "news":
        parts["최신"] = W_FRESH_NEWS * math.exp(-age_days / 7)
    else:
        parts["최신"] = W_FRESH_OTHER * math.exp(-age_days / 30)

    fb = aff_work.get(c.get("primary_entity_id"), 0.0) + aff_type.get(c["content_type"], 0.0)
    if fb:
        parts["반응"] = fb

    return Scored(content=c, score=sum(parts.values()), parts=parts)


# ## 6. 순서 섞기
# 같은 작품 글이 연달아 나오지 않게, 바로 앞과 같은 작품이면 다음 순위 글을 먼저 놓는다.
def diversify(ranked):
    out, pool = [], list(ranked)
    while pool:
        prev = out[-1].content.get("primary_entity_id") if out else None
        pick = next((i for i, s in enumerate(pool) if s.content.get("primary_entity_id") != prev), 0)
        out.append(pool.pop(pick))
    return out


# ## 7. 스포일러 표시와 추천 이유
# 좋아하는 작품(온보딩)과 "다 봤어요" 누른 작품은 본 것으로 친다.
def mark_spoiler(s, user):
    c = s.content
    watched = set(user.get("favorite_works", [])) | set(user.get("watched_works", []))
    s.blurred = c.get("spoiler_level", 0) >= SPOILER_BLUR_LEVEL and c.get("spoiler_entity_id") not in watched
    return s


REASON_TEXT = {
    "작품": "좋아하는 {work} 관련",
    "유형": "자주 보는 유형",
    "장르": "좋아하는 장르",
    "반응": "비슷한 글에 반응해서",
    "최신": "새로 올라온 글",
}


def reason(s, entities):
    top = max(s.parts.items(), key=lambda kv: kv[1])[0]
    ent = entities.get(s.content.get("primary_entity_id"))
    return REASON_TEXT[top].format(work=ent["name"] if ent else "")


# ## 8. 추천 실행
def recommend(user, contents, entities, now, top_n=TOP_N):
    by_id = {c["content_id"]: c for c in contents}
    aff_work, aff_type = feedback_affinity(user, by_id)
    scored = [score_content(c, user, entities, aff_work, aff_type, now)
              for c in contents if is_candidate(c, user, now)]
    scored.sort(key=lambda s: (-s.score, s.content["content_id"]))
    ranked = diversify(scored)[:top_n]
    return [mark_spoiler(s, user) for s in ranked]


def load_sample(sample_dir=None):
    d = pathlib.Path(sample_dir) if sample_dir else pathlib.Path(__file__).resolve().parents[1] / "sample"
    entities = {e["entity_id"]: e for e in json.loads((d / "entities.json").read_text(encoding="utf-8"))}
    contents = json.loads((d / "contents.json").read_text(encoding="utf-8"))
    users = json.loads((d / "users.json").read_text(encoding="utf-8"))
    return entities, contents, users


def main():
    entities, contents, users = load_sample()
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    for u in users:
        print(f"\n■ {u['name']}  유형 {u['content_types']} · 장르 {u['genres']} · 작품 {[entities[w]['name'] for w in u['favorite_works']]}")
        for rank, s in enumerate(recommend(u, contents, entities, now), 1):
            c = s.content
            flag = " [스포일러 흐림]" if s.blurred else ""
            print(f"  {rank:>2}. {s.score:4.2f}  [{c['content_type']:<8}] {c['title']}{flag}  ← {reason(s, entities)}")


if __name__ == "__main__":
    main()
