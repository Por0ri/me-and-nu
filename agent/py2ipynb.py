# py2ipynb — 에이전트 .py 를 콜랩 노트북(.ipynb)으로 뽑는다
#
# .py 가 정본이다. 노트북은 여기서 만든다. 노트북을 손으로 고치지 않는다.
#
#   python agent/py2ipynb.py agent/music/music_review_agent.py music_review_agent_v2.9.ipynb
#   python agent/py2ipynb.py agent/anime/anime_agent.py anime_agent_v0.2.ipynb     # 애니는 glossary.json 을 노트북 안에 같이 넣는다
#
# 자르는 규칙
# - 파일 맨 위 주석 덩어리 → 마크다운 셀
# - `# ## N. 제목` 줄마다 새 절. 바로 아래 붙은 주석 줄 → 마크다운 셀, 그 뒤 코드 → 코드 셀
# - `def main(` 부터 끝까지는 뺀다. 대신 콜랩용 첫 셀과 실행 셀을 붙인다

import sys, re, json, pathlib

def 자르기(src: str):
    lines = src.splitlines()
    # main() 아래는 노트북에 안 넣는다
    for i, l in enumerate(lines):
        if l.startswith("def main("):
            lines = lines[:i]
            break
    cells = []
    i = 0
    # 맨 위 주석
    head = []
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        head.append(lines[i]); i += 1
    cells.append(("markdown", _md(head)))
    cur_md, cur_code = [], []
    while i < len(lines):
        l = lines[i]
        if re.match(r"^# ## ", l):
            if cur_code and "".join(cur_code).strip():
                cells.append(("code", cur_code))
            cur_code = []
            md = [l]
            i += 1
            while i < len(lines) and lines[i].startswith("#"):
                md.append(lines[i]); i += 1
            cells.append(("markdown", _md(md)))
            continue
        if re.match(r"^# =====", l):          # 실행 안내 덩어리도 마크다운으로
            if cur_code and "".join(cur_code).strip():
                cells.append(("code", cur_code))
            cur_code = []
            md = [l]
            i += 1
            while i < len(lines) and lines[i].startswith("#"):
                md.append(lines[i]); i += 1
            cells.append(("markdown", _md(md)))
            continue
        cur_code.append(l); i += 1
    if cur_code and "".join(cur_code).strip():
        cells.append(("code", cur_code))
    return cells

def _md(lines):
    out = []
    for l in lines:
        l = re.sub(r"^#\s?", "", l)
        l = re.sub(r"^## (\d+[-\d]*\.)", r"## \1", l)
        out.append(l)
    # 앞뒤 빈 줄 정리
    while out and not out[0].strip(): out.pop(0)
    while out and not out[-1].strip(): out.pop()
    return out

def _cell(kind, lines):
    src = "\n".join(lines).strip("\n") + "\n"
    c = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        c["execution_count"] = None
        c["outputs"] = []
    return c

콜랩_첫셀 = '''# 콜랩에서만 돌리는 셀. 로컬 .py 에는 없다
# 보안 비밀(🔑)에 LLM_KEY 를 넣어 둔다
!pip -q install "pydantic-ai-slim[openai]" python-dotenv requests nest_asyncio beautifulsoup4 lxml ddgs feedparser sentence-transformers scikit-learn
import os
from google.colab import userdata
os.environ["LLM_KEY"] = userdata.get("LLM_KEY")
print("LLM_KEY 길이:", len(os.environ["LLM_KEY"]))
'''

