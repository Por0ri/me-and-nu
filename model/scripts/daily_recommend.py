# daily_recommend — 서비스 DB 의 유저마다 오늘 볼 글 순서(100개)를 계산해 추천 결과 표에 넣는다 (서비스 연결 5단계)
#
# 읽기: 온보딩 답(작품 · 칩 · 덕질 방식) · 활동 기록(user_event) · 반응 상태(content_reaction · saved_item) · 글 · 글↔작품
# 계산: v0.5 작품 취향 + v1 글 순위 (유저 테스트용 고정). 매번 가입부터 지금까지의 기록을 처음부터 다시 반영
# 쓰기: recommendation_round(유저 · 날짜 · 버전) + recommendation_item(순위 · 점수 · 이유). 백엔드 피드가 오늘(없으면 어제) 것을 씀
# 오늘 추천이 이미 있는 유저는 건너뜀 (앱에 보인 순서가 하루 중에 안 바뀌게). 날짜는 한국 시간
#
# 기본은 미리보기 (DB 에 안 씀). --expected-database 에 DB 이름을 정확히 적어야 함.
# DB 주소는 --database-url · 환경 변수 DATABASE_URL · backend/.env 순서로 찾고 화면에 안 찍음
#
#   backend/.venv/Scripts/python.exe model/scripts/daily_recommend.py --expected-database me_nu_model_check
#   ... --apply                              # 오늘 추천이 없는 유저만 계산해서 씀
#   ... --apply --replace                    # 오늘 추천을 지우고 다시 씀 (고칠 때만)
#   ... --apply --every 30 --daily-at 06:00  # 켜 두기: 30초마다 새 가입자, 매일 06:00 부터는 그날 전체
#   ... --context model/data/service_context.pkl.gz  # 서버: 원본 대신 통계 문맥 파일 (model/scripts/save_context.py)
#   ... --preview checking                     # 검토 전 미리보기 캐러셀도 (Checking. recommend.sh 가 API 설정을 보고 붙임)

import argparse
import json
import os
import pathlib
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from model.algo.preflight import check_inputs, require_inputs  # noqa: E402
from model.algo.preferences import apply_chip, load_chip_features, unmapped_summary  # noqa: E402

try:
    import psycopg
    from psycopg.types.json import Jsonb
    from model.algo import anime_ranker as ar, content_v1 as v1, service
except ModuleNotFoundError as exc:
    if exc.name not in {"numpy", "psycopg"}:
        raise
    raise SystemExit("Recommendation dependencies are missing. Install model/requirements.txt in a separate environment; "
                     "run python -m model.algo.preflight for an offline input/dependency report.") from None

KST = timezone(timedelta(hours=9))
TOPIC = "anime"
TOP_N = 100                               # 유저마다 하루 순위 개수 (EXP-30)
VERSION = "v1-usertest"                   # recommendation_round.algorithm_version. 알고리즘을 바꾸면 새 이름
LOCK = (20261029, 1)                      # 쓰기 잠금: 명령이 두 개 돌아도 한쪽씩 씀
CAROUSEL_METHOD = "carousel_import_v1"    # 백엔드 carousel_repository 와 같은 값
CHIPS = REPO / "model" / "catalog" / "chips.csv"

