"""가수 이름 바로잡기 (11/12 밤 서원 OK: 즛토마요 · 활동명으로, 다른 사람으로 붙은 것 고치기)
정보사전이 고른 한국어 이름이 틀린 경우: 위키데이터 직역('계속 한밤중이면 좋을텐데'), 활동명 대신 본명 · 일본어 그대로,
애니테마즈 가수가 다른 인물에 잘못 붙음 (YUI → 이시카와 유이, miwa → 오시마 미치루).
- 음악 글(-music-)에만 GLOBAL 을 쓴다 (다른 글의 '하이도라 베루' 같은 이름을 건드리지 않게)
- PER_POST 는 그 글에서만 (같은 사전 항목에 두 사람이 섞여 있어서 글마다 맞는 쪽이 다름)
- 글감(manifest · fixture)에 먼저 쓰면 생성 단계부터 맞는 이름으로 쓰고, 이미 만든 글은 묶을 때 쓴다
"""
import json
import pathlib
import re

GLOBAL = [
    (r"계속 한밤중이면 좋을 ?텐데\.?", "즛토마요"),
    (r"하이도(?!라)", "HYDE"),
    (r"瀬川素公|세가와 모토타카", "MOTSU"),
    (r"마에야마다 (겐|켄)이치|前山田健一", "햐다인"),
    (r"카와세 토모코|川瀬智子", "Tommy heavenly6"),
    (r"木須実怜花|키스미 레이카", "Ray"),
    (r"카라사와 미호|唐沢美帆", "TRUE"),
    (r"當山美智子|당산 미치코|토야마 미치코|도야마 미치코", "MICHI"),
    (r"키타지마 토루|키타지마 토오루|北嶋徹", "TK"),
    (r"야마이케 준야\(山池純矢\)|山池純矢|야마이케 준야", "Vickeblanka"),
    (r"熊川みゆ|쿠마가와 미유", "Myuk"),
]
PER_POST = {
    "dict-music-themes-w37": [(r"이시카와 유이|石川由依", "YUI")],          # 블리치 「Rolling star」
    "dict-music-themes-w10-v2": [(r"이시카와 유이|石川由依", "YUI")],       # 강철의 연금술사 「again」
    "dict-music-themes-w752": [(r"오시마 미치루|大島ミチル", "미와")],      # 은수저 「Kiss you」
    "dict-guide-basic-w652": [(r"(?<![A-Za-z])YUI(?![A-Za-z])", "이시카와 유이")],  # 성녀의 마력 세이 성우
    # 암살교실 시범 판: 성우가 여럿일 때 첫 번째를 골라서 틀림 (TV판 살생님 = 후쿠야마 준). 11/13 본 성우 고르기로 고침
    "dict-guide-basic-w17": [(r"세키 토모카즈", "후쿠야마 준")],
}
# 받침 있는 이름으로 바뀌면 조사도 바꿈 (햐다인)
PARTICLES = [(r"햐다인가", "햐다인이"), (r"햐다인는", "햐다인은"), (r"햐다인를", "햐다인을"), (r"햐다인와", "햐다인과"),
             (r"햐다인로", "햐다인으로"), (r"햐다인야", "햐다인이야"), (r"햐다인랑", "햐다인이랑"), (r"햐다인였", "햐다인이었")]
SKIP_KEYS = {"url", "source_url", "evidence_url", "images", "rendered_image_key", "image_id", "original",
             "local_path", "discovery_url"}


def rules(request_id: str) -> list[tuple[str, str]]:
    out = list(PER_POST.get(request_id, []))
    if "-music-" in request_id:
        out += GLOBAL
    return out


def fix_text(text: str, rs: list[tuple[str, str]]) -> str:
    for pattern, new in rs:
        text = re.sub(pattern, new, text)
        # '키스미 레이카(木須実怜花)' 처럼 한국어 + 원문을 둘 다 바꾸면 'Ray(Ray)' 가 되니 하나로
        text = text.replace(f"{new}({new})", new).replace(f"{new} ({new})", new)
    if "햐다인" in text:
        for pattern, new in PARTICLES:
            text = re.sub(pattern, new, text)
    return text


def fix_obj(obj, rs):
    if not rs:
        return obj
    if isinstance(obj, dict):
        return {k: (v if k in SKIP_KEYS else fix_obj(v, rs)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fix_obj(v, rs) for v in obj]
    if isinstance(obj, str):
        return fix_text(obj, rs)
    return obj


def fix_package(package: dict) -> dict:
    fixed = fix_obj(package, rules(package.get("request_id") or ""))
    # 노래 제목에 '히야다인'으로 옮겨져 있으면 (일상 「히야다인의 카카카타☆짝사랑」) 가수 이름도 같은 표기로
    if "히야다인" in json.dumps(fixed, ensure_ascii=False):
        fixed = fix_obj(fixed, [(r"햐다인", "히야다인")])
    return fixed


def patch_folder(root: pathlib.Path) -> int:
    """글감 폴더(manifests · fixtures)를 그 자리에서 고침. 바뀐 파일 수를 돌려줌"""
    n = 0
    for sub in ("manifests", "fixtures"):
        for f in sorted((root / sub).glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            fixed = fix_obj(data, rules(f.stem))
            if fixed != data:
                f.write_text(json.dumps(fixed, ensure_ascii=False, indent=1), encoding="utf-8")
                n += 1
    return n


if __name__ == "__main__":
    import sys
    for folder in sys.argv[1:]:
        print(folder, "고친 파일", patch_folder(pathlib.Path(folder)))
