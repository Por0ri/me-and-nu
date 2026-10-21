# new_anime_test — 신작 흉내 시험 (EXP-21)
#
# me;nu 에서 2022년 이후 작품은 MAL 기록이 없어서 "같이 본 작품"(같이 본 횟수·EASE)을 모른다.
# 그 상황을 MAL 채점기에서 흉내 낸다: 정답 기간(2020~2021년)에 시작한 작품을 "신작"으로 치고
# 그 작품들의 같이 본 기록을 지운다 (인기도·AniList 태그는 서비스에서도 있으니 그대로 둔다).
# 그다음 AniList 커뮤니티 추천("이거 좋아하면 이것도", 유저 투표)을 섞으면 얼마나 되찾는지 본다.
#
# 커뮤니티 추천은 지금(2026년) 받은 것이라, 정답 시점보다 나중 투표도 섞여 있다 → 나온 지 1~4년 된 작품
# (지금 me;nu 의 2022~2025년 작품)과 비슷한 조건. 이번 분기 신작은 투표가 적어서 효과가 이보다 작을 수 있다.
#
#   python -m model.eval.new_anime_test          # 기록 전체 + 신규 유저, 커뮤니티 추천 비중은 기록 전체로 고름
#   python -m model.eval.new_anime_test cold     # 신규 유저 설정의 비중만 따로 고름

import csv
import json
import math
import sys
import time

import numpy as np

from model.algo import anime_ranker as ar
from model.eval import next_anime_test as t

NEW_FROM, NEW_TO = "2020-01-01", "2022-01-01"   # 이 기간에 시작한 작품 = 신작으로 침 (정답 기간과 같음)
DELTAS = (0.0, 0.25, 0.5, 1.0, 2.0)             # 커뮤니티 추천 비중 후보 (연습 유저로 고름)
LINKS = t.ROOT / "anilist" / "links.jsonl"
ENTRIES = t.ROOT / "menu_work_entries.csv"       # AniList 번호 → MAL 번호 (make_work_catalog.py)


def load_rec_edges():
    # (MAL 작품 a, 추천 작품 b, 추천 수). AniList 번호를 MAL 번호로 바꾼다 (MAL 번호가 없는 작품은 뺌)
    mal = {}
    with open(ENTRIES, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["mal_id"]:
                mal[int(r["anilist_id"])] = int(r["mal_id"])
    edges = []
    with open(LINKS, encoding="utf-8") as f:
        for line in f:
            for m in json.loads(line)["media"]:
                for node in (m.get("recommendations") or {}).get("nodes", []):
                    b = (node.get("mediaRecommendation") or {}).get("id")
                    if m["id"] in mal and b in mal:
                        edges.append((mal[m["id"]], mal[b], node.get("rating") or 0))
    return edges


def masked(ctx, new_ids):
    # 신작의 같이 본 기록을 지운 문맥 (줄·칸을 0으로 → 유사도·EASE 다시 계산). 나머지는 원래 문맥과 같이 씀
    pos = ctx["cw_pos"][ar.rows_of(ctx, sorted(new_ids))]
    pos = pos[pos >= 0]
    C = ctx["cw_count"].copy()
    C[pos, :] = 0
    C[:, pos] = 0
    out = {**ctx, "cw_count": C, "cw_sim": ar.cowatch_sims(C, ctx["cw_users"])}
    ar.build_ease(out)
    return out, len(pos)


def case_hits(cases, ctx, model):
    # 문제마다 (시리즈 맞힘, 시리즈 NDCG). next_anime_test.evaluate 와 같은 규칙
    ser = lambda a: ctx["series"].get(a, a)
    out = []
    for case in cases:
        user = {"history": case["history"], "t": case["t"]}
        for chips in ("genres", "themes"):
            if chips in case:
                user[chips] = case[chips]
        recs = [ser(a) for a in ar.pick_top(model(user, ctx), user, ctx)]
        hit = ser(case["target"]) in recs
        out.append((hit, 1 / math.log2(recs.index(ser(case["target"])) + 2) if hit else 0.0))
    return np.array(out)


def with_delta(delta):
    # v0.5 그대로 + 커뮤니티 추천 비중 delta (기존·신규 유저 설정 둘 다)
    def model(user, ctx):
        ar.WARM["rec"], ar.COLD["rec"] = delta, delta
        try:
            return ar.next_auto(user, ctx)
        finally:
            ar.WARM.pop("rec"), ar.COLD.pop("rec")
    return model


COLD_DELTAS = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0)   # 신규 유저 설정의 커뮤니티 추천 비중 후보


