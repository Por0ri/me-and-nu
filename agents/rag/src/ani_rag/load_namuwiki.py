"""나무위키 → 정보사전: 감독 · 작가 · 가수 · 제작사 문서의 개요 · 특징 문단 (참고 글)

  uv run python -m ani_rag.load_namuwiki              (agents/rag 폴더에서)
  uv run python -m ani_rag.load_namuwiki --no-fetch   (요청 없이, 이미 뽑아 둔 문단으로 사전에 넣기만)
  uv run python -m ani_rag.load_namuwiki --collect    (캐릭터 · 작품 문서를 캐시에만 모음, 사전에는 안 넣음)
  uv run python -m ani_rag.load_namuwiki --load-collected   (모아 둔 캐릭터 · 작품 문단을 사전에 참고 글로 넣기, 요청 없음)
  uv run python -m ani_rag.load_namuwiki --load-numbers     (모아 둔 캐릭터 정보 표 숫자를 사전에 넣기, 요청 없음)

--collect (11/11 서원): 인기 상위 1,000편의 주연과 작품, 그리고 지금 글에 나오는 캐릭터. 목차 앞 정보 표에서만 키 · 몸무게 ·
혈액형 · 나이 · 생일을 읽고 (값이 여러 개면 비우고 첫 값만 따로), 개요 · 성격 · 특징 · 평가 · 여담 문단을 문단마다 600자까지 남긴다.
끊겨도 이어서 돌 수 있다. 사전에 넣는 건 AniList 키와 대조한 뒤에 따로 한다

- 라이선스 CC BY-NC-SA 2.0 KR (비영리 · 출처 표시 · 같은 조건). 학교 비상업 프로젝트라 서원 결정(11/11)으로 쓴다.
  사전에는 참고 글로만 두고, 글에는 문장을 옮기지 않는다 (새로 쓴다). 상업화하면 다시 확인할 것
- robots.txt 는 문서(/w/)를 허용함 (11/11 확인). 3초 간격, 403 · 429 면 바로 멈춘다. 원본 HTML 은 저장하지 않는다
- 대상: 대량 생산(--mass, 유형마다 100개)의 제작사 · 가수 · 감독 · 작가 글에 나오는 인물 · 제작사
- 문서 찾기: 한국어 이름 → 이름(직업) → 원어 이름 순서. 문서 안에 원어 이름이 있거나(인물),
  애니 제작사라는 말이 있어야(제작사) 맞다고 본다. 동음이의어 문서는 건너뛴다
- 뽑는 것: '개요' + 제목에 특징 · 작풍 · 연출 · 목소리 · 음색 · 창법 · 평가 · 성향 · 스타일이 들어간 문단 (문단마다 500자)
- 이 스크립트가 맡는 칸 (출처 namuwiki): namu_profile_ref, namu_character_ref · namu_work_ref (--load-collected),
  namu_height_cm · namu_weight_kg · namu_blood_type · namu_age (--load-numbers)
- --load-collected (11/11): 스포일러 표시가 있는 문단은 뺀다
- --load-numbers (11/11 밤 서원 결정, 느슨한 기준): 키가 AniList 와 86% 만 같아 처음 기준(95%)엔 못 미쳤지만,
  "나무위키도 공신력 있으니 많은 자료가 의미 있었으면" 해서 넣는다. 값이 하나뿐인 것만, AniList 와 키가 3cm 넘게
  다르거나 혈액형이 다른 캐릭터는 엉뚱한 문서일 수 있어 통째로 뺀다. 프로필 글은 AniList 값이 비어 있을 때만 쓴다
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import psycopg

import ani_rag.build_manifest as bm
from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store

NAMU = "namuwiki"
UA = "menu-dict/0.1 (school project)"
PAUSE = 3.0
CACHE = PACKAGE_ROOT / ".cache" / "raw" / "namuwiki"
FIELDS = ("namu_profile_ref",)
HEAD = re.compile(r"(\d+(?:\.\d+)*)\.\s*([^\[\]]{1,30}?)\s*\[편집\]")
RUBY = re.compile(r"\(\s*[぀-ヿー・\s]+\s*\)")  # 이름 옆 읽는 법 '( ゆあさ )'
CACHE_CHAR = PACKAGE_ROOT / ".cache" / "raw" / "namuwiki_characters"
CACHE_WORK = PACKAGE_ROOT / ".cache" / "raw" / "namuwiki_works"
CHAR_KEYS = ("개요", "성격", "특징", "여담", "외모")
WORK_KEYS = ("개요", "특징", "평가", "여담", "인기", "흥행")
_INFO_LABELS = ("나이", "연령", "생일", "생년월일", "별자리", "신장", "키", "체중", "몸무게", "혈액형", "좋아하는 것", "싫어하는 것",
                "취미", "성우", "소속", "가족", "가족관계", "종족", "성별", "직업", "특기", "국적", "출신", "능력")
_NEXT_LABEL = "|".join(sorted(map(re.escape, _INFO_LABELS), key=len, reverse=True))
KEYS = ("개요", "특징", "작풍", "연출", "목소리", "음색", "창법", "평가", "성향", "스타일", "작품 성향")
ROLE = {"studio": "제작사", "artist": "가수", "director": "감독", "author": "작가"}


class Blocked(Exception):
    pass


_last = 0.0
_short = 0
_wait_on_network = False  # --collect: 연결이 끊기면 '문서 없음'으로 적지 않고 기다렸다 다시 시도


def fetch(title: str) -> str:
    """문서 하나의 본문 글자 (태그를 걷어 냄). 없으면 빈 문자열, 막히면 Blocked"""
    global _last, _short
    for attempt in range(31):
        time.sleep(max(0.0, _last + PAUSE - time.time()))
        _last = time.time()
        req = urllib.request.Request("https://namu.wiki/w/" + urllib.parse.quote(title), headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=40) as res:
                raw = res.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise Blocked(f"나무위키가 {e.code} 로 막음") from e
            if e.code < 500 or not _wait_on_network:
                return ""
        except (urllib.error.URLError, TimeoutError, OSError):
            if not _wait_on_network:
                return ""
        print(f"  연결 문제, 60초 뒤 다시 ({attempt + 1}번째)", flush=True)
        time.sleep(60)
    else:
        raise Blocked("30분 동안 연결이 안 됨")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    if len(text) < 2000 and "[편집]" not in text:  # 빈 문서 · 확인 화면
        _short += 1
        if _short >= 20:
            raise Blocked("짧은 화면이 20번 연속 나옴 (확인 화면일 수 있음)")
        return ""
    _short = 0
    return text


def sections(text: str) -> list[tuple[str, str]]:
    """(문단 제목, 내용) — 개요와 특징 쪽 문단만, 문단마다 500자"""
    heads = list(HEAD.finditer(text))
    out = []
    for i, m in enumerate(heads):
        title = m.group(2).strip()
        if not any(k in title for k in KEYS):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = re.sub(r"\[\d+\]|\[편집\]", " ", text[m.end():end])
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) >= 40:
            out.append((title, body[:500]))
    return out[:4]


def targets(cur) -> list[dict]:
    """대량 생산과 같은 규칙으로 제작사 · 가수 · 감독 · 작가 글감을 골라 그 인물 · 제작사를 모은다 (DB 읽기만)"""
    bm.MASS = True
    for kind in bm.QUOTA:
        bm.QUOTA[kind] = 100
    ctx = bm.Ctx(cur)
    decks = [*bm.pick_studio(ctx), *bm.pick_music(ctx), *bm.pick_directors(ctx), *bm.pick_origin(ctx)]
    out, seen = [], set()
    for d in decks:
        if d.kind not in ROLE:
            continue
        eid = int(d.request_id.rsplit("-", 1)[1][1:])
        if eid in seen:
            continue
        seen.add(eid)
        _, ko, native, romaji, _ = ctx.ent[eid]
        name = ctx.studio(eid) if d.kind == "studio" else ctx.name(eid)
        out.append({"entity": eid, "kind": d.kind, "name": name, "ko": ko or ctx.ko.get(eid), "native": native,
                    "romaji": romaji})
    return out


def find(t: dict) -> tuple[str, str] | None:
    """(문서 제목, 본문) — 맞는 문서를 못 찾으면 None"""
    names = [n for n in dict.fromkeys([t["ko"], t["name"], f"{t['ko'] or t['name']}({ROLE[t['kind']]})",
                                       t["native"], t["romaji"]]) if n]
    for title in names[:4]:
        text = fetch(title)
        if not text or "동음이의어" in text[:4000]:
            continue
        if t["kind"] == "studio":
            ok = "애니메이션" in text and any(k in text for k in ("제작사", "스튜디오", "기업"))
        else:
            # 나무위키는 '湯浅 ( ゆあさ ) 政明 ( まさあき )' 처럼 읽는 법을 달아서, 괄호 속 가나를 걷어 내고 맞춘다 (11/11)
            native = (t["native"] or "").replace(" ", "")
            ok = bool(native) and native in RUBY.sub("", text).replace(" ", "")
        if ok:
            return title, text
    return None


def split_page(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(목차 앞 정보 표 부분, [(문단 제목, 내용)])"""
    toc = re.search(r"\b1\s\.\s*개요", text) or re.search(r"\b1\.\s*개요\s*\[편집\]", text)
    head = text[:toc.start()] if toc else text[:3000]
    heads = list(HEAD.finditer(text))
    found = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = re.sub(r"\s+", " ", re.sub(r"\[\d+\]|\[편집\]", " ", text[m.end():end])).strip()
        found.append((re.split(r"\d+(?:\.\d+)*\s*\.\s*", m.group(2))[-1].strip(), body))  # 목차가 붙어 나오면 끝 조각만
    return head, found


