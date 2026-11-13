"""서버에 이미 있는 글 중 가수 이름이 틀린 글을 글자만 고친 새 번호(-v2) 판으로 (그림 · 출처 그대로, 돈 안 듦)
11/12 밤 서원: 옛 글은 서버에서 숨기고 고친 판을 새 번호로. 옛 주제가 10개는 새 양식으로 다시 생성(mass-1112v2)해서 여기선 안 함
  uv run python -I <이 파일>   → .cache/server_bundles_20261113/<글 번호>-v2/
"""
import json
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
from performer_names import fix_package  # noqa: E402
from ani_rag.content.models import ContentPackage  # noqa: E402
from ani_rag.content.quality import run_quality_checks  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
CACHE = pathlib.Path(__file__).resolve().parents[2] / ".cache"
OLD = ["dict-music-artist-p21774"]  # 계속 한밤중이면 좋을텐데 → 즛토마요
for rid in OLD:
    src = CACHE / "server_bundles_20261112" / rid
    doc = json.loads((src / "bundle.json").read_text(encoding="utf-8"))
    item = doc["items"][0]
    old = item["package"]
    new = fix_package(json.loads(json.dumps(old)))
    new["request_id"] = f"{rid}-v2"
    issues = lambda p: {f"{i.code}@{i.page}" for i in run_quality_checks(ContentPackage.model_validate(p))}
    added = issues(new) - issues(old)
    assert not added, f"{rid}: 새 지적 {added}"
    root = CACHE / "server_bundles_20261113" / new["request_id"]
    if root.exists():
        shutil.rmtree(root)
    (root / "cards").mkdir(parents=True)
    for asset in item["assets"]:
        try:
            os.link(src / asset["path"], root / asset["path"])
        except OSError:
            shutil.copy2(src / asset["path"], root / asset["path"])
    evidence = {**item["reviewEvidence"], "sourceFile": f"server_bundles_20261112/{rid} (가수 이름만 고침)"}
    out = {**{k: v for k, v in doc.items() if k != "items"},
           "items": [{"package": new, "assets": item["assets"], "reviewEvidence": evidence}]}
    (root / "bundle.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(new["request_id"], "|", new["topic"])
    for c in sorted(new["cards"], key=lambda c: c["page"]):
        print(f"  [{c['page']}] {c.get('eyebrow')} | {' / '.join(c['title_lines'])} | {' '.join(c['body_sentences'])}")
