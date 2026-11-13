"""11/13 새벽 생산 현황 페이지 (읽기만): 돌고 있는 생산 · 아침에 넣을 글 · 시범 · 남은 유형. 30초마다 다시 씀
  python -I <이 파일>      → agents/rag/.cache/reports/production_status.html
일꾼이 하나도 없으면 마지막으로 한 번 쓰고 끝난다
"""
import html
import json
import pathlib
import statistics
import subprocess
import time

CACHE = pathlib.Path(__file__).resolve().parents[2] / ".cache"
RUNS = CACHE / "content" / "runs"
OUT = CACHE / "reports" / "production_status.html"
HERE = pathlib.Path(__file__).parent

# (이름, 글감 폴더, 결과 이름, 표지 · 형식, 만들 글 수(없으면 글감 수))
RUNNING = [
    ("주연 소개", "mass23", "mass-1113i",
     "표지: 주연 얼굴 바둑판(어둡게) 가운데 원작 애니 포스터 · 흰 테두리 얇게 6px (11/13 낮 서원)", None),
    ("팬 추천", "mass22", "mass-1113L3",
     "표지: 추천작 바둑판(어둡게) 가운데 원작 애니 포스터 · 겹침 규칙 (2편까지 · 추천작 4번까지 · 8위 안)", 508),
    ("입문 가이드 (그림 보강)", "mass19", "mass-1113c",
     "그림 모자라 빠졌던 것 · AniList 시즌 표지 + 원작 책 표지 · 4~5장 · 본 성우 고르기", None),
    ("주제가 (AniList 시즌 표지로 다시)", "mass18", "mass-1113t7",
     "그림이 늘어난 49개만 · 그 시즌 노래 카드에 그 시즌 표지", len((HERE / "themes_improved.txt").read_text(encoding="utf-8").split())),
]
# 오늘 만들어 아침에 서버에 넣을 것 (이름, 결과 이름들, 비고)
READY = [
    ("성우", ["mass-1112v"], "캐릭터 그림"),
    ("입문 가이드", ["mass-1112g"], "작품 포스터 · 5장"),
    ("소재 모음", ["mass-1112u"], "포스터 바둑판 · 둘째 줄 5가지"),
    ("원작 이야기", ["mass-1112r", "mass-1112r2"], "원작 만화책 표지 · 4장"),
    ("이달의 생일", ["mass-1112m"], "캐릭터 얼굴 · 2장"),
    ("주제가", ["mass-1112t3", "mass-1112t5"], "음악 재생 화면 · 나무위키 작은 글씨 80장"),
    ("주제가 (그림 돌려 쓰기)", ["mass-1112t4", "mass-1112t6"], "그림 1~2장 작품 · 49개는 AniList 판으로 바뀌는 중"),
    ("주제가 서버 옛 글 새 양식 (-v2)", ["mass-1112v2"], "옛 글 10개는 넣은 뒤 숨김"),
]
TRIALS = [
    ("팬 추천 시범", ["mass-1113L", "mass-1113L2"], "시범 끝 → 시안 C 로 생산했으나 01:47 디스크가 꽉 차서 188개에서 멈춤"),
]
TODO = [  # 아직 안 만든 것 (만들 글, 상황)
    ("생일 모음", "100", "표지 방식 서원이 다시 생각 중 (11/13 낮)"),
    ("성우 (표지 겹친 것)", "16", "안쪽 카드마다 그 캐릭터 글이라 그림만 맞바꾸면 어긋남 → 안 겹치는 캐릭터를 표지로 정해 다시 만들기"),
    ("원작 이야기 (그림 2장뿐)", "36", "책 표지 + 포스터 2장뿐이라 4장을 못 채움 → AniList 시즌 표지 · 관련 만화 표지를 보태면 됨"),
    ("입문 가이드 (그림 3장 이하)", "35", "AniList 를 더해도 모자라 빠짐"),
    ("개인 생일 (2주 안)", "16", "이달의 생일과 같은 캐릭터라 안 만들기 추천"),
    ("가수", "9", "남은 글감 (형식 정해짐)"),
]
LATER = [
    "로마자만 있는 가수 883명 ('3 Nen E Gumi Utatan') 한국어 이름 채우기",
    "정보사전 가수 이름 고치기 (즛토마요 · 활동명 · 잘못 붙은 YUI · miwa) — 지금은 글마다 바꿔 끼움",
    "이미 만든 주제가 글도 AniList 시즌 표지로 바꿀지 (베타 뒤)",
]
FINISHED = "프로필 · 감독 · 제작사 · 한국 성우 · 흥행 · 작가 · 수상 · 장소 (서버에 있음)"