def _field(head: str, label: str) -> str:
    m = re.search(rf"(?:^|\s)(?:{label})\s+(.{{1,70}}?)(?=\s(?:{_NEXT_LABEL})\s|$)", head)
    return m.group(1).strip() if m else ""


def read_info(head: str) -> dict:
    """정보 표의 값. 성장 · 시즌별로 값이 여러 개면 비우고 첫 값만 따로 둔다"""
    size = _field(head, r"신장\s*[/·,]\s*체중|신체(?:\s*사이즈|\s*조건)?|신장|키")
    cm = re.findall(r"(\d{2,3}(?:\.\d)?)\s*cm", size)
    kg = re.findall(r"(\d{2,3}(?:\.\d)?)\s*kg", size) or re.findall(r"(\d{2,3}(?:\.\d)?)\s*kg", _field(head, "체중|몸무게"))
    ages = re.findall(r"(\d{1,4})\s*세", _field(head, "나이|연령"))
    blood = re.search(r"\b(AB|A|B|O)\s*형", _field(head, "혈액형"))
    born = re.search(r"\d{1,2}월\s*\d{1,2}일", _field(head, "생일|생년월일"))
    return {"height_cm": cm[0] if len(set(cm)) == 1 else None, "height_first": cm[0] if cm else None,
            "weight_kg": kg[0] if len(set(kg)) == 1 else None, "weight_first": kg[0] if kg else None,
            "age": ages[0] if len(set(ages)) == 1 else None, "age_first": ages[0] if ages else None,
            "blood": blood.group(1) if blood else None, "birthday": born.group(0) if born else None}


