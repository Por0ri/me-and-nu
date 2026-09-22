# py2ipynb — 에이전트 .py 를 콜랩 노트북(.ipynb)으로 뽑는다
#
# .py 가 정본이다. 노트북은 여기서 만든다. 노트북을 손으로 고치지 않는다.
#
#   python agent/py2ipynb.py agent/music/music_review_agent.py music_review_agent_v2.9.ipynb
#   python agent/py2ipynb.py agent/anime/anime_agent.py anime_agent_v0.3.ipynb     # 애니는 glossary.json 을 노트북 안에 같이 넣는다
#
# 자르는 규칙
# - 파일 맨 위 주석 덩어리 → 마크다운 셀
# - `# ## N. 제목` 줄마다 새 절. 바로 아래 붙은 주석 줄 → 마크다운 셀, 그 뒤 코드 → 코드 셀
# - `def main(` 부터 끝까지는 뺀다. 대신 콜랩용 첫 셀과 실행 셀을 붙인다
# - 음악은 다르다 (v3.2부터). main 이 노트북에서 불려도 안 터지게 되어 있어 남긴다. `if __name__` 덩어리만 뺀다.
#   첫 셀도 음악 전용(pydantic-ai · openai 판 맞추기, Jev 키, agent/nb 로 이동)을 쓴다

import sys, re, json, pathlib

def 자르기(src: str, main유지=False):
    lines = src.splitlines()
    # main() 아래는 노트북에 안 넣는다. main유지 면 `if __name__` 덩어리만 뺀다
    자를곳 = 'if __name__ == "__main__":' if main유지 else "def main("
    for i, l in enumerate(lines):
        if l.startswith(자를곳):
            lines = lines[:i]
            break
    cells = []
    i = 0
    # 맨 위 주석
    head = []
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()) and not lines[i].startswith("# ## "):
        head.append(lines[i]); i += 1          # `# ## 1.` 부터는 절이다. 머리에 안 넣는다
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

