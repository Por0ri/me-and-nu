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
#   python -m model.eval.new_anime_test fill     # 기록 없는 작품의 점수를 0점 대신 채우기 (EXP-23)
#   python -m model.eval.new_anime_test input    # 유저가 고른 신작도 비슷한 작품으로 이어서 계산 (1-1b)
#   python -m model.eval.new_anime_test ease     # 위를 기록 많은 유저 설정에만 켠 그대로 다시 재고 짝지어 확인 (EXP-24)
#   python -m model.eval.new_anime_test trend    # 신규 유저 설정에 최근 30일 인기(트렌딩)를 더 섞기 (EXP-25)
#   python -m model.eval.new_anime_test people   # 작품 특징에 스튜디오·핵심 제작진·주연 성우 더하기 (EXP-26, 1-4 0단계)
#   python -m model.eval.new_anime_test text     # 줄거리 임베딩으로 빌려 오기 이웃 · 취향 보강 (EXP-27, 1-4b. embed_descriptions.py 먼저)
#   python -m model.eval.new_anime_test neighbors  # 방식별 비슷한 작품이 실제로 같이 본 작품인지 (EXP-27 원인 확인)
#   python -m model.eval.new_anime_test learn    # 작품 정보 → 같이 본 패턴 학습으로 빌려 오기 이웃 찾기 (EXP-28, 1-4b2)
#   python -m model.eval.new_anime_test age      # 같이 보는 패턴이 옛날 작품에도 도움이 되나 (정답 작품 나이별)

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


FILLS = [("0점 (지금)", None, None), ("중립값 (중앙값)", "median", None), ("빌려 오기 · 태그", "borrow", ("tag",)),
         ("빌려 오기 · 커뮤니티 추천", "borrow", ("rec",)), ("빌려 오기 · 태그 + 커뮤니티 추천", "borrow", ("tag", "rec"))]


def fill_test():
    # EXP-23: 기록 없는 작품(신작)의 행동 기반 점수를 0점 대신 채우면 옛날 작품 쏠림이 풀리는가
    # 고르는 기준 (미리 정함): 연습 5,000명 시리즈 Hit@10 최고. 바꾸는 건 실전에서도 전체·신작이 같이 오를 때만
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
    is_new = lambda cs: np.array([c["target"] in new_ids for c in cs])
    is_ip = lambda cs: np.array([c["target"] in new_ids and ser(c["target"]) not in old_series for c in cs])
    print(f"신작 기록 없음 · 연습 {t.TUNE_USERS:,} · 실전 {len(cases) - t.TUNE_USERS:,} · 준비 {time.time() - t0:.0f}초")

    def run(cs, fill, use):
        old, ar.CF_FILL = ar.CF_FILL, fill
        if fill == "borrow":
            ar.build_cf_borrow(new_ctx, new_ids, use=use)
        elif fill == "median":
            ar.build_cf_borrow(new_ctx, new_ids, use=("tag",))   # cf_missing 표시만 쓰임
        try:
            h = case_hits(cs, new_ctx, ar.next_auto)
            n_new = np.mean([sum(a in new_ids for a in ar.pick_top(ar.next_auto({"history": c["history"], "t": c["t"]}, new_ctx),
                                                                      {"history": c["history"], "t": c["t"]}, new_ctx))
                             for c in cs[:2000]])
        finally:
            ar.CF_FILL = old
        return h, n_new

    def report(label, cs, h, n_new):
        new, ip = is_new(cs), is_ip(cs)
        print(f"{label}\t{h[:, 0].mean():.4f}\t{h[:, 1].mean():.4f}\t{h[new, 0].mean():.4f}\t{h[ip, 0].mean():.4f}\t{n_new:.2f}"
              f"\t({time.time() - t0:.0f}초)", flush=True)

    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP\t추천 10개 중 신작"
    onb = [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]
    for scen, cs in (("기록 전체", cases), ("신규 유저 (작품 1개 + 장르 3 + 테마 3)", onb)):
        tune, test = cs[: t.TUNE_USERS], cs[t.TUNE_USERS:]
        print(f"\n[{scen} · 연습]\n{head}")
        best, best_hit = FILLS[0], -1.0
        for f in FILLS:
            h, n_new = run(tune, f[1], f[2])
            report(f[0], tune, h, n_new)
            if h[:, 0].mean() > best_hit + 1e-9:
                best, best_hit = f, h[:, 0].mean()
        print(f"→ 고른 방법: {best[0]}\n[{scen} · 실전]\n{head}")
        report(FILLS[0][0], test, *run(test, None, None))
        if best is not FILLS[0]:
            report(best[0], test, *run(test, best[1], best[2]))


ALL_KINDS = ("confidence", "confidence_max", "cosine", "lift", "ease")   # EXP-24: 기존·신규 유저 설정 모두