# 피드에 보일 수 있는 글 (백엔드 recommendation_repository._visible 과 같은 조건)
# 캐러셀 검수 = carousel_repository._carousel_review_condition: 미리보기가 아니면 검수 통과 (confirmed),
# 검토 전 미리보기 (needs_review)는 그 범위가 켜졌을 때만 (--preview). 피드는 백엔드가 같은 조건으로 다시 거른다
PREVIEW_SCOPES = {"checking": "checking_test_preview", "local": "local_test_preview"}
VISIBLE = """(c.status = 'active' AND c.deleted_at IS NULL
    AND (c.feed_valid_until IS NULL OR c.feed_valid_until > now())
    AND EXISTS (SELECT 1 FROM content_source s WHERE s.content_id = c.content_id)
    AND (COALESCE(c.ai_judgment_basis ->> 'method', '') <> %(carousel)s OR (
        ((NOT COALESCE((c.ai_judgment_basis ->> 'preview')::boolean, false) AND c.judgment_status = 'confirmed')
         OR (%(preview_scope)s::text IS NOT NULL AND COALESCE((c.ai_judgment_basis ->> 'preview')::boolean, false)
             AND c.judgment_status = 'needs_review'
             AND c.ai_judgment_basis -> %(preview_scope)s::text ->> 'database' = %(preview_database)s::text
             AND c.ai_judgment_basis -> %(preview_scope)s::text ->> 'quality_approval' = 'not_reviewed'
             AND (%(preview_scope)s::text <> 'checking_test_preview'
                  OR c.ai_judgment_basis -> 'checking_test_preview' ->> 'environment' = 'checking')))
        AND c.feed_valid_until IS NOT NULL
        AND (SELECT count(*) FROM content_slide sl WHERE sl.content_id = c.content_id) BETWEEN 3 AND 10
        AND (SELECT max(sl.page) FROM content_slide sl WHERE sl.content_id = c.content_id)
            = (SELECT count(*) FROM content_slide sl WHERE sl.content_id = c.content_id))))"""


def database_url(given, expected):
    # DB 주소를 찾고 이름이 --expected-database 와 같은지 확인. 주소는 어디에도 안 찍음
    url = given or os.environ.get("DATABASE_URL")
    env = REPO / "backend" / ".env"
    if not url and env.exists():
        url = next((line.split("=", 1)[1].strip().strip("\"'") for line in env.read_text(encoding="utf-8").splitlines()
                    if line.startswith("DATABASE_URL=")), None)
    if not url:
        sys.exit("DB 주소가 없음: --database-url · 환경 변수 DATABASE_URL · backend/.env 중 하나가 필요")
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", url)
    name = urlsplit(url).path.lstrip("/")
    if name != expected or name in {"postgres", "template0", "template1"}:
        sys.exit("DB 이름이 --expected-database 와 다름 (주소는 안 찍음)")
    return url


def chip_names():
    # Exact catalog names plus explicit legacy aliases; stored IDs/kinds are unchanged.
    return load_chip_features(CHIPS)


def load_users(cur, topic_id, chips):
    """온보딩을 마친 애니 분야 유저 → {tap_id: 고른 작품 번호 · 장르 · 소재(AniList 이름) · 온보딩 답}"""
    cur.execute("""SELECT t.tap_id FROM tap t JOIN user_account u ON u.user_id = t.user_id
        WHERE t.topic_id = %s AND t.deleted_at IS NULL AND u.onboarding_completed_at IS NOT NULL
          AND u.withdrawn_at IS NULL AND u.account_status IN ('active', 'warned') ORDER BY t.tap_id""", (topic_id,))
    users = {tap: {"works": [], "genres": [], "themes": [], "answers": [], "unknown_chips": 0} for (tap,) in cur.fetchall()}
    taps = list(users)
    if not taps:
        return users
    cur.execute("""SELECT te.tap_id, x.external_id FROM tap_entity te
        JOIN entity_external_id x ON x.entity_id = te.entity_id AND x.provider = 'menu_work'
        WHERE te.tap_id = ANY(%s) ORDER BY te.tap_id, te.created_at, te.entity_id""", (taps,))
    for tap, ext in cur.fetchall():
        if ext.isdigit():
            users[tap]["works"].append(int(ext))
    cur.execute("""SELECT tp.tap_id, pt.kind, pt.code, pt.display_name, pt.tag_id FROM tap_preference_tag tp JOIN preference_tag pt ON pt.tag_id = tp.tag_id
        WHERE tp.tap_id = ANY(%s) ORDER BY tp.tap_id, pt.kind, pt.sort_order""", (taps,))
    for tap, kind, code, label, tag_id in cur.fetchall():
        apply_chip(users[tap], kind, code, label, tag_id, chips)
    cur.execute("SELECT tap_id, consumption_type, q1_choice, q2_choice FROM onboarding_answers WHERE tap_id = ANY(%s)", (taps,))
    for tap, *answers in cur.fetchall():
        users[tap]["answers"] = [a for a in answers if a]
    return users


