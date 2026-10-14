# export_mal_sample — MAL 데이터에서 채점기용 표본을 뽑아 CSV로 저장한다
#
# MAL 데이터(anime_large 스키마, 2004~2022-03)는 로컬 Docker Postgres 에 있다.
# 2억 줄을 다 쓰면 무거우니 유저 일부만 뽑는다.
# - 유저: 다 본 작품 20~1000개, 2021년 이후 접속. md5 순서로 골라서 매번 같은 유저가 나온다
# - 기록: 그 유저들의 시청 기록 (보고 싶어요 plan_to_watch 는 뺌)
# - 작품: 전체 작품 정보, 작품끼리 관계(추천·속편 등)
#
#   python model/scripts/export_mal_sample.py            # 유저 20000명
#   python model/scripts/export_mal_sample.py 5000       # 유저 수 바꾸기
#   → model/data/mal_sample/ 아래 CSV 3개 (커밋 안 함)
#
# Docker Desktop 이 켜져 있어야 한다. 컨테이너 이름은 MENU_PG_CONTAINER 로 바꿀 수 있다.

import os
import pathlib
import subprocess
import sys

CONTAINER = os.environ.get("MENU_PG_CONTAINER", "menu-postgres")
DB = "menu_db"
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "mal_sample"

USERS_SQL = """
select user_id from anime_large."user"
where num_completed between 20 and 1000 and last_online_date >= '2021-01-01'
order by md5(user_id) limit {n}
"""

QUERIES = {
    "interactions.csv": """
        with u as ({users})
        select ua.user_id, ua.anime_id, ua.status, ua.score, ua.favorite,
               ua.last_interaction_date::timestamp as t
        from anime_large.user_anime ua join u using (user_id)
        where ua.status <> 'plan_to_watch' and ua.last_interaction_date is not null
        order by ua.user_id, t
    """,
    "anime.csv": """
        select anime_id, title, type, genres, left(start_date, 10) as start_date, members_count
        from anime_large.anime
    """,
    "anime_anime.csv": """
        select "animeA" as anime_a, "animeB" as anime_b, recommendation, related, relation_type
        from anime_large.anime_anime
        where recommendation = 1 or (related = 1 and relation_type <> 'Identity')
    """,
}


def copy_to_csv(sql, path):
    # docker exec 로 psql 의 COPY 결과를 그대로 파일에 쓴다 (비밀번호 설정 없이 동작)
    # SQL 은 따옴표가 많아서 명령줄 대신 표준 입력으로 넘긴다. \copy 는 한 줄이어야 한다
    line = f"\\copy ({' '.join(sql.split())}) to stdout with csv header\n"
    cmd = ["docker", "exec", "-i", CONTAINER, "sh", "-c",
           f'psql -U "$POSTGRES_USER" -d {DB} -v ON_ERROR_STOP=1 -q']
    with open(path, "wb") as f:
        subprocess.run(cmd, input=line.encode("utf-8"), stdout=f, check=True)


def main():
    n_users = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    OUT.mkdir(parents=True, exist_ok=True)
    users = " ".join(USERS_SQL.format(n=n_users).split())
    for fname, sql in QUERIES.items():
        path = OUT / fname
        print(f"{fname} 뽑는 중...", flush=True)
        copy_to_csv(sql.format(users=users), path)
        with open(path, encoding="utf-8") as f:
            rows = sum(1 for _ in f) - 1
        print(f"  → {rows:,}줄 ({path.stat().st_size / 1e6:.1f}MB)")


if __name__ == "__main__":
    main()