def cold_test():
    # 신규 유저(작품 1개 + 장르 3 + 테마 3) 설정에서만 커뮤니티 추천 비중을 따로 고른다. me;nu 유저는 처음엔 모두 신규 유저 설정
    t0 = time.time()
    anime, data = t.load_anime(), t.load_interactions()
    cases = t.make_cases(data)
    anilist = t.load_anilist()
    stats = t.stats_histories(data, cases)
    ctx = t.build_context(cases, anime, t.load_series(anime), anilist, data=data, stats=stats)
    ar.build_cowatch(ctx, stats)
    ar.build_ease(ctx)
    ar.build_rec(ctx, load_rec_edges())
    new_ids = {a for a, v in anime.items() if v["start_date"] and NEW_FROM <= v["start_date"] < NEW_TO}
    new_ctx, _ = masked(ctx, new_ids)
    ser = lambda a: ctx["series"].get(a, a)
    old_series = {ser(a) for a, v in anime.items() if v["start_date"] and v["start_date"] < NEW_FROM}
    onb = [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]
    tune, test = onb[: t.TUNE_USERS], onb[t.TUNE_USERS:]
    is_new = lambda cs: np.array([c["target"] in new_ids for c in cs])
    is_new_ip = lambda cs: np.array([c["target"] in new_ids and ser(c["target"]) not in old_series for c in cs])
    print(f"신규 유저 (작품 1개 + 장르 3 + 테마 3) · 연습 {len(tune):,} · 실전 {len(test):,} · 준비 {time.time() - t0:.0f}초")

    def report(label, cs, h):
        new, ip = is_new(cs), is_new_ip(cs)
        print(f"{label}\t{h[:, 0].mean():.4f}\t{h[:, 1].mean():.4f}\t{h[new, 0].mean():.4f}\t{h[ip, 0].mean():.4f}"
              f"\t({time.time() - t0:.0f}초)", flush=True)

    def cold_only(d):   # 신규 유저 설정에만 비중 d (기존 유저 설정은 그대로 0)
        def model(user, c):
            ar.COLD["rec"] = d
            try:
                return ar.next_auto(user, c)
            finally:
                ar.COLD.pop("rec")
        return model

    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP"
    print(f"\n[연습 · 신작 같이 본 기록 지움 · 비중 고르기]\n{head}")
    best, best_hit = 0.0, -1.0
    for d in COLD_DELTAS:
        h = case_hits(tune, new_ctx, cold_only(d))
        report(f"비중 {d}", tune, h)
        if h[:, 0].mean() > best_hit + 1e-9:
            best, best_hit = d, h[:, 0].mean()
    print(f"→ 고른 비중 {best}\n\n[실전]\n{head}")
    report("신작 기록 없음 · v0.5", test, case_hits(test, new_ctx, cold_only(0.0)))
    report(f"신작 기록 없음 · 커뮤니티 추천 {best}", test, case_hits(test, new_ctx, cold_only(best)))
    report("기록 다 있음 · v0.5", test, case_hits(test, ctx, cold_only(0.0)))
    report(f"기록 다 있음 · 커뮤니티 추천 {best}", test, case_hits(test, ctx, cold_only(best)))