def _pick(found: list[tuple[str, str]], keys: tuple[str, ...]) -> list[list]:
    """[제목, 내용 600자, 스포일러 표시가 있나]"""
    return [[t, b[:600], "스포일러" in b[:600]] for t, b in found if any(k in t for k in keys) and len(b) >= 40][:5]


def _overview(found: list[tuple[str, str]]) -> str:
    return next((b[:400] for t, b in found if "개요" in t), "")


def _squeeze(s: str | None) -> str:
    return re.sub(r"\s", "", s or "")


def find_character(t: dict) -> tuple[str, str] | None:
    """(문서 제목, 본문). 문서의 개요나 정보 표에 작품 이름이 있고, 캐릭터 이름도 맞아야 한다"""
    work, native = _squeeze(t["work"])[:5], _squeeze(t["native"])
    for title in dict.fromkeys(x for x in (f"{t['name']}({t['work']})", t["name"], t["native"]) if x):
        text = fetch(title)
        if not text or "동음이의어" in text[:3000]:
            continue
        head, found = split_page(text)
        flat = _squeeze(RUBY.sub("", head))
        if (work in _squeeze(_overview(found)) or work in flat) and (
                not native or native in flat or _squeeze(t["name"])[:3] in flat):
            return title, text
    return None


def find_work(t: dict) -> tuple[str, str] | None:
    """(문서 제목, 본문). 애니 문서를 먼저 찾고, 없으면 원작 문서. '~에 등장하는' 문서는 작품이 아니라 거른다.
    제목이 짧으면 다른 뜻의 문서가 걸리기 쉬워 시작 연도까지 본다"""
    title, native = t["title"], t["native"]
    for page in dict.fromkeys(x for x in (f"{title}(애니메이션)", f"{title}/애니메이션", title, native) if x):
        text = fetch(page)
        if not text or "동음이의어" in text[:3000]:
            continue
        head, found = split_page(text)
        ov = _overview(found)
        if "등장하는" in ov[:120] or "등장인물" in ov[:60]:
            continue
        flat = _squeeze(RUBY.sub("", head + ov))
        kind_ok = any(k in ov for k in ("애니", "만화", "소설", "노벨", "게임", "영화"))
        name_ok = _squeeze(title)[:5] in flat or bool(native and _squeeze(native)[:6] in flat)
        year_ok = len(_squeeze(title)) > 5 or bool(t["year"] and t["year"] in text[:6000])
        if kind_ok and name_ok and year_ok:
            return page, text
    return None