def load_posts(cur, topic_id, today, preview=None):
    """유형이 있는 글 → (글 목록, content_id → 글 번호). v1 은 유형으로 점수를 매겨서 유형 없는 글은 뺌 (피드에선 추천 뒤 최신순).
    지금 피드에 못 보이는 글도 넣음: 예전에 반응한 글의 작품 · 유형 취향은 반영해야 해서. 대신 노출 끝을 어제로 둬서 순위엔 안 나옴.
    preview: (미리보기 범위, DB 이름) — 그 범위의 검토 전 미리보기 캐러셀도 보이는 글로 침 (main 의 --preview)"""
    scope, database = preview or (None, None)
    cur.execute(f"""SELECT c.content_id, c.content_type, c.title, COALESCE(c.published_at, c.created_at), c.feed_valid_until,
        {VISIBLE},
        (SELECT x.external_id FROM content_entity_link l
           JOIN entity_external_id x ON x.entity_id = l.entity_id AND x.provider = 'menu_work'
          WHERE l.content_id = c.content_id ORDER BY l.sort_order LIMIT 1)
        FROM content c WHERE c.topic_id = %(topic)s AND c.content_type = ANY(%(types)s) ORDER BY c.content_id""",
                {"carousel": CAROUSEL_METHOD, "topic": topic_id, "types": list(v1.TYPES),
                 "preview_scope": scope, "preview_database": database})
    posts = []
    for cid, ctype, title, published, valid_until, visible, work in cur.fetchall():
        pub = published.astimezone(KST).date()
        if not visible:
            until = today - timedelta(days=1)
        elif valid_until is not None:
            until = valid_until.astimezone(KST).date()
        else:
            until = service.exposure_end(ctype, pub)[0]
        posts.append({"id": cid, "type": ctype, "title": title, "published": pub, "until": until,
                      "menu_work_id": int(work) if work and work.isdigit() else None})
    return posts, {p["id"]: i for i, p in enumerate(posts)}


def load_visits(cur, taps, index):
    """기록 → 유저마다 접속 목록 [[(종류, 글 번호, 값), ...], ...] (시간 순, v1.learn 에 넣을 모양).
    활동 기록은 같은 session_id (없으면 같은 날)를 한 접속으로. 반응(좋아요 · 저장 · O/X)은 현재 상태 표에서 읽어 그날에 넣음.
    읽은 비율 = 카드 넘긴 장 수 / 전체 장 수 · 상세 스크롤 비율 중 큰 값 (접속마다 글 하나에 한 번)"""
    groups = {tap: {} for tap in taps}

    def visit(tap, key, at):
        g = groups[tap].setdefault(key, {"at": at, "events": [], "read": {}})
        g["at"] = min(g["at"], at)
        return g

    cur.execute("""SELECT tap_id, event_type, content_id, event_value, occurred_at FROM user_event
        WHERE tap_id = ANY(%s) AND data_source <> 'automated_test' AND event_type IN ('impression', 'open', 'card_swipe')
        ORDER BY occurred_at, user_event_id""", (taps,))
    for tap, kind, cid, value, at in cur.fetchall():
        c, value = index.get(cid), value or {}
        if c is None:
            continue
        g = visit(tap, ("s", value["session_id"]) if value.get("session_id") else ("d", at.astimezone(KST).date()), at)
        ratio = None
        if kind == "card_swipe":
            if value.get("card_index") and value.get("card_count"):
                ratio = min(value["card_index"] / value["card_count"], 1.0)
        else:
            g["events"].append((kind, c, None))
            if kind == "open" and value.get("scroll_pct") is not None:
                ratio = value["scroll_pct"] / 100
        if ratio is not None:
            g["read"][c] = max(g["read"].get(c, 0.0), ratio)

    reactions = []
    cur.execute("SELECT tap_id, content_id, is_liked, liked_at, ox_feedback, updated_at FROM content_reaction WHERE tap_id = ANY(%s)",
                (taps,))
    for tap, cid, liked, liked_at, ox, updated in cur.fetchall():
        if liked:
            reactions.append((tap, "like", cid, liked_at or updated))
        if ox in ("O", "X"):
            reactions.append((tap, "more" if ox == "O" else "less", cid, updated))
    cur.execute("SELECT tap_id, content_id, saved_at FROM saved_item WHERE tap_id = ANY(%s) AND status IN ('kept', 'resurface_off')",
                (taps,))
    reactions += [(tap, "save", cid, at) for tap, cid, at in cur.fetchall()]
    for tap, kind, cid, at in reactions:
        if cid in index:
            visit(tap, ("r", at.astimezone(KST).date()), at)["events"].append((kind, index[cid], None))
    return {tap: [g["events"] + [("read", c, r) for c, r in g["read"].items()]
                  for g in sorted(gs.values(), key=lambda g: g["at"])] for tap, gs in groups.items()}


