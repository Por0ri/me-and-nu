"""올림 글을 git에 올릴 콘텐츠로 모은다.

agent/out/ 은 gitignore다(판정 기록 · 탈락 글 · 캐시가 섞여 있다). 여기서 올림 글만 골라
agent/contents/ 아래에 깨끗한 글(.md)과 한 벌의 JSON(contents.json)으로 옮긴다.

    python agent/export_contents.py          # 다시 돌려도 같은 글은 같은 id로 덮어쓴다

팀원마다 out/ 이 다르므로 합친다. 이미 contents.json 에 있는 글은 그대로 두고, 내 out/ 의 올림 글만
id로 더하거나 덮어쓴다. 내 out/ 에 원본이 있는데 올림에서 빠진 글만 contents 에서 뺀다.

JSON 한 건의 모양은 프론트 mock(frontend/types/content.ts ContentDetail)에 맞췄다.
  id · title · summary · body · sourceName · sources[{id, name, url}] · saved(false) · topicId
그 밖에 subtopic(영화 정보 · 영화 리뷰 · 음악 리뷰 · 애니 유형) · work(작품 이름) · agent · agentVersion · createdAt 을 붙인다.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
DEST = HERE / "contents"

# 결과 폴더 → 토픽. 영화 정보는 올림 · 탈락이 한 폴더라 머리의 **상태**로 가른다
FOLDERS = [
    ("movie_info", "topic-movie", "영화", "영화 정보"),
    ("movie_review/올림", "topic-movie", "영화", "영화 리뷰"),
    ("music/올림", "topic-music", "음악", "음악 리뷰"),
    ("anime/올림", "topic-anime", "애니메이션", None),     # 애니는 파일 이름 앞머리가 유형이다
]
TOPIC_DIR = {"topic-movie": "movie", "topic-music": "music", "topic-anime": "anime"}
빼는유형 = {"감상순서", "기념일"}          # 애니. PM 9/29: 글 형식이 이상하다 → 유형에서 뺐다
애니_시작일 = "2026-09-29"                # 애니 9/21 글은 v0.x 시험판이다(제목 '제목' · "이 글은 ~에 관한 글이다")
빼는글 = {                                # PM이 짚은 글. 파일 이름 앞부분으로 찾는다
    "사람_코드 기아스 반역의 를르슈_": "PM 9/29: 첫 문장부터 이상하다(보는 법 지시 · 캐릭터 분석)",
    "타겟__거래_한_번이_일상을_잠식할_때": "PM 9/29: 같은 정보를 두 번씩 되풀이한다",
}


def _섹션(rest: str, 이름: str) -> str:
    m = re.search(rf"^## {이름}\s*\n(.*?)(?=^## |\Z)", rest, re.M | re.S)
    return m.group(1) if m else ""


def _출처(rest: str) -> list:
    """## 출처 줄에서 실제로 읽은 출처만. '본문 안 읽음' 링크는 뺀다."""
    out = []
    for line in _섹션(rest, "출처").splitlines():
        line = line.strip()
        if not line.startswith("- ") or "본문 안 읽음" in line:
            continue
        url = (re.search(r"https?://\S+", line) or [None])[0]
        name = line[2:]
        if url:
            name = name.replace(url, "").strip(" —-·:")
        name = re.sub(r"^\((.*?)\)\s*", r"\1 · ", name).strip(" ·") or (re.sub(r"^https?://(www\.)?", "", url).split("/")[0] if url else "")
        if url and name in ("맥락", "한 매체", ""):             # 에이전트 안쪽 이름은 도메인으로
            name = re.sub(r"^https?://(www\.|m\.)?", "", url).split("/")[0]
        out.append({"name": name, "url": url})
    return out


def _첫문장(body: str, n: int = 120) -> str:
    s = re.split(r"(?<=[.?!])\s+", body.strip(), maxsplit=1)[0]
    return s if len(s) <= n else s[: n - 1] + "…"


def _만든때(path: Path, head: str) -> str:
    m = re.search(r"_(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.md$", path.name)
    if m:
        mo, d, h, mi, s = map(int, m.groups())
        return datetime.datetime(2026, mo, d, h, mi, s).isoformat()
    m = re.search(r"\*\*만든 날\*\*\s*(\d{4}-\d{2}-\d{2})", head)
    if m:
        return m.group(1) + "T00:00:00"
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat()


def _판(text: str) -> str:
    m = re.search(r"\*\*버전\*\*\s*([^·\n]+)", text)
    if m:
        return m.group(1).strip()
    vs = re.findall(r"(?<![A-Za-z0-9.])([Vv]\d+\.\d+)(?![\d.])", text)
    return vs[-1] if vs else ""