def collect_targets(cur) -> list[tuple[str, dict]]:
    """모을 순서: 지금 글에 나오는 캐릭터 → 작품 (인기순) → 나머지 주연 (인기순). DB 읽기만"""
    bm.MASS = True
    for kind in bm.QUOTA:
        bm.QUOTA[kind] = 100
    bm.QUOTA["bdaymonth"] = 10 ** 6
    ctx = bm.Ctx(cur)
    mains = bm.main_casts(ctx)
    works = [w for w in ctx.order[:1000] if w in ctx.title]
    first: dict[int, int] = {}
    for d in bm.pick_birthday_month(ctx):
        first.setdefault(int(d.request_id.rsplit("-c", 1)[1]), d.works[0])
    for d in bm.pick_character(ctx):  # 주연 소개 · 생일 모음 · 프로필
        for c in mains.get(d.works[0], {}):
            first.setdefault(c, d.works[0])
    rest = {c: w for w in works for c in mains.get(w, {}) if c not in first}
    known = {c for c in [*first, *rest] if c in ctx.ko}

    def person(c: int, w: int) -> tuple[str, dict] | None:
        name = ctx.char_name(c)
        return ("character", {"entity": c, "name": name, "confirmed": c in known, "native": ctx.ent[c][2],
                              "work": ctx.title[w]}) if name else None

    years = ctx.rows([ctx.work_entries[w][0] for w in works], ("start_year",))
    queue = [p for p in (person(c, w) for c, w in sorted(first.items(), key=lambda x: (ctx.rank[x[1]], x[0]))) if p]
    for w in works:
        y = years.get((ctx.work_entries[w][0], "start_year"))
        queue.append(("work", {"entity": w, "title": ctx.title[w], "native": ctx.ent[w][2], "rank": ctx.rank[w],
                               "year": str(y[0].value) if y else None}))
    queue += [p for p in (person(c, w) for c, w in sorted(rest.items(), key=lambda x: (ctx.rank[x[1]], x[0]))) if p]
    return queue