def recommend(user, visits, ctx, feed, posts, day, t, cfg, catalog_ids):
    """유저 한 명 → [(글 번호, 점수, 이유), ...] 순위대로 (preview_feed.py 와 같은 계산 + 기록 반영)"""
    ob = service.onboarding_user(ctx, user["works"], user["genres"], user["themes"], t)
    state = v1.UserState(ob["history"], ob["genres"], ob["themes"], service.user_types(user["answers"]), t,
                         len(catalog_ids), len(posts))
    for events in visits:
        v1.learn(state, events, feed, cfg, catalog_ids)
    service.work_affinity(state, ctx, cfg)
    # 점수는 모든 글에 매기고, 보여 줄 순서 규칙 (고른 작품 먼저 · 같은 작품은 한 바퀴에 하나씩)을 적용한 뒤 TOP_N 개
    order = service.arrange(v1.rank_feed(state, feed, day, cfg, k=len(posts)).tolist(), feed, user["works"], ctx)
    out = []
    for i in order[:TOP_N]:
        parts = service.score_parts(state, feed, i, day, cfg)
        out.append((i, sum(parts), service.reason(parts, posts[i]["menu_work_id"], user["works"], posts[i]["type"], ctx)))
    return out


def write(conn, results, posts, today, replace):
    # 한 번에 씀 (잠금 + 트랜잭션). 같은 유저 · 날짜 · 버전이 이미 있으면 그 유저는 건너뜀 (다른 명령이 먼저 쓴 경우)
    written = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", LOCK)
        for tap, ranked in results.items():
            if replace:
                cur.execute("DELETE FROM recommendation_round WHERE tap_id = %s AND service_date = %s AND algorithm_version = %s",
                            (tap, today, VERSION))
            cur.execute("""INSERT INTO recommendation_round (tap_id, service_date, algorithm_version) VALUES (%s, %s, %s)
                ON CONFLICT (tap_id, service_date, algorithm_version) DO NOTHING RETURNING recommendation_round_id""",
                        (tap, today, VERSION))
            row = cur.fetchone()
            if row is None:
                continue
            cur.executemany("""INSERT INTO recommendation_item (recommendation_round_id, content_id, rank, score, recommend_reason)
                VALUES (%s, %s, %s, %s, %s)""",
                            [(row[0], posts[i]["id"], rank, score, Jsonb(why)) for rank, (i, score, why) in enumerate(ranked, 1)])
            written += 1
    return written