def input_test():
    # 1-1b: 유저가 본(고른) 작품 중 기록 없는 신작도 비슷한 옛날 작품으로 이어서 계산하면 신규 유저도 오르는가
    # 후보 쪽 빌려 오기(EXP-23, 태그 + 커뮤니티 추천)는 켠 상태에서 비교. 고를 값이 없어서 연습에서 먼저 보고 실전으로 확인
    # 채택 기준 (미리 정함): 연습·실전 둘 다 시리즈 Hit@10 과 정답이 신작인 문제가 같이 오르고 NDCG 가 안 떨어질 때
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
    ar.build_cf_borrow(new_ctx, new_ids, use=("tag", "rec"))
    ar.CF_FILL = "borrow"
    ser = lambda a: ctx["series"].get(a, a)
    old_series = {ser(a) for a, v in anime.items() if v["start_date"] and v["start_date"] < NEW_FROM}
    is_new = lambda cs: np.array([c["target"] in new_ids for c in cs])
    is_ip = lambda cs: np.array([c["target"] in new_ids and ser(c["target"]) not in old_series for c in cs])
    print(f"신작 기록 없음 · 후보 쪽 빌려 오기 켬 · 준비 {time.time() - t0:.0f}초")

    def run(cs, mode):
        old, ar.CF_INPUT = ar.CF_INPUT, mode
        try:
            h = case_hits(cs, new_ctx, ar.next_auto)
            n_new = np.mean([sum(a in new_ids for a in ar.pick_top(ar.next_auto(u, new_ctx), u, new_ctx))
                             for u in ({"history": c["history"], "t": c["t"], **{k: c[k] for k in ("genres", "themes") if k in c}}
                                       for c in cs[:1000])])
        finally:
            ar.CF_INPUT = old
        new, ip = is_new(cs), is_ip(cs)
        return f"{h[:, 0].mean():.4f}\t{h[:, 1].mean():.4f}\t{h[new, 0].mean():.4f}\t{h[ip, 0].mean():.4f}\t{n_new:.2f}"

    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP\t추천 10개 중 신작 (앞 1,000명)"
    scenes = [("신규 유저 · 작품 1개 + 장르 3 + 테마 3", [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]),
              ("신규 유저 · 작품 3개 + 장르 3 + 테마 3", [t.onboarding(c, anime, 3, 3, anilist, 3) for c in cases]),
              ("기록 전체", cases)]
    for scen, cs in scenes:
        for label, part in (("연습", cs[: t.TUNE_USERS]), ("실전", cs[t.TUNE_USERS:])):
            print(f"\n[{scen} · {label} {len(part):,}명]\n{head}")
            print(f"고른 작품은 그대로\t{run(part, None)}\t({time.time() - t0:.0f}초)", flush=True)
            print(f"고른 신작도 이어서\t{run(part, ALL_KINDS)}\t({time.time() - t0:.0f}초)", flush=True)


def paired(h0, h1):
    # 같은 문제끼리 짝지어 비교 (차이가 우연인지 확인). 맞힘: 새로 맞힌 문제 − 놓친 문제, 95% 범위, 우연일 확률 (McNemar)
    # NDCG: 문제별 차이의 평균이 0인지 (짝지은 t 검정. 문제가 많아서 정규분포로 봄)
    a, b = h0[:, 0] > 0, h1[:, 0] > 0
    gain, loss, n = int((b & ~a).sum()), int((a & ~b).sum()), len(a)
    diff = (gain - loss) / n
    se = math.sqrt(max(gain + loss - (gain - loss) ** 2 / n, 0)) / n
    z = (abs(gain - loss) - 1) / math.sqrt(gain + loss) if gain + loss else 0.0
    d = h1[:, 1] - h0[:, 1]
    zn = d.mean() / (d.std(ddof=1) / math.sqrt(n)) if d.std() > 0 else 0.0
    return (f"새로 맞힘 {gain:,} · 놓침 {loss:,} → {diff * 100:+.2f}%p (95% 범위 {(diff - 1.96 * se) * 100:+.2f} ~ "
            f"{(diff + 1.96 * se) * 100:+.2f}%p, 우연일 확률 {math.erfc(max(z, 0) / math.sqrt(2)):.3f}) · "
            f"NDCG {d.mean():+.4f} (우연일 확률 {math.erfc(abs(zn) / math.sqrt(2)):.3f})")


def prepare():
    # 신작 흉내 문맥: 신작 같이 본 기록 지움 + 지금 채택된 빌려 오기 켬 (EXP-28부터 학습 이웃 + 커뮤니티 추천)
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
    ar.build_cf_borrow(new_ctx, new_ids)
    ser = lambda a: ctx["series"].get(a, a)
    old_series = {ser(a) for a, v in anime.items() if v["start_date"] and v["start_date"] < NEW_FROM}
    is_new = lambda cs: np.array([c["target"] in new_ids for c in cs])
    is_ip = lambda cs: np.array([c["target"] in new_ids and ser(c["target"]) not in old_series for c in cs])
    return anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip


