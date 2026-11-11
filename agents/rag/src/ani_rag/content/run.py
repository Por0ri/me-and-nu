"""글감 묶음(manifest) → 글 · 카드 · 검수 → feed_queue

  uv run python -m ani_rag.content.run --only curation music director        (agents/rag 폴더에서)

- 글감 묶음은 ani_rag.build_manifest 가 만든다 (.cache/trial/manifests)
- 결과: .cache/content/runs/<시각>/ 에 index.html (글 · 카드를 눈으로 보는 페이지) · results.json · 카드 PNG
- 통과한 글은 .cache/content/agent.sqlite3 의 feed_queue 에 들어간다 (지금 서버에 넣는 모양 그대로)
- --offline: LLM 없이 임시 문구로 그림 · 카드 단계만 점검 (비용 0원)
- --budget: 이 실행의 LLM 비용 상한(달러). 넘으면 남은 글감을 건너뛴다
- 끝나면 index.html 을 브라우저로 연다 (--no-open 이면 안 연다)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics
import sys
import time
import webbrowser
from collections import Counter

from ani_rag.config import PACKAGE_ROOT
from ani_rag.content.config import get_settings
from ani_rag.content.gallery import write_gallery
from ani_rag.content.graph import build_graph
from ani_rag.content.models import ContentPackage
from ani_rag.content.storage import AgentRepository

TRIAL = PACKAGE_ROOT / ".cache" / "trial"


def cost_of(state: dict) -> float:
    return sum(float(r.get("estimated_cost_usd") or 0) for r in state.get("usage_records", []))


def export_cards(package: dict, repository: AgentRepository, folder: pathlib.Path) -> dict[int, str]:
    """카드 PNG 를 파일로 꺼낸다 (페이지 → 상대 경로)"""
    saved: dict[int, str] = {}
    folder.mkdir(parents=True, exist_ok=True)
    for card in package.get("cards") or []:
        key = card.get("rendered_image_key")
        stored = repository.get_image_bytes(key) if key else None
        if stored:
            path = folder / f"card_{card['page']:02d}.png"
            path.write_bytes(stored[0])
            saved[card["page"]] = f"{folder.name}/{path.name}"
    return saved


def run_one(path: pathlib.Path, fixtures: pathlib.Path, offline: bool, out: pathlib.Path) -> dict:
    package = ContentPackage.model_validate(json.loads(path.read_text(encoding="utf-8")))
    repository = AgentRepository()
    repository.persist_images(package)
    state = {
        "request_id": package.request_id,
        "topic": package.topic,
        "content_kind": package.content_kind.value,
        "requested_content_type": package.content_type,
        "package": package.model_dump(mode="json"),
        "trace": [],
        "revision_count": 0,
        "max_revision": get_settings().max_revision,
        "allow_llm": not offline,
        "offline_cards": [],
        "render_required": True,
    }
    if offline:
        deck = json.loads((fixtures / path.name).read_text(encoding="utf-8"))
        state["offline_cards"] = deck["cards"]
    started = time.time()
    record: dict = {"request_id": package.request_id, "topic": package.topic, "content_type": package.content_type,
                    "n_facts": len(package.facts)}
    try:
        result = build_graph().invoke(state, {"recursion_limit": 60})
    except Exception as exc:
        record.update(status="error", error=f"{type(exc).__name__}: {exc}"[:800], seconds=time.time() - started,
                      cost=0.0, cards={}, package=package.model_dump(mode="json"))
        return record
    final = result.get("package") or {}
    record.update(
        status=result.get("status"),
        decision=result.get("editorial_decision"),
        score=(result.get("quality_report") or {}).get("total"),
        issues=[{k: i.get(k) for k in ("code", "message", "page", "severity")} for i in result.get("qa_report") or []],
        review=result.get("language_review") or {},
        rejection=result.get("rejection_reason") or "",
        revisions=result.get("revision_count", 0),
        seconds=time.time() - started,
        cost=cost_of(result),
        cards=export_cards(final, repository, out / package.request_id),
        package=final,
    )
    return record


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifests", type=pathlib.Path, default=TRIAL / "manifests")
    ap.add_argument("--fixtures", type=pathlib.Path, default=TRIAL / "fixtures")
    ap.add_argument("--only", nargs="*", default=[], help="글감 이름에 이 글자가 있는 것만")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--budget", type=float, default=0.6)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    if not args.offline and not get_settings().openai_api_key:
        raise SystemExit("agents/rag/.env 에 OPENAI_API_KEY 가 없음")
    paths = sorted(p for p in args.manifests.glob("*.json") if not args.only or any(o in p.stem for o in args.only))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M") + ("-offline" if args.offline else "")
    out = PACKAGE_ROOT / ".cache" / "content" / "runs" / stamp
    out.mkdir(parents=True, exist_ok=True)
    print(f"글감 {len(paths)}개 · {'오프라인' if args.offline else f'상한 ${args.budget}'} · 결과 {out}", flush=True)

    records, spent = [], 0.0
    for n, path in enumerate(paths, 1):
        if spent >= args.budget:
            print(f"상한 도달 (${spent:.3f}): 남은 {len(paths) - n + 1}개 건너뜀", flush=True)
            break
        rec = run_one(path, args.fixtures, args.offline, out)
        spent += rec["cost"]
        records.append(rec)
        print(f"  {n}/{len(paths)} {rec['request_id']}: {rec['status']} · 점수 {rec.get('score')} · "
              f"${rec['cost']:.4f} · {rec['seconds']:.0f}초 {rec.get('error') or ''}", flush=True)
        (out / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        write_gallery(records, out / "index.html")

    done = [r for r in records if r["status"] == "ready_for_feed"]
    print(f"결과: 통과 {len(done)} / {len(records)} · 총 ${spent:.3f}")
    print("상태별: " + " · ".join(f"{k} {v}" for k, v in Counter(r["status"] for r in records).most_common()))
    codes = Counter(i["code"] for r in records if r["status"] != "ready_for_feed" for i in r.get("issues") or [])
    print("떨어진 이유: " + (" · ".join(f"{k} {v}" for k, v in codes.most_common(12)) or "없음"))
    if records:
        median = statistics.median(r["seconds"] for r in records)
        print(f"건당 평균 ${spent / len(records):.4f} · 시간 중간값 {median:.0f}초")
    print(f"보기: {out / 'index.html'}")
    if not args.no_open:
        webbrowser.open((out / "index.html").as_uri())


if __name__ == "__main__":
    main()