def main():
    if sys.argv[1:2] == ["cold"]:
        return cold_test()
    t0 = time.time()
    anime, data = t.load_anime(), t.load_interactions()
    cases = t.make_cases(data)
    anilist = t.load_anilist()
    stats = t.stats_histories(data, cases)
    ctx = t.build_context(cases, anime, t.load_series(anime), anilist, data=data, stats=stats)
    ar.build_cowatch(ctx, stats)
    ar.build_ease(ctx)
    edges = load_rec_edges()
    ar.build_rec(ctx, edges)

    new_ids = {a for a, v in anime.items() if v["start_date"] and NEW_FROM <= v["start_date"] < NEW_TO}
    new_ctx, n_masked = masked(ctx, new_ids)
    ser = lambda a: ctx["series"].get(a, a)
    old_series = {ser(a) for a, v in anime.items() if v["start_date"] and v["start_date"] < NEW_FROM}
    tune, test = cases[: t.TUNE_USERS], cases[t.TUNE_USERS:]
    is_new = lambda cs: np.array([c["target"] in new_ids for c in cs])
    is_new_ip = lambda cs: np.array([c["target"] in new_ids and ser(c["target"]) not in old_series for c in cs])
    print(f"채점 {len(cases):,}명 (연습 {len(tune):,} · 실전 {len(test):,}) · 커뮤니티 추천 연결 {len(edges):,}개 · "
          f"추천 있는 작품 {len(ctx['rec']):,}편 · 신작 {len(new_ids):,}편 중 같이 본 표 안 {n_masked:,}편 지움 "
          f"· 준비 {time.time() - t0:.0f}초")
    print(f"정답이 신작인 문제: 연습 {is_new(tune).mean():.1%} · 실전 {is_new(test).mean():.1%} "
          f"(그중 처음 나온 시리즈 = 신규 IP: 실전 {is_new_ip(test).sum() / max(is_new(test).sum(), 1):.0%})\n")

    def report(label, cs, h):
        new, ip = is_new(cs), is_new_ip(cs)
        print(f"{label}\t{h[:, 0].mean():.4f}\t{h[:, 1].mean():.4f}\t{h[new, 0].mean():.4f}\t{h[ip, 0].mean():.4f}"
              f"\t({time.time() - t0:.0f}초)", flush=True)

    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP"
    print(f"[연습 {len(tune):,}명 · 신작 같이 본 기록 지움 · 커뮤니티 추천 비중 고르기]\n{head}")
    best, best_hit = 0.0, -1.0
    for d in DELTAS:
        h = case_hits(tune, new_ctx, with_delta(d))
        report(f"비중 {d}", tune, h)
        if h[:, 0].mean() > best_hit + 1e-9:
            best, best_hit = d, h[:, 0].mean()
    print(f"→ 고른 비중 {best}\n")

    print(f"[실전 {len(test):,}명 · 기록 전체]\n{head}")
    report("A. v0.5 · 같이 본 기록 다 있음 (참고: 신작 공백 없을 때)", test, case_hits(test, ctx, with_delta(0.0)))
    report("B. v0.5 · 신작 같이 본 기록 없음 (지금 me;nu 상황)", test, case_hits(test, new_ctx, with_delta(0.0)))
    report(f"C. B + 커뮤니티 추천 {best}", test, case_hits(test, new_ctx, with_delta(best)))
    report(f"D. A + 커뮤니티 추천 {best}", test, case_hits(test, ctx, with_delta(best)))

    onb = [t.onboarding(c, anime, 1, 3, anilist, 3) for c in test]
    print(f"\n[실전 {len(test):,}명 · 신규 유저 (작품 1개 + 장르 3 + 테마 3) · 신작 같이 본 기록 없음]\n{head}")
    report("B. v0.5", onb, case_hits(onb, new_ctx, with_delta(0.0)))
    report(f"C. + 커뮤니티 추천 {best}", onb, case_hits(onb, new_ctx, with_delta(best)))


if __name__ == "__main__":
    main()