# 음악 전용 첫 셀 (v3.1~). 콜랩에 미리 깔린 openai 가 오래된 판이라 pydantic-ai 와 짝으로 올린다.
# 키는 셀 안에 직접 적거나 보안 비밀에서 찾는다. Jev 키는 없어도 된다. 결과 자리를 맞추려고 agent/nb 로 들어간다
음악_첫셀 = r'''# 콜랩에서 돌리기 전에 이 셀부터 돌린다. 런타임을 새로 시작했으면 다시 돌린다.
#
# 콜랩에는 openai가 미리 깔려 있는데 그게 오래된 판이다. pydantic-ai는 openai 3.8 위를 쓴다.
# 그래서 "이미 있으니 건너뛴다"로 하면 판이 어긋나 ResponseCompactionCompactingEvent 같은 이름을 못 찾는다.
# 여기서는 둘을 짝으로 묶어 올린다. 판이 바뀌면 런타임을 다시 시작해야 한다. 아래에서 알려 준다.
import importlib, importlib.metadata as 메타, subprocess, sys, os, pathlib

짝으로올릴것 = ["pydantic-ai-slim[openai]", "openai>=3.8"]        # 이 둘은 판을 맞춰야 한다
없으면깔것 = {
    "ddgs":                  "ddgs",
    "feedparser":            "feedparser",
    "bs4":                   "beautifulsoup4",
    "lxml":                  "lxml",
    "sentence_transformers": "sentence-transformers",
    "dotenv":                "python-dotenv",
    "nest_asyncio":          "nest_asyncio",
    "requests":              "requests",
    "langchain_typesafe":    "langchain-typesafe",    # Jev. 이것만 없으면 Jev를 안 쓰고 나머지는 돈다
}


def _판(이름):
    try:
        return 메타.version(이름)
    except Exception:
        return None


def _깔기(것들, 올리기=False):
    줄 = [sys.executable, "-m", "pip", "install", "-q"] + (["-U"] if 올리기 else []) + list(것들)
    if subprocess.run(줄).returncode != 0:
        raise SystemExit("설치가 실패했다. 위에 찍힌 줄을 보고 무엇이 안 깔렸는지 확인한다")


볼판 = ("openai", "pydantic-ai-slim")
전 = {n: _판(n) for n in 볼판}
print("지금 판:", ", ".join(f"{n} {전[n] or '없음'}" for n in 볼판))
_깔기(짝으로올릴것, 올리기=True)
후 = {n: _판(n) for n in 볼판}

없는것 = []
for 모듈, 이름 in 없으면깔것.items():
    try:
        importlib.import_module(모듈)
    except Exception:
        없는것.append(이름)
if 없는것:
    print("더 깔 것:", ", ".join(없는것))
    _깔기(없는것)
importlib.invalidate_caches()

바뀐것 = [n for n in 볼판 if 전[n] != 후[n]]
if 바뀐것:
    print("판이 바뀌었다:", ", ".join(f"{n} {전[n] or '없음'} → {후[n]}" for n in 바뀐것))
    if any(n in sys.modules for n in ("openai", "pydantic_ai")):
        raise SystemExit(
            "런타임을 다시 시작하고 이 셀을 한 번 더 돌린다.\n"
            "  위 메뉴 런타임 → 세션 다시 시작. 콜랩이 띄우는 RESTART SESSION 단추를 눌러도 된다.\n"
            "  이미 읽어 둔 옛 판이 메모리에 남아 있어 다시 시작하지 않으면 그대로 실패한다."
        )
    print("아직 읽어 둔 것이 없어 다시 시작하지 않아도 된다")
else:
    print("판이 그대로다. 그냥 간다")

아직없음 = []
for 모듈 in ["pydantic_ai", "openai", *없으면깔것]:
    try:
        importlib.import_module(모듈)
    except Exception as e:
        아직없음.append(f"{모듈} ({type(e).__name__})")
if 아직없음:
    if len(아직없음) == 1 and 아직없음[0].startswith("langchain_typesafe"):
        print("langchain-typesafe만 안 된다. Jev를 안 쓰고 그대로 돈다. 큰 모델이 다 하고 그만큼 느리다")
    else:
        raise SystemExit(f"아직 안 되는 것: {', '.join(아직없음)}. 런타임을 다시 시작하고 이 셀을 한 번 더 돌린다")
else:
    print("필요한 것이 다 된다")

# ── 키 ─────────────────────────────────────────────────────────
# 두 가지 중 하나로 넣는다.
#   (가) 아래 따옴표 안에 키를 그대로 붙인다
#   (나) 콜랩 왼쪽 열쇠 모양(보안 비밀)에 넣고 여기는 비워 둔다
# 비워 두면 이미 들어 있는 값을 지우지 않는다. 보안 비밀에서 찾아본다.
#
# Jev 키 이름이 TYPESAFE_API_KEY인 것은 Jev를 만든 회사 이름이 TypeSafe라서다.
# 보안 비밀에는 JEV · JEV_KEY · JEV_API_KEY · TYPESAFE_API_KEY 중 아무 이름으로나 넣어도 찾는다.
LLM_KEY_직접 = ""    # 큰 모델 키. 이건 꼭 있어야 한다
JEV_키_직접  = ""    # Jev 키. 없어도 돈다. 그만큼 느리다


def _키넣기(환경변수, 직접, 딴이름=()):
    if 직접.strip():
        os.environ[환경변수] = 직접.strip()
        return "직접 적음"
    for 이름 in (환경변수, *딴이름):
        v = (os.environ.get(이름) or "").strip()
        if v:
            os.environ[환경변수] = v
            return f"환경변수 {이름}"
    try:
        from google.colab import userdata
        for 이름 in (환경변수, *딴이름):
            try:
                v = userdata.get(이름)
            except Exception:
                continue
            if v and v.strip():
                os.environ[환경변수] = v.strip()
                return f"콜랩 보안 비밀 {이름}"
    except Exception:
        pass
    return "없음"


print("LLM_KEY:", _키넣기("LLM_KEY", LLM_KEY_직접, ("OPENAI_API_KEY",)))
print("Jev 키 :", _키넣기("TYPESAFE_API_KEY", JEV_키_직접, ("JEV", "JEV_KEY", "JEV_API_KEY")))

if not (os.environ.get("LLM_KEY") or "").strip():
    raise SystemExit("LLM_KEY가 없다. 위 LLM_KEY_직접에 키를 넣고 이 셀을 다시 돌린다")
if not (os.environ.get("TYPESAFE_API_KEY") or "").strip():
    print("  Jev 키가 없다. 큰 모델이 다 한다. 돌아가기는 한다")

# 콜랩은 __file__이 없다. 노트북은 지금 폴더를 자기 자리로 본다.
# 결과는 한 칸 위 out/music에 쌓이므로 agent/nb 안으로 들어간다. 그러면 agent/out/music이 된다.
# 이미 들어와 있으면 또 들어가지 않는다
if pathlib.Path.cwd().name != "nb":
    pathlib.Path("agent/nb").mkdir(parents=True, exist_ok=True)
    os.chdir("agent/nb")
print("지금 폴더:", pathlib.Path.cwd())
'''