실행셀들 = [
    ("markdown", ["## 실행", "", "위 셀을 전부 돌린 뒤 아래 중 하나를 돌린다.", "",
                  "- `점검()` — 이즘 · 아이돌로지 API, 검색 엔진, 해외 매체, RSS. 모델 안 부른다",
                  "- `뽑기만(True, 3)` — 한국 앨범 뽑기 + 재료 모으기까지. 모델 안 부른다",
                  "- `run(목표편수=5)` — 올린 글이 다섯 편 될 때까지. `쪽만=\"KR\"` 이면 한국만",
                  "- `run_one(\"우즈\", \"OO-LI\", 곡=\"Drowning\")` — 앨범 지정. 유명도 안 본다",
                  "- 결과는 `out/music/올림|탈락/` 에 마크다운으로 쌓인다. 마지막 셀이 zip 으로 묶어 내려준다"]),
    ("code", ["점검()"]),
    ("code", ["뽑기만(True, 3)      # 한국. 해외는 뽑기만(False, 3)"]),
    ("code", ["결과 = run(목표편수=5)                 # 한국 3 · 해외 2", "# 결과 = run(목표편수=3, 쪽만=\"KR\")   # 한국만"]),
    ("code", ["st = run_one(\"우즈\", \"OO-LI\", 곡=\"Drowning\")      # 앨범 지정. 곡을 주면 그 곡이 글의 가운데", "# st = run_one(\"염따\", \"살아숨셔 4\")"]),
    ("code", ["z = zip_outputs()", "from google.colab import files", "files.download(str(z))"]),
]

# 애니 에이전트용 실행 셀. 파일 이름에 anime 가 들어 있으면 이것을 붙인다
애니_실행셀들 = [
    ("markdown", ["## 실행", "", "위 셀을 전부 돌린 뒤 아래 중 하나를 돌린다.", "",
                  "- `점검()` — AniList · Jikan · 라프텔 · 위키백과 · 검색. 모델 안 부른다",
                  "- `용어집점검()` — 시리즈마다 관계도와 표기 매칭. 모델 안 부른다",
                  "- `뽑기만(\"감상순서\", 2)` — 주제 뽑기 + 재료 모으기까지. 모델 안 부른다",
                  "- `run(\"사람\", 목표편수=3)` — 올린 글이 세 편 될 때까지. 유형: 사람 · 기념일 · 감상순서 · 제작이야기 · 작품리뷰",
                  "- `run_one(\"블리치\", \"제작이야기\")` — 시리즈 지정. 사람 유형은 `사람=\"Hiroshi Kamiya\"`",
                  "- 결과는 `out/anime/올림|탈락/` 에 마크다운으로 쌓인다. 마지막 셀이 zip 으로 묶어 내려준다"]),
    ("code", ["점검()"]),
    ("code", ["용어집점검()"]),
    ("code", ["뽑기만(\"감상순서\", 2)"]),
    ("code", ["결과 = run(\"감상순서\", 목표편수=2)", "# 결과 = run(\"사람\", 목표편수=3)", "# 결과 = run(\"제작이야기\", 목표편수=2)"]),
    ("code", ["st = run_one(\"나루토\", \"감상순서\")      # 프리렌은 순서에 갈림이 없어 안 뽑힌다", "# st = run_one(\"진격의 거인\", \"사람\", 사람=\"Hiroshi Kamiya\")"]),
    ("code", ["z = zip_outputs()", "from google.colab import files", "files.download(str(z))"]),
]

def main():
    src_path, out_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    src = src_path.read_text(encoding="utf-8")
    실행 = 애니_실행셀들 if "anime" in src_path.name else 실행셀들
    cells = [("code", 콜랩_첫셀.splitlines())]
    용어집 = src_path.parent / "glossary.json"
    if "anime" in src_path.name and 용어집.exists():
        # 용어집을 노트북 안에 넣는다. 콜랩에서 파일을 따로 올리지 않아도 된다. 정본은 glossary.json 이다
        cells.append(("markdown", ["## 용어집", "", "`agent/anime/glossary.json` 을 그대로 옮긴 것이다. 고칠 때는 파일을 고치고 노트북을 다시 뽑는다."]))
        cells.append(("code", ["import pathlib", "pathlib.Path(\"glossary.json\").write_text(r'''"]
                      + 용어집.read_text(encoding="utf-8").splitlines()
                      + ["''', encoding=\"utf-8\")", "print(\"glossary.json 씀\")"]))
    cells += 자르기(src) + 실행
    nb = {
        "cells": [_cell(k, l) for k, l in cells],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "colab": {"provenance": []},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }
    out_path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    n_code = sum(1 for k, _ in cells if k == "code")
    print(f"{out_path}: 셀 {len(cells)}개 (코드 {n_code})")

if __name__ == "__main__":
    main()