def _작품(rest: str, head: str) -> str:
    m = re.search(r"\*\*영화\*\*\s*(.+)", head)
    if m:
        return m.group(1).strip()
    for 이름 in ("영화", "앨범", "주제"):
        sec = _섹션(rest, 이름)
        for line in sec.splitlines():
            if line.startswith("- "):
                return re.split(r"\s·\s|\s{2,}\[", line[2:])[0].strip()
    return ""


def 읽기(path: Path, topic_id: str, topic_name: str, subtopic):
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"^---\s*$", text, flags=re.M)
    head = parts[0]
    if "**상태**" in head:                                   # 영화 정보: 머리 · 본문 · 나머지
        if not re.search(r"\*\*상태\*\*\s*올림", head):
            return None
        body, rest = (parts[1] if len(parts) > 1 else ""), "---".join(parts[2:])
        title = re.search(r"^# (.+)$", head, re.M).group(1).strip()
    else:                                                    # 리뷰 · 음악 · 애니: 제목+본문 · 나머지
        m = re.search(r"^# (.+)$", head, re.M)
        if not m:
            return None
        title = m.group(1).strip()
        body, rest = head[m.end():], "---".join(parts[1:])
    body = body.strip()
    if not body or title in ("", "제목"):
        return None
    if any(path.name.startswith(k) for k in 빼는글):
        return None
    if subtopic is None:                                     # 애니: 사람_… / 작품리뷰_… / 제작이야기_… / 신작소식_…
        subtopic = path.name.split("_")[0]
        if subtopic in 빼는유형:
            return None
    if topic_id == "topic-anime" and _만든때(path, head) < 애니_시작일:
        return None
    rel = path.relative_to(OUT).as_posix()
    cid = f"{TOPIC_DIR[topic_id]}-" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
    sources = [{"id": f"{cid}-s{i + 1}", **s} for i, s in enumerate(_출처(rest))]
    return {
        "id": cid,
        "topicId": topic_id,
        "topicName": topic_name,
        "subtopic": subtopic,
        "title": title,
        "summary": _첫문장(body),
        "body": body,
        "sourceName": "me;nu AI 에이전트",
        "sources": sources,
        "saved": False,
        "work": _작품(rest, head),
        "agent": rel.split("/")[0],
        "agentVersion": _판(text),
        "createdAt": _만든때(path, head),
        "chars": len(body.replace("\n", "")),
        "origin": rel,
    }


def main():
    # 팀원마다 out/ 이 다르다. 이미 올라간 글(contents.json)은 두고, 내 out/ 의 올림 글만 id로 더하거나 덮어쓴다
    json_path = DEST / "contents.json"
    old = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else []
    merged = {it["id"]: it for it in old}
    mine, dropped = [], []
    for folder, topic_id, topic_name, sub in FOLDERS:
        for p in sorted((OUT / folder).glob("*.md")):
            it = 읽기(p, topic_id, topic_name, sub)
            if it:
                mine.append(it)
            else:                                            # 내 원본이 올림에서 빠졌으면 전에 올린 것도 뺀다
                dropped.append(p.relative_to(OUT).as_posix())
    added = sum(it["id"] not in merged for it in mine)
    for it in old:
        if it["origin"] in dropped:
            del merged[it["id"]]
            (DEST / TOPIC_DIR[it["topicId"]] / f"{it['id']}.md").unlink(missing_ok=True)
    merged.update({it["id"]: it for it in mine})
    items = sorted(merged.values(), key=lambda x: (x["topicId"], x["createdAt"]))
    for it in mine:                                          # 남의 .md는 건드리지 않는다
        d = DEST / TOPIC_DIR[it["topicId"]]
        d.mkdir(parents=True, exist_ok=True)
        src = "\n".join(f"- {s['name']}" + (f" — {s['url']}" if s["url"] else "") for s in it["sources"])
        meta = f"{it['topicName']} · {it['subtopic']}" + (f" · {it['work']}" if it["work"] else "") + f" · {it['agentVersion']} · {it['createdAt'][:10]}"
        (d / f"{it['id']}.md").write_text(f"# {it['title']}\n\n{meta}\n\n{it['body']}\n\n## 출처\n{src}\n", encoding="utf-8")
    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    세기 = {}
    for it in items:
        k = f"{it['topicName']} · {it['subtopic']}"
        세기[k] = 세기.get(k, 0) + 1
    빠짐 = len(old) + added - len(items)
    print(f"내 올림 글 {len(mine)}편 (새로 {added} · 덮어씀 {len(mine) - added} · 뺌 {빠짐})")
    print(f"전체 {len(items)}편 → {DEST}")
    for k, n in sorted(세기.items()):
        print(f"  {k}: {n}편")


if __name__ == "__main__":
    main()