음악_실행셀들 = [
    ("markdown", ["## 실행", "", "위 셀을 전부 돌린 뒤 아래 셀에서 하나만 골라 주석을 푼다.", "",
                  "- `점검()` — 이즘 · 아이돌로지 API, 검색 엔진, 해외 매체, RSS, Jev. 큰 모델은 안 부른다",
                  "- `뽑기만(want_kr=True, n=3)` — 앨범 뽑기 + 재료 모으기까지. 큰 모델은 안 부른다",
                  "- `run(목표편수=5)` — 올린 글이 다섯 편 될 때까지. `동시=2` 면 두 편씩 같이",
                  "- `run_one(\"우즈\", \"OO-LI\", 곡=\"Drowning\")` — 앨범 지정. 유명도 안 본다",
                  "- 결과는 `agent/out/music/올림|탈락/` 에 마크다운으로 쌓인다. `run` · `run_one` 이 끝나면 zip 으로 묶어 브라우저로 내려준다 (`콜랩_끝나면zip`)",
                  "- `zip_outputs()` 는 다시 내려받고 싶을 때만"]),
    ("code", """# ── 여기서 돌린다. 하나만 골라 주석을 푼다 ──────────────────

점검()                       # 매체 · 검색 엔진 · Jev가 살아 있는지 본다. 큰 모델은 안 부른다

# 뽑기만(want_kr=True, n=3)   # 앨범 뽑기와 재료 모으기까지만 본다. 큰 모델은 안 부른다
# run(목표편수=5)             # 다섯 편이 올라갈 때까지 돈다 (한국 3 · 해외 2)
# run_one("우즈", "OO-LI", 곡="Drowning")   # 앨범을 지정해서 한 편
# zip_outputs()               # out/music 폴더를 zip으로 묶는다""".splitlines()),
]

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
                  "- `run(목표편수=6)` — 유형 넷을 섞어 여섯 편. `run(\"사람\", 목표편수=3)` 처럼 하나만도 된다. 유형: 사람 · 감상순서 · 제작이야기 · 작품리뷰 (기념일은 v0.4에서 뺐다. `run_one`으로는 된다)",
                  "- `run_one(\"블리치\", \"제작이야기\")` — 시리즈 지정. 사람 유형은 `사람=\"Hiroshi Kamiya\"`",
                  "- 결과는 `out/anime/올림|탈락/` 에 마크다운으로 쌓인다. `run` · `run_one` 이 끝나면 zip 으로 묶어 브라우저로 내려준다 (`콜랩_끝나면zip`). 마지막 셀은 다시 받고 싶을 때만"]),
    ("code", ["점검()"]),
    ("code", ["용어집점검()"]),
    ("code", ["뽑기만(\"감상순서\", 2)"]),
    ("code", ["결과 = run(목표편수=6)                  # 유형 다섯을 섞어 여섯 편", "# 결과 = run(\"사람\", 목표편수=3)      # 하나만"]),
    ("code", ["st = run_one(\"나루토\", \"감상순서\")      # 프리렌은 순서에 갈림이 없어 안 뽑힌다", "# st = run_one(\"진격의 거인\", \"사람\", 사람=\"Hiroshi Kamiya\")"]),
    ("code", ["z = zip_outputs()", "from google.colab import files", "files.download(str(z))"]),
]

def main():
    src_path, out_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    src = src_path.read_text(encoding="utf-8")
    음악 = "music" in src_path.name
    실행 = 애니_실행셀들 if "anime" in src_path.name else (음악_실행셀들 if 음악 else 실행셀들)
    cells = [("code", (음악_첫셀 if 음악 else 콜랩_첫셀).splitlines())]
    용어집 = src_path.parent / "glossary.json"
    if "anime" in src_path.name and 용어집.exists():
        # 용어집을 노트북 안에 넣는다. 콜랩에서 파일을 따로 올리지 않아도 된다. 정본은 glossary.json 이다
        cells.append(("markdown", ["## 용어집", "", "`agent/anime/glossary.json` 을 그대로 옮긴 것이다. 고칠 때는 파일을 고치고 노트북을 다시 뽑는다."]))
        cells.append(("code", ["import pathlib", "pathlib.Path(\"glossary.json\").write_text(r'''"]
                      + 용어집.read_text(encoding="utf-8").splitlines()
                      + ["''', encoding=\"utf-8\")", "print(\"glossary.json 씀\")"]))
    cells += 자르기(src, main유지=음악) + 실행
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
