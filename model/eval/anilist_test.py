# anilist_test — AniList 유저 목록으로 만든 최근 시험지 (EXPERIMENTS 4장 1-3)
#
# MAL 시험지(next_anime_test)는 정답이 2020~2021년이라 2022년 이후 작품을 못 잰다. 같은 규칙으로
# AniList 유저 표본(fetch_anilist_lists.py)에서 2026년 정답 시험지를 만들고, 서비스 문맥(service.py)으로 잰다.
#
# 정답: 유저마다 기준일(CUTOFF) 전에 마지막으로 다 본 작품 (TARGET_FROM 이후, 우리 작품 목록 안)
#   - 완료 날짜(년월일)가 있고, 그날 다 본 작품이 그것 하나뿐인 날만 (여러 편을 같은 날로 적은 건 목록을 몰아서 정리한 날.
#     시범 100명의 다 본 기록 58%가 하루 4편 넘게 적힌 날)
#   - 기준일 2026-09-01: 수집(2026-10) 직전 두 달은 기록이 몰려서 뺌 (MAL 시험지 EXP-12와 같은 이유)
# 기록 시각: 완료일 → 시작일 → 마지막 수정 시각 중 있는 것 (셋 다 없으면 0 = 오래된 기록). 정답 날 전 기록만 남김
# 통계: 인기 · 같이 본 작품은 시험 유저의 정답 전 기록 + 나머지 유저의 기준일 전 기록으로 셈 (next_anime_test 와 같음).
#   최근 30일 인기는 서비스와 같이 AniList 표본의 마지막 수정 시각 (정답 시각 전 30일만 봄)
#
#   python -m model.eval.anilist_test info       # 시험지 정보 (문제 수 · 목록 밖 정답 · 정답 쏠림)
#   python -m model.eval.anilist_test cowatch    # 같이 본 작품 재료 비교: MAL (지금 서비스) / AniList 표본 / 둘 다
#   python -m model.eval.anilist_test people both  # 사람 정보 빼 보기 (같이 본 작품 재료: mal · anilist · both)

import csv
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import numpy as np

from model.algo import anime_ranker as ar
from model.algo import service
from model.eval import next_anime_test as t
from model.eval.new_anime_test import case_hits, paired

CUTOFF = date(2026, 9, 1)          # 정답 · 통계 기준일 (이날 전 기록만)
TARGET_FROM = date(2026, 1, 1)     # 정답이 이날보다 오래되면 뺌
TUNE_SHARE = 0.3                   # 유저 키 순서로 앞 30%는 연습, 나머지는 실전
STATUS = {"COMPLETED": ar.STATUS["completed"], "CURRENT": ar.STATUS["watching"], "REPEATING": ar.STATUS["watching"],
          "DROPPED": ar.STATUS["dropped"], "PAUSED": ar.STATUS["on_hold"]}


def secs(d):
    # "YYYY-MM-DD" (모르는 칸은 00) → 초. 년월일이 다 있어야 함
    if len(d) != 10 or d[5:7] == "00" or d[8:10] == "00":
        return None
    try:
        return ar.to_seconds(datetime(int(d[:4]), int(d[5:7]), int(d[8:10])))
    except ValueError:
        return None


def day_secs(d):
    return ar.to_seconds(datetime(d.year, d.month, d.day))