def run_once(url, world, today, *, apply, replace, new_only, show, preview=None):
    """한 번 계산. new_only = 오늘 · 어제 추천이 둘 다 없는 유저만 (켜 두기 모드에서 매일 계산 시각 전). preview: load_posts 참고"""
    ctx, cfg, catalog_ids, chips = world
    day = today.toordinal() - 719163                                # service.make_feed 와 같은 날 번호
    t = ar.to_seconds(datetime(today.year, today.month, today.day))
    with psycopg.connect(url, autocommit=True) as conn:
        if not apply:
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        with conn.cursor() as cur:
            cur.execute("SELECT topic_id FROM topic WHERE topic_code = %s", (TOPIC,))
            row = cur.fetchone()
            if row is None:
                sys.exit(f"{TOPIC} Topic 이 없음")
            users = load_users(cur, row[0], chips)
            cur.execute("""SELECT tap_id, service_date FROM recommendation_round
                WHERE algorithm_version = %s AND service_date IN (%s, %s)""", (VERSION, today, today - timedelta(days=1)))
            have = {}
            for tap, d in cur.fetchall():
                have.setdefault(tap, set()).add(d)
            targets = [u for u in users if replace or (not have.get(u) if new_only else today not in have.get(u, ()))]
            if not targets:
                return 0
            posts, index = load_posts(cur, row[0], today, preview)
            visits = load_visits(cur, targets, index)
        diagnostics = unmapped_summary({tap: users[tap] for tap in targets})
        if diagnostics:
            print("Unmapped preferences (original DB choices retained; no guessed model feature): "
                  + json.dumps(diagnostics, ensure_ascii=False))
        feed = service.make_feed(posts, ctx)
        results = {tap: recommend(users[tap], visits[tap], ctx, feed, posts, day, t, cfg, catalog_ids) for tap in targets}
        live = int(((feed["day"] <= day) & (feed["until"] >= day)).sum())
        print(f"[{datetime.now(KST):%m-%d %H:%M}] {today} · 유저 {len(targets)}명 계산 (전체 {len(users)}) · "
              f"글 {len(posts)}개 중 오늘 노출 {live}개")
        for tap in targets[:show]:
            u = users[tap]
            print(f"  tap {tap}: 작품 {len(u['works'])} · 장르 {u['genres']} · 소재 {u['themes']} · "
                  f"유형 {service.user_types(u['answers'])} · 접속 {len(visits[tap])}"
                  + (f" · 표에 없는 칩 {u['unknown_chips']}" if u["unknown_chips"] else ""))
            for rank, (i, score, why) in enumerate(results[tap][:5], 1):
                print(f"    {rank}. [{posts[i]['type']}] {posts[i]['title'][:30]} — {why['text']} ({score:.2f})")
        if apply:
            print(f"  → 추천 {write(conn, results, posts, today, replace)}명 저장")
    return len(targets)


