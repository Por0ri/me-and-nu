"""묶음 1,462개를 서버 가져오기 도구의 검사(load_bundle)로 확인 (DB 없이, 읽기만)"""
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from app.seeds.carousel_contents import load_bundle  # noqa: E402

folder = Path(sys.argv[1])
ok, errors = 0, Counter()
examples = {}
for bundle in sorted(folder.glob("*/bundle.json")):
    try:
        load_bundle(bundle)
        ok += 1
    except Exception as exc:  # 검사 실패 이유를 모은다
        key = f"{type(exc).__name__}: {str(exc)[:150]}"
        errors[key] += 1
        examples.setdefault(key, bundle.parent.name)
print(f"통과 {ok}개 · 실패 {sum(errors.values())}개")
for key, n in errors.most_common(15):
    print(f"  {n}개 · {key} · 예: {examples[key]}")