def measure(cs, c, model, new_ids, is_new, is_ip):
    # 문제별 맞힘과 한 줄 요약 (시리즈 Hit@10 · NDCG · 정답이 신작 · 정답이 신규 IP · 추천 10개 중 신작 (앞 1,000명))
    h = case_hits(cs, c, model)
    users = ({"history": x["history"], "t": x["t"], **{k: x[k] for k in ("genres", "themes") if k in x}} for x in cs[:1000])
    n_new = np.mean([sum(a in new_ids for a in ar.pick_top(model(u, c), u, c)) for u in users])
    new, ip = is_new(cs), is_ip(cs)
    return h, f"{h[:, 0].mean():.4f}\t{h[:, 1].mean():.4f}\t{h[new, 0].mean():.4f}\t{h[ip, 0].mean():.4f}\t{n_new:.2f}"


def ease_check():
    # EXP-24 확인: 채택할 설정 그대로(기록 많은 유저 설정의 EASE 에만 CF_INPUT) 연습·실전을 다시 재고, 짝지어 우연인지 본다
    # "근거가 명확" (미리 정함): 연습·실전 모두 시리즈 Hit@10·NDCG·정답이 신작이 오르고, 실전 짝 비교에서 우연일 확률 0.05 미만
    t0 = time.time()
    anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip = prepare()
    print(f"신작 기록 없음 · 후보 쪽 빌려 오기 켬 · 기록 전체 · 준비 {time.time() - t0:.0f}초")
    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP\t추천 10개 중 신작 (앞 1,000명)"
    for label, part in (("연습", cases[: t.TUNE_USERS]), ("실전", cases[t.TUNE_USERS:])):
        print(f"\n[{label} {len(part):,}명]\n{head}")
        hs = []
        for name, mode in (("고른 작품 그대로", None), ("기록 많은 유저 설정만 이어서", ("ease",))):
            old, ar.CF_INPUT = ar.CF_INPUT, mode
            try:
                h, row = measure(part, new_ctx, ar.next_auto, new_ids, is_new, is_ip)
            finally:
                ar.CF_INPUT = old
            hs.append(h)
            print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
        print(f"짝 비교: {paired(*hs)}", flush=True)


TRENDS = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0)   # 2-1: 신규 유저 설정에 더 섞을 최근 30일 인기 비중 후보


def with_trend(w, replace=False):
    # 신규 유저 설정에만 최근 30일 인기 비중 w. replace 면 섞지 않고 전체 기간 인기를 최근 30일 인기로 바꿈 (참고용)
    def model(user, c):
        old = dict(ar.COLD)
        if replace:
            ar.COLD["kind"] = "recent"
        ar.COLD["trend"] = w
        try:
            return ar.next_auto(user, c)
        finally:
            ar.COLD.clear()
            ar.COLD.update(old)
    return model