def load_users():
    """AniList 표본 → 유저 키 → 칸별 배열. t = 기록 시각, done = 완료일 (없으면 -1)"""
    rows = defaultdict(list)
    with open(service.ANILIST_USERS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            u = r.get("updated_at") or ""
            if not u.isdigit() or r["status"] not in STATUS:      # 받는 중이면 마지막 줄이 덜 써졌을 수 있음
                continue
            done = secs(r["completed"]) if r["status"] == "COMPLETED" else None
            when = done or secs(r["started"]) or int(u)
            rows[r["user_key"]].append((int(r["media_id"]), STATUS[r["status"]], int(r["score"] or 0), when,
                                        -1 if done is None else done))
    users = {}
    for k in sorted(rows):
        a = np.array(rows[k], dtype=np.int64)
        users[k] = {"anime": a[:, 0], "status": a[:, 1].astype(np.int8), "score": a[:, 2], "t": a[:, 3], "done": a[:, 4]}
    return users


def history(u, m):
    return ar.History(u["t"][m], u["anime"][m], u["status"][m], u["score"][m], np.zeros(int(m.sum()), dtype=np.int8))


def make_cases(users, catalog):
    """유저마다 정답 하나를 가린 문제 (유저 키 순서). 목록 밖 정답 수도 같이 돌려줌"""
    lo, hi = day_secs(TARGET_FROM), day_secs(CUTOFF)
    cases, outside = [], 0
    for k, u in users.items():
        done = u["done"]
        per_day = Counter(done[done >= 0].tolist())
        alone = np.array([per_day[d] == 1 for d in done.tolist()]) & (done >= lo) & (done < hi)
        if not alone.any():
            continue
        i = int(np.flatnonzero(alone)[np.argmax(done[alone])])
        target, when = int(u["anime"][i]), int(done[i])
        if target not in catalog:
            outside += 1
            continue
        h = (u["t"] < when) & np.isin(u["anime"], catalog) & (u["anime"] != target)
        if h.sum() >= t.MIN_HISTORY:
            cases.append({"user_id": k, "history": history(u, h), "target": target, "t": when})
    return cases, outside


def stats_histories(users, cases, catalog):
    # 통계용 기록: 시험 유저는 정답 전 기록만, 나머지 유저는 기준일 전 기록
    in_test = {c["user_id"]: c["history"] for c in cases}
    hi = day_secs(CUTOFF)
    out = []
    for k, u in users.items():
        if k in in_test:
            out.append(in_test[k])
            continue
        m = (u["t"] < hi) & np.isin(u["anime"], catalog)
        if m.any():
            out.append(history(u, m))
    return out


def prepare():
    users = load_users()
    entries, _ = service.load_catalog()
    catalog = np.array(sorted(e["anilist_id"] for e in entries), dtype=np.int64)
    cases, outside = make_cases(users, catalog)
    stats = stats_histories(users, cases, catalog)
    pop = Counter()
    for h in stats:
        pop.update(set(h.anime.tolist()))
    ctx = service.base(popularity=dict(pop))
    return users, cases, outside, stats, ctx


def kinds(ctx, cases):
    # 정답 종류: 2022년 이후 작품 (MAL 기록이 없는 작품) / 신규 IP (시리즈에 2022년 전 작품이 없음)
    year = lambda a: int((ctx["anime"][a]["start_date"] or "0")[:4])
    old_series = {ctx["series"][a] for a in ctx["ids"].tolist() if 0 < year(a) < service.NO_COWATCH_FROM}
    new = np.array([year(c["target"]) >= service.NO_COWATCH_FROM for c in cases])
    ip = np.array([n and ctx["series"][c["target"]] not in old_series for c, n in zip(cases, new)])
    return new, ip


def info():
    users, cases, outside, stats, ctx = prepare()
    new, ip = kinds(ctx, cases)
    work = lambda a: ctx["works"][ctx["work_of"][a]]["title_ko"] or ctx["anime"][a]["title"]
    top = Counter(c["target"] for c in cases).most_common(10)
    months = Counter((ar.EPOCH + timedelta(seconds=c["t"])).strftime("%Y-%m") for c in cases)
    pop = case_hits(cases, ctx, ar.popular)
    print(f"유저 {len(users):,}명 → 문제 {len(cases):,}개 (연습 {int(len(cases) * TUNE_SHARE):,}) · 정답이 목록 밖 {outside:,}명 "
          f"({outside / max(len(cases) + outside, 1):.1%}) · 통계 기록 {len(stats):,}명")
    print(f"정답이 2022년 이후 작품 {new.mean():.1%} · 신규 IP {ip.mean():.1%} · 기록 길이 중간값 "
          f"{np.median([len(c['history'].anime) for c in cases]):.0f} · 인기순 시리즈 Hit@10 {pop[:, 0].mean():.4f}")
    print("정답 달:", " ".join(f"{k}:{v}" for k, v in sorted(months.items())))
    print("많이 나온 정답:", " / ".join(f"{work(a)} {n}" for a, n in top), f"(1위 {top[0][1] / len(cases):.1%})" if top else "")


VARIANTS = {"mal": "MAL (지금 서비스)", "anilist": "AniList 표본만", "both": "MAL + AniList"}


def variants(ctx, stats, names=tuple(VARIANTS)):
    """같이 본 작품 재료만 다른 문맥들 (인기 · 최근 인기 · 작품 특징은 같음).
    MAL: 2022년 이후 작품 기록은 지우고 빌려 옴 (지금 서비스) / AniList 표본: 시험 유저는 정답 전 기록만 /
    MAL + AniList: MAL 의 2022년 이후 작품 기록만 빼고 합침. 기록이 하나도 없는 작품은 빌려 옴.
    EASE 규제 세기는 기록 유저 수에 비례 (MAL 47,999명 = 500. 같은 기록을 두 배로 늘리면 같은 결과가 나오게)"""
    mal = service.mal_histories(ctx["entries"])
    no_mal = {e["anilist_id"] for e in ctx["entries"] if e["year"] >= service.NO_COWATCH_FROM or not e["mal_id"]}
    drop = np.array(sorted(no_mal), dtype=np.int64)
    mal_old = [ar.History(*(col[keep] for col in h)) for h in mal for keep in [~np.isin(h.anime, drop)] if keep.any()]
    sources = {"mal": (mal, no_mal), "anilist": (stats, None), "both": (mal_old + stats, None)}
    out = {}
    for key in names:
        hs, nr = sources[key]
        out[key] = c = dict(ctx)
        service.add_cowatch(c, hs, nr, lam=ar.EASE_LAMBDA * len(hs) / len(mal))
        print(f"{VARIANTS[key]}: 기록 {c['cw_users']:,}명 · EASE 규제 {ar.EASE_LAMBDA * len(hs) / len(mal):.0f} · "
              f"기록 없어 빌려 온 작품 {len(c['no_record']):,}편")
    return out


def compare(ctxs, cases, new, ip, base, model=ar.next_auto):
    # 장면(기록 전체 · 신규 유저) × 연습 · 실전마다 표 + 기준(base) 대비 짝 비교
    onb = [t.onboarding(c, ctxs[base]["anime"], 1, 3, ctxs[base]["anilist"], 3) for c in cases]
    head = "방법\t시리즈 Hit@10\tNDCG@10\t정답이 2022년 이후\t정답이 그 전\t신규 IP"
    for scene, cs in (("기록 전체", cases), ("신규 유저 · 작품 1개 + 장르 3 + 테마 3", onb)):
        n = int(len(cs) * TUNE_SHARE)
        for label, idx in (("연습", slice(0, n)), ("실전", slice(n, len(cs)))):
            part, n_, i_ = cs[idx], new[idx], ip[idx]
            print(f"[{scene} · {label} {len(part):,}문제]\n{head}")
            hs = {}
            for name, c in ctxs.items():
                hs[name] = h = case_hits(part, c, model)
                print(f"{name}\t{h[:, 0].mean():.4f}\t{h[:, 1].mean():.4f}\t{h[n_, 0].mean():.4f}\t{h[~n_, 0].mean():.4f}\t"
                      f"{h[i_, 0].mean():.4f}")
            for name in ctxs:
                if name != base:
                    print(f"  {name} vs {base}: {paired(hs[base], hs[name])}")
            print()


def cowatch_test():
    # 1-3 가설 1: 같이 본 작품을 AniList 기록으로 세면 (2022년 이후 작품도 실제 기록) 지금 서비스(MAL + 빌려 오기)보다 맞힘
    t0 = time.time()
    users, cases, outside, stats, ctx = prepare()
    new, ip = kinds(ctx, cases)
    ctxs = {VARIANTS[k]: c for k, c in variants(ctx, stats).items()}
    print(f"문제 {len(cases):,}개 · 준비 {time.time() - t0:.0f}초\n")
    compare(ctxs, cases, new, ip, VARIANTS["mal"])


def people_test(key):
    # 1-3 가설 2 (EXP-26 다시 확인): 같이 본 작품 재료를 정한 뒤, 작품 특징에서 사람 정보(스튜디오 · 제작진 · 성우)를 빼면 어떤가
    users, cases, outside, stats, ctx = prepare()
    new, ip = kinds(ctx, cases)
    on = variants(ctx, stats, (key,))[key]
    off = dict(on)
    saved = dict(ar.PEOPLE)
    try:
        ar.PEOPLE.clear()
        ar.build_features(off, off["anilist"])                    # 특징표 · 빌려 오기 이웃을 사람 정보 없이 다시
        ar.build_cf_borrow(off, off["no_record"])
    finally:
        ar.PEOPLE.update(saved)
    compare({"사람 정보 있음 (지금)": on, "사람 정보 없음": off}, cases, new, ip, "사람 정보 있음 (지금)")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "info"
    if mode == "people":
        return people_test(sys.argv[2] if len(sys.argv) > 2 else "both")
    {"info": info, "cowatch": cowatch_test}[mode]()


if __name__ == "__main__":
    main()
