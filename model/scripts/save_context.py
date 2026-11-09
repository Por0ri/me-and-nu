# save_context — 서버용 통계 문맥 파일 만들기 (서버에서 추천 계산 1단계)
#
# 내 PC 의 원본 (AniList 작품 정보 · MAL 시청 기록 · AniList 유저 표본)으로 추천 문맥을 만들어 파일 하나로 저장한다.
# 서버는 이 파일만 읽는다 (daily_recommend.py --context). 원본은 서버에 올리지 않는다 (AniList 유저 표본은 PC 한 대 · 12/31 삭제 약속)
# 문맥에는 작품끼리 통계만 있음 (저장 전에 유저 단위 자료 검사). 파일은 model/data/ (커밋 안 함)에 두고 서버로 직접 옮긴다
#
#   backend/.venv/Scripts/python.exe model/scripts/save_context.py           # → model/data/service_context.pkl.gz
#   ... --check                                                            # 저장 뒤 다시 읽어 원본 문맥과 칸마다 같은지 확인
#   ... --out 다른/경로.pkl.gz

import argparse
import math
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import numpy as np  # noqa: E402

from model.algo import service  # noqa: E402
from model.algo.preflight import require_inputs  # noqa: E402


def nbytes(v):
    if isinstance(v, np.ndarray):
        return v.nbytes
    if isinstance(v, dict):
        return sum(nbytes(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return sum(nbytes(x) for x in v)
    return 0


def describe(v):
    if isinstance(v, np.ndarray):
        return f"배열 {v.dtype} {v.shape}"
    if isinstance(v, dict):
        return f"dict {len(v):,}칸"
    if isinstance(v, (list, tuple, set)):
        return f"{type(v).__name__} {len(v):,}개"
    return type(v).__name__


def diff(a, b, path="ctx", out=None):
    """두 문맥이 칸마다 같은지 → 다른 칸 이름 목록"""
    out = [] if out is None else out
    if type(a) is not type(b):
        out.append(f"{path}: 종류 {type(a).__name__} ≠ {type(b).__name__}")
    elif isinstance(a, np.ndarray):
        if a.dtype != b.dtype or a.shape != b.shape or not np.array_equal(a, b, equal_nan=a.dtype.kind in "fc"):
            out.append(f"{path}: 배열 다름")
    elif isinstance(a, dict):
        if a.keys() != b.keys():
            out.append(f"{path}: 칸 이름 다름")
        for k in a.keys() & b.keys():
            diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):
            out.append(f"{path}: 길이 다름")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diff(x, y, f"{path}[{i}]", out)
    elif a != b and not (isinstance(a, float) and math.isnan(a) and math.isnan(b)):
        out.append(f"{path}: 값 다름")
    return out


def main():
    ap = argparse.ArgumentParser(description="서버용 통계 문맥 파일 만들기 (원본은 서버에 안 올림)")
    ap.add_argument("--out", default=str(REPO / "model" / "data" / "service_context.pkl.gz"), help="저장할 파일")
    ap.add_argument("--check", action="store_true", help="저장 뒤 다시 읽어 원본 문맥과 칸마다 같은지 확인")
    args = ap.parse_args()
    require_inputs()
    sys.stdout.reconfigure(line_buffering=True)

    t0 = time.time()
    ctx = service.build()
    print(f"문맥 만들기 {time.time() - t0:.0f}초 · 작품 {len(ctx['ids']):,} · 같이 본 기록 유저 {int(ctx['cw_users']):,}명 (숫자만 저장)")
    for k in sorted(ctx, key=lambda k: -nbytes(ctx[k])):
        print(f"  {k:<16} {describe(ctx[k]):<34} {nbytes(ctx[k]) / 2 ** 20:8.1f} MB")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    service.save_context(ctx, out)
    print(f"저장 {time.time() - t0:.0f}초 · {out} · {out.stat().st_size / 2 ** 20:.0f} MB (유저 단위 자료 검사 통과)")

    if args.check:
        t0 = time.time()
        back = service.load_context(out)
        print(f"다시 읽기 {time.time() - t0:.1f}초")
        bad = diff(ctx, back)
        print("확인: 원본 문맥과 칸마다 같음" if not bad else f"확인: {len(bad)}칸 다름 → " + ", ".join(bad[:10]))
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