def collect(queue: list[tuple[str, dict]]) -> None:
    """캐시에만 쌓는다 (대상마다 파일 하나, 있으면 건너뜀)"""
    global _wait_on_network
    _wait_on_network = True
    CACHE_CHAR.mkdir(parents=True, exist_ok=True)
    CACHE_WORK.mkdir(parents=True, exist_ok=True)
    done = {"character": [0, 0], "work": [0, 0]}
    started = time.time()
    print(f"대상 {len(queue)}개 (캐릭터 {sum(k == 'character' for k, _ in queue)} · 작품 {sum(k == 'work' for k, _ in queue)})", flush=True)
    for n, (kind, t) in enumerate(queue, 1):
        path = (CACHE_CHAR if kind == "character" else CACHE_WORK) / f"{t['entity']}.json"
        if path.exists():
            continue
        try:
            hit = find_character(t) if kind == "character" else find_work(t)
        except Blocked as e:
            print(f"{e} → 멈춤 ({n}번째). 다시 돌리면 이어서 함", flush=True)
            return
        record = {**t, "page": hit[0] if hit else None, "sections": []}
        if hit:
            head, found = split_page(hit[1])
            if kind == "character":
                record.update(info=read_info(head), sections=_pick(found, CHAR_KEYS))
            else:
                record.update(overview=_overview(found)[:200], sections=_pick(found, WORK_KEYS))
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        done[kind][0] += 1
        done[kind][1] += bool(hit)
        if n % 50 == 0:
            print(f"  {n}/{len(queue)} · 캐릭터 {done['character'][1]}/{done['character'][0]} · "
                  f"작품 {done['work'][1]}/{done['work'][0]} · {(time.time() - started) / 60:.0f}분", flush=True)
    print(f"끝: 이번에 캐릭터 {done['character'][1]}/{done['character'][0]} · 작품 {done['work'][1]}/{done['work'][0]} 찾음 · "
          f"{(time.time() - started) / 60:.0f}분", flush=True)


COLLECTED_FIELDS = ("namu_character_ref", "namu_work_ref")


def load_collected(cur) -> None:
    """--collect 로 모은 캐릭터 · 작품 문단을 사전에 참고 글로 넣는다 (캐시만 읽음, 요청 없음).
    수집이 아직 돌고 있어도 된다: 다 쓴 파일만 넣고, 다시 돌리면 이번에 모은 것으로 맞춘다"""
    store = Store(cur)
    now = dt.datetime.now(dt.UTC)
    counts = {}
    for folder, field in ((CACHE_CHAR, "namu_character_ref"), (CACHE_WORK, "namu_work_ref")):
        n = skipped = 0
        for path in folder.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue  # 수집이 막 쓰는 파일
            keep = [(t, b) for t, b, spoiler in record.get("sections") or [] if not spoiler]
            skipped += len(record.get("sections") or []) - len(keep)
            if not record.get("page") or not keep:
                continue
            text = "\n".join(f"[{t}] {b}" for t, b in keep)
            store.fact(record["entity"], field, {"text": text, "title": record["page"]}, now,
                       "https://namu.wiki/w/" + urllib.parse.quote(record["page"]), NAMU)
            n += 1
        counts[field] = (n, skipped)
    upserted, deleted = store.write_facts(NAMU, COLLECTED_FIELDS)
    print(" · ".join(f"{field} {n}곳 (스포일러 문단 {s}개 뺌)" for field, (n, s) in counts.items())
          + f" · 넣거나 고침 {upserted} · 지움 {deleted}", flush=True)


NUMBER_FIELDS = ("namu_height_cm", "namu_weight_kg", "namu_blood_type", "namu_age")