def main():
    ap = argparse.ArgumentParser(description="서비스 DB 유저마다 오늘 볼 글 순서를 계산해 추천 결과 표에 넣는다")
    ap.add_argument("--expected-database", help="쓸 DB 이름 (DB 모드에서 필수, 주소 말고 이름만)")
    ap.add_argument("--preflight", action="store_true", help="입력/의존성 존재만 검사; DB·.env 접근 없음")
    ap.add_argument("--self-test", action="store_true", help="합성 문맥 + 메모리 SQLite 저장 어댑터 검증; 실제 DB 접근 없음")
    ap.add_argument("--database-url", help="DB 주소 (화면에 안 찍음). 없으면 DATABASE_URL · backend/.env")
    ap.add_argument("--apply", action="store_true", help="DB 에 씀. 없으면 미리보기")
    ap.add_argument("--replace", action="store_true", help="오늘 추천을 지우고 다시 씀 (고칠 때만)")
    ap.add_argument("--today", help="기준 날짜 YYYY-MM-DD (시험용. 기본 오늘 한국 시간)")
    ap.add_argument("--every", type=int, help="켜 두기: N초마다 반복")
    ap.add_argument("--daily-at", default="06:00", help="켜 두기: 이 시각(한국)부터 그날 전체 계산. 그 전엔 새 가입자만")
    ap.add_argument("--show", type=int, default=3, help="화면에 보여 줄 유저 수")
    ap.add_argument("--context", help="서버용 통계 문맥 파일 (save_context.py 로 만든 것). 주면 원본 자료 없이 이 파일로 계산")
    ap.add_argument("--preview", choices=sorted(PREVIEW_SCOPES),
                    help="검토 전 미리보기 캐러셀도 보이는 글로 침 (백엔드 피드와 같게). checking = Checking 서버 미리보기가 켜졌을 때, "
                         "local = 로컬 시험 DB")
    args = ap.parse_args()
    if args.preflight or args.self_test:
        if (args.preflight and args.self_test) or args.apply or args.replace or args.every is not None or args.today or args.database_url or args.expected_database:
            ap.error("--preflight / --self-test 는 서로 또는 DB 실행 인자와 같이 쓸 수 없음")
        if args.preflight:
            report = check_inputs(context=args.context)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise SystemExit(0 if report["ready"] else 2)
        from model.tests.storage_adapter import self_test
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    if not args.expected_database:
        ap.error("DB 모드에는 --expected-database 필요")
    if args.every is not None and args.every <= 0:
        ap.error("--every 는 1초 이상이어야 함")
    if args.every and (args.replace or args.today):
        ap.error("--every 는 --replace · --today 와 같이 못 씀")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", args.daily_at):
        ap.error("--daily-at 은 유효한 24시간 시각 (예: 06:00)")
    if args.today:
        try:
            date.fromisoformat(args.today)
        except ValueError:
            ap.error("--today 는 유효한 YYYY-MM-DD 날짜")
    report = require_inputs(args.context)  # fail before database_url can read backend/.env
    for warning in report["warnings"]:
        print("Preflight: " + warning)
    url = database_url(args.database_url, args.expected_database)
    preview = None
    if args.preview == "checking":                                  # 백엔드 carousel_preview: Checking 전용 DB 만
        if args.expected_database != "me_nu_checking":
            ap.error("--preview checking 은 me_nu_checking DB 에서만")
        preview = (PREVIEW_SCOPES["checking"], "me_nu_checking")
    elif args.preview == "local":                                   # 백엔드 local_test_database: 내 PC 시험 DB 만
        host, name = urlsplit(url).hostname, args.expected_database
        if host not in {"localhost", "127.0.0.1", "::1"} or not (name.startswith("test_") or name.endswith(("_test", "_e2e"))):
            ap.error("--preview local 은 내 PC 의 시험 DB (이름 test_* · *_test · *_e2e)에서만")
        preview = (PREVIEW_SCOPES["local"], name)
    sys.stdout.reconfigure(line_buffering=True)                     # 켜 두기 모드에서도 기록이 바로 찍히게 (로그 파일 포함)
    if preview:
        print(f"미리보기 글 포함: {preview[0]}")

    t0 = time.time()
    ctx = service.load_context(args.context) if args.context else service.build()
    print(f"문맥 준비 ({'파일' if args.context else '원본'}) {time.time() - t0:.0f}초 · 작품 {len(ctx['ids']):,} · 버전 {VERSION}")
    world = (ctx, v1.Config(logs=service.LOGS), ctx["ids"][ctx["cw_rows"]], chip_names())
    while True:
        now = datetime.now(KST)
        today = date.fromisoformat(args.today) if args.today else now.date()
        try:
            run_once(url, world, today, apply=args.apply, replace=args.replace,
                     new_only=bool(args.every) and now.strftime("%H:%M") < args.daily_at, show=args.show, preview=preview)
        except psycopg.OperationalError as e:
            if not args.every:
                raise
            print(f"[{now:%m-%d %H:%M}] DB 연결 실패 ({type(e).__name__}) → 다음에 다시")
        if not args.every:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