def trend_test():
    # 2-1: 신규 유저 설정(전체 기간 인기 β=1)에 최근 30일 인기(트렌딩)를 더 섞으면 신작·신규 IP 가 올라오는가
    # 고르는 기준 (미리 정함): 연습 5,000명 (작품 1개 + 칩 · 신작 기록 없음) 시리즈 Hit@10 최고
    # 채택 기준 (미리 정함): 실전에서 시리즈 Hit@10·NDCG·정답이 신작이 같이 오르고 짝 비교 우연일 확률 0.05 미만.
    #   작품 3개 + 칩, 기록 다 있는 상황에서도 안 떨어질 때. 신규 IP 만 오르고 전체가 떨어지면 안 바꿈
    t0 = time.time()
    anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip = prepare()
    onb1 = [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]
    onb3 = [t.onboarding(c, anime, 3, 3, anilist, 3) for c in cases]
    print(f"신작 기록 없음 · 후보 쪽 빌려 오기 켬 · 준비 {time.time() - t0:.0f}초")
    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP\t추천 10개 중 신작 (앞 1,000명)"
    tune = onb1[: t.TUNE_USERS]
    print(f"\n[연습 {len(tune):,}명 · 신규 유저 (작품 1개 + 장르 3 + 테마 3) · 비중 고르기]\n{head}")
    variants = [(f"최근 인기 {w}", w, False) for w in TRENDS] + [("최근 인기로 바꾸기 (참고)", 0.0, True)]
    best, best_hit = variants[0], -1.0
    for v in variants:
        h, row = measure(tune, new_ctx, with_trend(v[1], v[2]), new_ids, is_new, is_ip)
        print(f"{v[0]}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
        if h[:, 0].mean() > best_hit + 1e-9:
            best, best_hit = v, h[:, 0].mean()
    print(f"→ 고른 방법: {best[0]}", flush=True)
    if best is variants[0]:
        return
    for scen, cs, c in (("신규 유저 · 작품 1개 + 칩 · 신작 기록 없음", onb1, new_ctx),
                        ("신규 유저 · 작품 3개 + 칩 · 신작 기록 없음", onb3, new_ctx),
                        ("신규 유저 · 작품 1개 + 칩 · 기록 다 있음", onb1, ctx)):
        part = cs[t.TUNE_USERS:]
        print(f"\n[실전 {len(part):,}명 · {scen}]\n{head}")
        hs = []
        for name, w, rep in (("지금 (섞기 없음)", 0.0, False), best):
            h, row = measure(part, c, with_trend(w, rep), new_ids, is_new, is_ip)
            hs.append(h)
            print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
        print(f"짝 비교: {paired(*hs)}", flush=True)


THREE = ("studio", "staff", "voice")
PEOPLE_SETS = [("장르·태그만 (지금)", {}), ("+ 스튜디오 0.5", {"studio": 0.5}), ("+ 핵심 제작진 0.5", {"staff": 0.5}),
               ("+ 주연 성우 0.5", {"voice": 0.5}), ("+ 셋 다 0.25", dict.fromkeys(THREE, 0.25)),
               ("+ 셋 다 0.5", dict.fromkeys(THREE, 0.5)), ("+ 셋 다 1", dict.fromkeys(THREE, 1.0))]


def use_people(weights, anilist, ctx, new_ctx, new_ids):
    # 작품 특징표를 사람 정보 비중 weights 로 다시 만들어 두 문맥에 넣고, 빌려 오기 이웃도 새 특징으로 다시 고른다
    ar.PEOPLE.clear()
    ar.PEOPLE.update(weights)
    ar.build_features(ctx, anilist)
    for k in ("X", "vnorm", "feature_col", "feat_rows", "Xf"):
        new_ctx[k] = ctx[k]
    ar.build_cf_borrow(new_ctx, new_ids, use=("tag", "rec"))


def people_test():
    # 1-4 0단계 (EXP-26): 작품 특징(장르·태그)에 스튜디오·핵심 제작진·주연 성우를 더하면 오르는가
    # 고르는 기준 (미리 정함): 연습 5,000명 (신규 유저 · 작품 1개 + 칩 · 신작 기록 없음) 시리즈 Hit@10 최고
    # 채택 기준 (미리 정함): 실전에서 시리즈 Hit@10·NDCG·정답이 신작이 같이 오르고 짝 비교 우연일 확률 0.05 미만.
    #   작품 3개 + 칩, 기록 전체(기존 유저), 기록 다 있는 신규 유저에서도 안 떨어질 때
    t0 = time.time()
    anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip = prepare()
    onb1 = [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]
    onb3 = [t.onboarding(c, anime, 3, 3, anilist, 3) for c in cases]
    n_people = sum(1 for m in anilist.values() if m.get("staff"))
    print(f"신작 기록 없음 · 빌려 오기 켬 · 사람 정보 있는 작품 {n_people:,}편 · 준비 {time.time() - t0:.0f}초")
    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP\t추천 10개 중 신작 (앞 1,000명)"
    tune = onb1[: t.TUNE_USERS]
    print(f"\n[연습 {len(tune):,}명 · 신규 유저 (작품 1개 + 장르 3 + 테마 3) · 사람 정보 고르기]\n{head}")
    best, best_hit = PEOPLE_SETS[0], -1.0
    for name, w in PEOPLE_SETS:
        use_people(w, anilist, ctx, new_ctx, new_ids)
        h, row = measure(tune, new_ctx, ar.next_auto, new_ids, is_new, is_ip)
        print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
        if h[:, 0].mean() > best_hit + 1e-9:
            best, best_hit = (name, w), h[:, 0].mean()
    print(f"→ 고른 방법: {best[0]}", flush=True)
    if best[0] == PEOPLE_SETS[0][0]:
        return
    for scen, cs, c in (("신규 유저 · 작품 1개 + 칩 · 신작 기록 없음", onb1, new_ctx),
                        ("신규 유저 · 작품 3개 + 칩 · 신작 기록 없음", onb3, new_ctx),
                        ("기록 전체 (기존 유저 설정) · 신작 기록 없음", cases, new_ctx),
                        ("신규 유저 · 작품 1개 + 칩 · 기록 다 있음", onb1, ctx)):
        part = cs[t.TUNE_USERS:]
        print(f"\n[실전 {len(part):,}명 · {scen}]\n{head}")
        hs = []
        for name, w in (PEOPLE_SETS[0], best):
            use_people(w, anilist, ctx, new_ctx, new_ids)
            h, row = measure(part, c, ar.next_auto, new_ids, is_new, is_ip)
            hs.append(h)
            print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
        print(f"짝 비교: {paired(*hs)}", flush=True)


def people_check():
    # EXP-26 확인: (1) 빠뜨린 상황 — 기존 유저 · 기록 다 있음 (대표 점수 33.06%) 에서 안 떨어지는가
    # (2) 경계값 — 연습 1등이 가장 센 값(1)이라 더 센 값(1.5 · 2 · 3)도 연습에서만 본다 (더 좋으면 따로 실험)
    t0 = time.time()
    anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip = prepare()
    onb1 = [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]
    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP\t추천 10개 중 신작 (앞 1,000명)"
    part = cases[t.TUNE_USERS:]
    print(f"[실전 {len(part):,}명 · 기록 전체 (기존 유저 설정) · 기록 다 있음]\n{head}")
    hs = []
    for name, w in (PEOPLE_SETS[0], PEOPLE_SETS[-1]):
        use_people(w, anilist, ctx, new_ctx, new_ids)
        h, row = measure(part, ctx, ar.next_auto, new_ids, is_new, is_ip)
        hs.append(h)
        print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
    print(f"짝 비교: {paired(*hs)}", flush=True)
    tune = onb1[: t.TUNE_USERS]
    print(f"\n[연습 {len(tune):,}명 · 신규 유저 (작품 1개 + 칩) · 신작 기록 없음 · 더 센 값]\n{head}")
    for v in (1.0, 1.5, 2.0, 3.0):
        use_people(dict.fromkeys(THREE, v), anilist, ctx, new_ctx, new_ids)
        h, row = measure(tune, new_ctx, ar.next_auto, new_ids, is_new, is_ip)
        print(f"+ 셋 다 {v}\t{row}\t({time.time() - t0:.0f}초)", flush=True)


TEXT_FILLS = [("작품 특징 (지금)", ("tag", "rec")), ("줄거리", ("text", "rec")), ("작품 특징 + 줄거리", ("tag", "text", "rec"))]
TEXT_TASTES = (0.0, 0.25, 0.5, 1.0)   # 줄거리 취향 비중 후보


def load_text(*ctxs):
    # 줄거리 임베딩(embed_descriptions.py) → 작품 줄 순서에 맞춘 표 ctx["T"]. 줄거리 없는 작품은 0
    # 평균 벡터를 빼고 다시 길이 1로 맞춘다 (문장 임베딩은 상관없는 글끼리도 코사인이 0.5 안팎이라 차이를 키움)
    src = t.ROOT / "anilist"
    ids = json.loads((src / "desc_emb_ids.json").read_text(encoding="utf-8"))["ids"]
    emb = np.load(src / "desc_emb.npy").astype(np.float64)
    mal_of = {}
    with open(src / "media.jsonl", encoding="utf-8") as f:
        for line in f:
            for m in json.loads(line)["media"]:
                if m["idMal"]:
                    mal_of[m["id"]] = m["idMal"]
    row = ctxs[0]["row"]
    T = np.zeros((len(ctxs[0]["ids"]), emb.shape[1]))
    for i, a in enumerate(ids):
        if mal_of.get(a) in row:
            T[row[mal_of[a]]] = emb[i]
    has = np.flatnonzero(np.abs(T).sum(axis=1) > 0)
    T[has] -= T[has].mean(axis=0)
    T[has] /= np.linalg.norm(T[has], axis=1, keepdims=True)
    for c in ctxs:
        c["T"] = T
    return len(has)


def text_test():
    # 1-4b (EXP-27): 줄거리 임베딩으로 (1) 빌려 오기 이웃 (2) 취향 점수를 보강하면 오르는가
    # 고르는 기준 (미리 정함): (1) 이웃 방법은 연습 5,000명 기존 유저 · 신작 기록 없음 Hit@10 최고 (빌려 오기 효과는 기존 유저에서 남)
    #   (2) 고른 이웃을 들고 줄거리 취향 비중은 연습 5,000명 신규 유저 (작품 1개 + 칩) · 신작 기록 없음 Hit@10 최고
    # 채택 기준 (미리 정함): 실전 신작 기록 없음의 신규 유저 · 기존 유저 중 하나 이상 오르고 (우연일 확률 0.05 미만) 둘 다 안 떨어짐,
    #   NDCG · 정답이 신작도 안 떨어짐. 작품 3개 · 기록 다 있는 상황 · 대표 점수(기존 유저 · 기록 다 있음)에서 의미 있게 안 떨어짐
    t0 = time.time()
    anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip = prepare()
    n_text = load_text(ctx, new_ctx)
    onb1 = [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]
    onb3 = [t.onboarding(c, anime, 3, 3, anilist, 3) for c in cases]
    print(f"신작 기록 없음 · 빌려 오기 켬 · 줄거리 있는 시험 작품 {n_text:,}편 · 준비 {time.time() - t0:.0f}초")
    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP\t추천 10개 중 신작 (앞 1,000명)"

    def setup(use, taste):
        ar.build_cf_borrow(new_ctx, new_ids, use=use)
        ar.TEXT_TASTE = taste

    try:
        tune = cases[: t.TUNE_USERS]
        print(f"\n[연습 {len(tune):,}명 · 기존 유저 · 신작 기록 없음 · (1) 빌려 오기 이웃]\n{head}")
        best_fill, best_hit = TEXT_FILLS[0], -1.0
        for name, use in TEXT_FILLS:
            setup(use, 0.0)
            h, row = measure(tune, new_ctx, ar.next_auto, new_ids, is_new, is_ip)
            print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
            if h[:, 0].mean() > best_hit + 1e-9:
                best_fill, best_hit = (name, use), h[:, 0].mean()
        print(f"→ 고른 이웃: {best_fill[0]}", flush=True)
        tune = onb1[: t.TUNE_USERS]
        print(f"\n[연습 {len(tune):,}명 · 신규 유저 (작품 1개 + 칩) · 신작 기록 없음 · (2) 줄거리 취향 비중]\n{head}")
        best_taste, best_hit = 0.0, -1.0
        for w in TEXT_TASTES:
            setup(best_fill[1], w)
            h, row = measure(tune, new_ctx, ar.next_auto, new_ids, is_new, is_ip)
            print(f"줄거리 취향 {w}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
            if h[:, 0].mean() > best_hit + 1e-9:
                best_taste, best_hit = w, h[:, 0].mean()
        print(f"→ 고른 비중: {best_taste}", flush=True)
        if best_fill[0] == TEXT_FILLS[0][0] and best_taste == 0.0:
            print("→ 둘 다 지금 방식이 가장 좋음 (효과 없음)")
            return
        chosen = f"{best_fill[0]} · 줄거리 취향 {best_taste}"
        scenes = [("신규 유저 · 작품 1개 + 칩 · 신작 기록 없음", onb1, new_ctx),
                  ("신규 유저 · 작품 3개 + 칩 · 신작 기록 없음", onb3, new_ctx),
                  ("기존 유저 · 신작 기록 없음", cases, new_ctx)]
        if best_taste:   # 기록 다 있는 문맥은 빌려 오기를 안 써서 줄거리 취향을 쓸 때만 달라짐
            scenes += [("신규 유저 · 작품 1개 + 칩 · 기록 다 있음", onb1, ctx), ("기존 유저 · 기록 다 있음 (대표 점수)", cases, ctx)]
        for scen, cs, c in scenes:
            part = cs[t.TUNE_USERS:]
            print(f"\n[실전 {len(part):,}명 · {scen}]\n{head}")
            hs = []
            for name, use, w in (("지금", TEXT_FILLS[0][1], 0.0), (chosen, best_fill[1], best_taste)):
                setup(use, w)
                h, row = measure(part, c, ar.next_auto, new_ids, is_new, is_ip)
                hs.append(h)
                print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
            print(f"짝 비교: {paired(*hs)}", flush=True)
    finally:
        ar.TEXT_TASTE = 0.0


def neighbor_check():
    # EXP-27 원인 확인: 방식마다 고른 "비슷한 작품 10개" 중 실제로 같이 많이 본 작품(같이 본 코사인 상위 50)이 몇 개인가
    # 같이 본 기록 · 작품 특징 · 줄거리가 다 있는 옛날 작품끼리 비교 (기록 다 있는 문맥)
    anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip = prepare()
    load_text(ctx)
    cw = ctx["cw_rows"]
    idx = np.flatnonzero((np.abs(ctx["X"][cw]).sum(axis=1) > 0) & (np.abs(ctx["T"][cw]).sum(axis=1) > 0))
    rows = cw[idx]
    C = ctx["cw_sim"]["cosine"][np.ix_(idx, idx)].astype(np.float64)
    np.fill_diagonal(C, -9)
    k, g = 10, 50
    truth = [set(r) for r in np.argpartition(-C, g, axis=1)[:, :g]]

    def overlap(S):
        np.fill_diagonal(S, -9)
        top = np.argpartition(-S, k, axis=1)[:, :k]
        return np.mean([len(set(top[i]) & truth[i]) for i in range(len(idx))])

    def feat():
        X = ctx["X"][rows] / ctx["vnorm"][rows, None]
        return X @ X.T

    print(f"비교 작품 {len(idx):,}편 · 무작위로 고르면 {k * g / len(idx):.2f}개 / {k}")
    print(f"작품 특징 (장르·태그 + 사람 정보)\t{overlap(feat()):.2f}")
    use_people({}, anilist, ctx, new_ctx, new_ids)
    print(f"장르·태그만\t{overlap(feat()):.2f}")
    T = ctx["T"][rows]
    print(f"줄거리 임베딩\t{overlap(T @ T.T):.2f}")
    pos = {r: i for i, r in enumerate(rows.tolist())}
    hits = []
    for i, r in enumerate(rows.tolist()):
        ys = [pos[y] for y in np.asarray(ctx.get("rec", {}).get(r, ((), ()))[0]).tolist() if y in pos][:k]
        if ys:
            hits.append(len(set(ys) & truth[i]) / len(ys) * k)
    print(f"커뮤니티 추천 (추천 있는 {len(hits):,}편, {k}개로 환산)\t{np.mean(hits):.2f}")


LEARN_FEATS = [("특징", ("feat",)), ("줄거리", ("text",)), ("특징 + 줄거리", ("feat", "text"))]
LEARN_LAMS = (0.1, 1.0, 10.0)
LEARN_FILLS = [("작품 특징 (지금)", ("tag", "rec")), ("학습", ("learn", "rec")), ("작품 특징 + 학습", ("tag", "learn", "rec"))]


def learn_test():
    # 1-4b2 (EXP-28): 작품 정보 → 같이 본 패턴을 학습해서 기록 없는 신작의 빌려 오기 이웃을 찾으면 오르는가
    # 1단계 (작품끼리): 기록 있는 작품을 5묶음으로 나눠 한 묶음씩 신작처럼 가리고 나머지로 학습 → 가린 작품의 이웃 10개 중
    #   실제로 같이 많이 본 작품(같이 본 코사인 상위 50) 수. 고르는 기준 (미리 정함): 평균 최고 설정. 원래 특징 코사인보다 못하면 끝
    # 2단계 (유저): 고르는 기준 = 연습 5,000명 기존 유저 · 신작 기록 없음 Hit@10 최고 (지금 / 학습 / 둘 다)
    #   채택 기준 = 실전 기존 유저 · 신작 기록 없음 Hit@10 오르고 우연일 확률 0.05 미만, NDCG · 정답이 신작 안 떨어짐,
    #   신규 유저 (작품 1개 · 3개 + 칩) 에서 의미 있게 안 떨어짐. 대표 점수는 빌려 오기를 안 써서 그대로
    t0 = time.time()
    anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip = prepare()
    load_text(ctx, new_ctx)
    cw = ctx["cw_rows"]
    rows = cw[(np.abs(ctx["X"][cw]).sum(axis=1) > 0) & (np.abs(ctx["T"][cw]).sum(axis=1) > 0)]
    fold = np.random.default_rng(0).permutation(len(rows)) % 5
    k, g = 10, 50
    Xn = lambda r: ctx["X"][r] / ctx["vnorm"][r, None]
    scores = {}
    for f in range(5):
        tr, te = rows[fold != f], rows[fold == f]
        C = ctx["cw_sim"]["cosine"][np.ix_(ctx["cw_pos"][te], ctx["cw_pos"][tr])].astype(np.float64)
        truth = [set(r) for r in np.argpartition(-C, g, axis=1)[:, :g]]

        def overlap(S):
            top = np.argpartition(-S, k, axis=1)[:, :k]
            return np.mean([len(set(top[i]) & truth[i]) for i in range(len(te))])

        scores.setdefault("원래 특징 코사인 (지금)", []).append(overlap(Xn(te) @ Xn(tr).T))
        scores.setdefault("원래 줄거리 코사인", []).append(overlap(ctx["T"][te] @ ctx["T"][tr].T))
        for name, feats in LEARN_FEATS:
            for lam in LEARN_LAMS:
                scores.setdefault((name, feats, lam), []).append(overlap(ar.learned_sims(ctx, te, tr, feats, lam)))
        print(f"  {f + 1}/5묶음 ({time.time() - t0:.0f}초)", flush=True)
    print(f"\n[1단계 · 작품 {len(rows):,}편 · 5묶음 · 가린 작품의 이웃 10개 중 같이 많이 본 작품 수 (평균 ± 묶음 간 표준편차)]")
    for key, v in scores.items():
        label = key if isinstance(key, str) else f"학습 · {key[0]} · 세기 {key[2]}"
        print(f"{label}\t{np.mean(v):.2f} ± {np.std(v):.2f}")
    base = np.mean(scores["원래 특징 코사인 (지금)"])
    best = max((key for key in scores if not isinstance(key, str)), key=lambda key: np.mean(scores[key]))
    print(f"→ 고른 학습: {best[0]} · 세기 {best[2]} ({np.mean(scores[best]):.2f}개, 지금 {base:.2f}개)", flush=True)
    if np.mean(scores[best]) <= base:
        print("→ 학습이 원래 특징 코사인보다 못함 (2단계 안 함)")
        return
    old = dict(ar.LEARN)
    ar.LEARN.update(feats=best[1], lam=best[2])
    try:
        head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 신작\t정답이 신규 IP\t추천 10개 중 신작 (앞 1,000명)"
        tune = cases[: t.TUNE_USERS]
        print(f"\n[2단계 · 연습 {len(tune):,}명 · 기존 유저 · 신작 기록 없음 · 빌려 오기 이웃]\n{head}")
        best_fill, best_hit = LEARN_FILLS[0], -1.0
        for name, use in LEARN_FILLS:
            ar.build_cf_borrow(new_ctx, new_ids, use=use)
            h, row = measure(tune, new_ctx, ar.next_auto, new_ids, is_new, is_ip)
            print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
            if h[:, 0].mean() > best_hit + 1e-9:
                best_fill, best_hit = (name, use), h[:, 0].mean()
        print(f"→ 고른 이웃: {best_fill[0]}", flush=True)
        if best_fill[0] == LEARN_FILLS[0][0]:
            print("→ 지금 방식이 가장 좋음 (실전 안 함)")
            return
        onb1 = [t.onboarding(c, anime, 1, 3, anilist, 3) for c in cases]
        onb3 = [t.onboarding(c, anime, 3, 3, anilist, 3) for c in cases]
        for scen, cs in (("기존 유저 · 신작 기록 없음", cases), ("신규 유저 · 작품 1개 + 칩 · 신작 기록 없음", onb1),
                         ("신규 유저 · 작품 3개 + 칩 · 신작 기록 없음", onb3)):
            part = cs[t.TUNE_USERS:]
            print(f"\n[실전 {len(part):,}명 · {scen}]\n{head}")
            hs = []
            for name, use in (LEARN_FILLS[0], best_fill):
                ar.build_cf_borrow(new_ctx, new_ids, use=use)
                h, row = measure(part, new_ctx, ar.next_auto, new_ids, is_new, is_ip)
                hs.append(h)
                print(f"{name}\t{row}\t({time.time() - t0:.0f}초)", flush=True)
            print(f"짝 비교: {paired(*hs)}", flush=True)
    finally:
        ar.LEARN.clear()
        ar.LEARN.update(old)


def cowatch_by_age():
    # 분석: 같이 보는 패턴(EASE · 같이 본 작품)은 옛날 작품을 나중에 따라잡아 볼 때도 도움이 되나 (실전 · 기록 다 있음)
    # 정답 작품의 시작 연도로 나눠, 같이 보는 패턴을 켰을 때와 껐을 때(γ = 0)를 짝 비교
    anime, anilist, cases, ctx, new_ctx, new_ids, is_new, is_ip = prepare()
    test = cases[t.TUNE_USERS:]
    start = lambda c: anime[c["target"]]["start_date"] or "9999"
    groups = [("정답이 2017년 전 시작한 작품", [c for c in test if start(c) < "2017-01-01"]),
              ("정답이 2017~2019년 작품", [c for c in test if "2017-01-01" <= start(c) < "2020-01-01"]),
              ("정답이 2020~2021년 작품", [c for c in test if start(c) >= "2020-01-01"])]
    for name, cs in groups:
        on = case_hits(cs, ctx, ar.next_auto)
        old = ar.WARM["gamma"], ar.COLD["gamma"]
        ar.WARM["gamma"] = ar.COLD["gamma"] = 0.0
        try:
            off = case_hits(cs, ctx, ar.next_auto)
        finally:
            ar.WARM["gamma"], ar.COLD["gamma"] = old
        print(f"{name} · {len(cs):,}문제 · 끔 {off[:, 0].mean():.2%} → 켬 {on[:, 0].mean():.2%} · {paired(off, on)}", flush=True)


def main():
    if sys.argv[1:2] == ["cold"]:
        return cold_test()
    if sys.argv[1:2] == ["input"]:
        return input_test()
    if sys.argv[1:2] == ["ease"]:
        return ease_check()
    if sys.argv[1:2] == ["trend"]:
        return trend_test()
    if sys.argv[1:2] == ["people"]:
        return people_test()
    if sys.argv[1:2] == ["people_check"]:
        return people_check()
    if sys.argv[1:2] == ["text"]:
        return text_test()
    if sys.argv[1:2] == ["neighbors"]:
        return neighbor_check()
    if sys.argv[1:2] == ["learn"]:
        return learn_test()
    if sys.argv[1:2] == ["age"]:
        return cowatch_by_age()
    if sys.argv[1:2] == ["fill"]:
        return fill_test()
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
