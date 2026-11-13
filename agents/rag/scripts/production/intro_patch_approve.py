"""주연 소개 얼굴 바둑판 표지를 묶음에 넣을 준비 (11/13 낮 서원 요청으로 바꾼 표지)
- 품질 검사(run_quality_checks)를 바꾸기 전 · 뒤로 돌려 새 지적이 없는 것만 intro_coverA_patch.approved.json 에 넣음
- 묶을 글 번호 → scratchpad/final_ids.txt : 표지를 바꾼 주연 소개 + 원작 이야기(그림 보강) 통과 글
  (표지를 못 바꾼 주연 소개는 모양이 달라서 이번에 뺌)
  uv run python -I <이 파일>
"""
import json
import pathlib
import sys

from ani_rag.config import PACKAGE_ROOT
from ani_rag.content.models import ContentPackage
from ani_rag.content.quality import run_quality_checks

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
RUNS = PACKAGE_ROOT / ".cache" / "content" / "runs"


def issues(p: dict) -> set[str]:
    return {f"{i.code}@{i.page}" for i in run_quality_checks(ContentPackage.model_validate(p))}


def ready(stamp: str) -> dict[str, dict]:
    out = {}
    for f in sorted(RUNS.glob(f"{stamp}-p*/results.json")):
        for r in json.loads(f.read_text(encoding="utf-8")):
            if r.get("status") == "ready_for_feed" and r.get("package"):
                out[r["request_id"]] = r["package"]
    return out


def normalized(p: dict) -> dict:
    p = json.loads(json.dumps(p))
    for card in p["cards"]:  # 묶음 만들기와 같은 정리
        card["title_lines"] = [t for t in card.get("title_lines") or [] if t.strip()]
        card["body_sentences"] = [s for s in card.get("body_sentences") or [] if s.strip()]
    return p


intro, origin = ready("mass-1113i"), ready("mass-1113o")
patch = json.loads((HERE / "intro_coverA_patch.json").read_text(encoding="utf-8"))
ok, bad = {}, {}
for rid, p in patch.items():
    if rid not in intro:
        continue
    new = issues(normalized(p)) - issues(normalized(intro[rid]))
    if new:
        bad[rid] = sorted(new)
    else:
        ok[rid] = p
(HERE / "intro_coverA_patch.approved.json").write_text(json.dumps(ok, ensure_ascii=False), encoding="utf-8")
left_out = sorted(set(intro) - set(ok))
ids = sorted(ok) + sorted(origin)
(HERE / "final_ids.txt").write_text(" ".join(ids), encoding="utf-8")
print(f"주연 소개 통과 {len(intro)}개 · 표지 바꿈 OK {len(ok)}개 · 새 지적 {len(bad)}개 {bad}")
print(f"이번에 뺀 주연 소개 (표지를 못 바꿈): {left_out}")
print(f"원작 이야기 통과 {len(origin)}개 · 묶을 글 합계 {len(ids)}개 → final_ids.txt")