def load_numbers(cur) -> None:
    """--collect 로 모은 캐릭터 정보 표 숫자를 사전에 넣는다 (캐시만 읽음, 요청 없음)"""
    records = []
    for path in CACHE_CHAR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # 수집이 막 쓰는 파일
        if record.get("page") and record.get("info"):
            records.append(record)
    cur.execute("""select entity_id, field, value from dict.fact
                   where entity_id = any(%s) and field = any(%s) and source_id = 'anilist'""",
                ([r["entity"] for r in records], ["character_description_ref", "blood_type"]))
    anilist: dict[int, dict] = defaultdict(dict)
    for entity, field, value in cur.fetchall():
        anilist[entity].setdefault(field, value)
    store = Store(cur)
    now = dt.datetime.now(dt.UTC)
    counts: Counter = Counter()
    skipped = 0
    for record in records:
        entity, info = record["entity"], record["info"]
        mine = anilist.get(entity, {})
        height = bm.HEIGHT_RE.search(str(mine.get("character_description_ref") or ""))
        blood = re.search(r"\b(AB|A|B|O)\b", str(mine.get("blood_type") or ""))
        if (height and info.get("height_cm") and abs(float(height.group(1)) - float(info["height_cm"])) > 3) or (
                blood and info.get("blood") and blood.group(1) != info["blood"]):
            skipped += 1  # AniList 와 크게 어긋남 → 다른 캐릭터 문서일 수 있어 숫자를 통째로 뺀다
            continue
        url = "https://namu.wiki/w/" + urllib.parse.quote(record["page"])
        for field, key in (("namu_height_cm", "height_cm"), ("namu_weight_kg", "weight_kg"),
                           ("namu_blood_type", "blood"), ("namu_age", "age")):
            if info.get(key):
                store.fact(entity, field, str(info[key]), now, url, NAMU)
                counts[field] += 1
    upserted, deleted = store.write_facts(NAMU, NUMBER_FIELDS)
    print(f"문서 찾은 캐릭터 {len(records)}명 · 크게 어긋나 뺀 {skipped}명 · "
          + " · ".join(f"{field} {n}" for field, n in counts.items()) + f" · 넣거나 고침 {upserted} · 지움 {deleted}",
          flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="요청 없이 뽑아 둔 문단으로 사전에 넣기만")
    parser.add_argument("--collect", action="store_true", help="캐릭터 · 작품 문서를 캐시에만 모음")
    parser.add_argument("--load-collected", action="store_true", help="모아 둔 캐릭터 · 작품 문단을 사전에 넣기")
    parser.add_argument("--load-numbers", action="store_true", help="모아 둔 캐릭터 정보 표 숫자를 사전에 넣기")
    args = parser.parse_args()
    started = time.time()
    settings = load_settings()
    if args.load_collected or args.load_numbers:
        # 단계마다 연결을 따로 (Store 가 연결마다 임시 표를 만들어서 한 연결에서 두 번 만들면 부딪힘)
        steps = [step for flag, step in ((args.load_collected, load_collected), (args.load_numbers, load_numbers)) if flag]
        for step in steps:
            with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
                step(cur)
        return
    if args.collect:
        with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
            queue = collect_targets(cur)
            conn.rollback()
        collect(queue)
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        todo = targets(cur)
        conn.rollback()
    print(f"대상 {len(todo)}곳 (제작사 · 가수 · 감독 · 작가)", flush=True)
    requests_before = 0
    if not args.no_fetch:
        for n, t in enumerate(todo, 1):
            path = CACHE / f"{t['entity']}.json"
            if path.exists():
                continue
            try:
                found = find(t)
            except Blocked as e:
                print(f"{e} → 멈춤 ({n}번째 대상)", flush=True)
                break
            record = {"entity": t["entity"], "kind": t["kind"], "name": t["name"], "title": None, "sections": []}
            if found:
                title, text = found
                record.update(title=title, sections=sections(text))
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            if n % 20 == 0:
                print(f"  {n}/{len(todo)} · 지금 {t['name']} → {record['title']} · 문단 {len(record['sections'])}",
                      flush=True)
    found = 0
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        now = dt.datetime.now(dt.UTC)
        for path in CACHE.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if not record.get("title") or not record.get("sections"):
                continue
            found += 1
            text = "\n".join(f"[{t}] {b}" for t, b in record["sections"])
            store.fact(record["entity"], "namu_profile_ref", {"text": text, "title": record["title"]}, now,
                       "https://namu.wiki/w/" + urllib.parse.quote(record["title"]), NAMU)
        upserted, deleted = store.write_facts(NAMU, FIELDS)
    print(f"문서 찾고 특징 문단이 있는 곳 {found}/{len(todo)} · 사실 넣거나 고침 {upserted} · 지움 {deleted} · "
          f"{time.time() - started:.0f}초 (요청 전 {requests_before})")


if __name__ == "__main__":
    main()