def results(stamps: list[str]) -> list[dict]:
    out = []
    for stamp in stamps:
        for f in RUNS.glob(f"{stamp}-p*/results.json"):
            try:
                out += json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass  # 일꾼이 쓰는 중
    best: dict[str, dict] = {}
    for r in out:  # 같은 글이 오류 뒤 다시 돌면 마지막 상태 (뒤 묶음이 앞을 덮음)
        if r["request_id"] not in best or r["status"] != "error":
            best[r["request_id"]] = r
    return list(best.values())


def workers() -> dict[str, int]:
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*run_part.py*' }"
           " | ForEach-Object { $_.CommandLine }")
    try:
        text = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                              timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    counts: dict[str, int] = {}
    for line in text.splitlines():
        for _, root, _, _, _ in RUNNING:
            if f".cache/{root} " in line or f".cache/{root}\"" in line or line.rstrip().endswith(f".cache/{root}"):
                counts[root] = counts.get(root, 0) + 1
    return {k: max(1, v // 2) for k, v in counts.items()}  # uv 가 python 을 두 번 띄움


def bar(done: int, total: int) -> str:
    pct = 0 if not total else min(100, round(done * 100 / total))
    return f'<div class="bar"><span style="width:{pct}%"></span></div><small>{pct}%</small>'


def page(alive: dict[str, int]) -> str:
    rows, spent_all, morning = [], 0.0, 0
    for name, root, stamp, form, target in RUNNING:
        total = target or len(list((CACHE / root / "manifests").glob("*.json")))
        building = not (CACHE / root / "manifests_done.flag").exists()
        rs = results([stamp])
        ok = [r for r in rs if r["status"] == "ready_for_feed"]
        bad = sum(1 for r in rs if r["status"] in ("rejected_archived", "needs_material"))
        err = sum(1 for r in rs if r["status"] == "error")
        spent = sum(r.get("cost") or 0 for r in rs)
        spent_all += spent
        morning += len(ok)
        left = max(0, total - len(ok) - bad)
        secs = [r["seconds"] for r in rs if r.get("seconds")]
        n = alive.get(root, 0)
        eta = (f"약 {round(left * statistics.median(secs) / n / 60)}분" if left and n and secs
               else ("끝" if not left else "-"))
        state = "생산 중" if n else ("끝" if not left else "멈춤")
        rows.append(f"<tr><td><b>{html.escape(name)}</b><br><small>{html.escape(form)}</small></td>"
                    f"<td>{total}{f' (글감 {len(list((CACHE / root / "manifests").glob("*.json")))}개 만듦)' if building else ''}</td><td class=ok>{len(ok)}</td>"
                    f"<td>{bad}</td><td>{err}</td><td>{left}</td><td>{bar(len(ok) + bad, total)}</td>"
                    f"<td>{n}</td><td>{state}<br><small>{eta}</small></td><td>${spent:.2f}</td></tr>")
    ready_rows = []
    for name, stamps, note in READY:
        rs = results(stamps)
        ok = sum(1 for r in rs if r["status"] == "ready_for_feed")
        spent = sum(r.get("cost") or 0 for r in rs)
        spent_all += spent
        morning += ok
        ready_rows.append(f"<tr><td><b>{html.escape(name)}</b><br><small>{html.escape(note)}</small></td>"
                          f"<td class=ok>{ok}</td><td>${spent:.2f}</td></tr>")
    trial_rows = []
    for name, stamps, note in TRIALS:
        rs = results(stamps)
        ok = sum(1 for r in rs if r["status"] == "ready_for_feed")
        spent_all += sum(r.get("cost") or 0 for r in rs)
        trial_rows.append(f"<tr><td><b>{html.escape(name)}</b></td><td class=ok>{ok}</td><td>{html.escape(note)}</td></tr>")
    todo = "".join(f"<tr><td><b>{html.escape(n)}</b></td><td>{c}</td><td>{html.escape(p)}</td></tr>" for n, c, p in TODO)
    later = "".join(f"<li>{html.escape(x)}</li>" for x in LATER)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>생산 현황</title><style>
:root{{--bg:#f7f7f8;--card:#fff;--text:#1d1d22;--muted:#6b6b76;--line:#e4e4ea;--accent:#4f46e5;--ok:#0f8a5f}}
@media (prefers-color-scheme: dark){{:root{{--bg:#141418;--card:#1e1e24;--text:#ececf1;--muted:#a0a0ad;--line:#2e2e37;--accent:#8b85ff;--ok:#3ccf91}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 60px}} h1{{margin:0 0 4px;font-size:24px}}
h2{{font-size:17px;margin:28px 0 10px}} .muted,small{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:16px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}}
.kpi b{{display:block;font-size:24px}} table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}}
th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-size:13px;color:var(--muted);font-weight:600}}
.ok{{color:var(--ok);font-weight:700}} .bar{{height:8px;background:var(--line);border-radius:4px;overflow:hidden;min-width:90px}}
.bar span{{display:block;height:100%;background:var(--accent)}} ul{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 18px 12px 30px;margin:0}}
li{{margin:4px 0}} .wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>콘텐츠 생산 현황</h1><div class=muted>{time.strftime('%m/%d %H:%M:%S')} 기준 · 30초마다 새로 고침</div>
<div class=cards><div class=kpi><small>아침에 넣을 글 (지금까지 통과, 겹침 거르기 전)</small><b>{morning:,}</b></div>
<div class=kpi><small>서버에 이미 있는 글</small><b>1,364</b></div>
<div class=kpi><small>11/12~13 생산 비용 (이 페이지 묶음)</small><b>${spent_all:.2f}</b></div></div>
<h2>지금 만드는 중</h2><div class=wrap><table><tr><th>유형</th><th>만들 글</th><th>통과</th><th>떨어짐</th><th>오류</th><th>남음</th><th>진행</th><th>일꾼</th><th>상태</th><th>비용</th></tr>{''.join(rows)}</table></div>
<h2>만들어 두고 아침에 서버에 넣을 것</h2><table><tr><th>유형</th><th>통과</th><th>비용</th></tr>{''.join(ready_rows)}</table>
<p class=muted>묶을 때: 서버 글 · 새 글끼리 표지 같은 것 보류, 가수 이름 바로잡기, 소재 모음 둘째 줄, 나무위키 작은 글씨. 넣은 뒤 서버 옛 글 11개 숨김.</p>
<h2>시범</h2><table><tr><th>유형</th><th>통과</th><th>상황</th></tr>{''.join(trial_rows)}</table>
<h2>남은 유형</h2><table><tr><th>유형</th><th>만들 글</th><th>상황</th></tr>{todo}</table>
<h2>나중 할 일</h2><ul>{later}</ul>
<h2>끝난 유형</h2><p>{FINISHED}</p>
</main></body></html>"""


OUT.parent.mkdir(parents=True, exist_ok=True)
empty = 0
while True:
    alive = workers()
    OUT.write_text(page(alive), encoding="utf-8")
    # 11/13 낮: 일꾼 찾기가 한 번 실패(시간 초과 등)해서 생산 중에 멈춘 적이 있음 → 세 번 연속 비어야 끝냄
    empty = 0 if alive else empty + 1
    if empty >= 3:
        break
    time.sleep(30)
print(OUT)
