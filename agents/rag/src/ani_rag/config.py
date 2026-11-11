"""설정 읽기 — agents/rag/.env (값은 화면에 찍지 않는다)

.env 칸
- MENU_DICT_DB_USER · MENU_DICT_DB_PASSWORD : 사전 DB (compose.yaml 의 menu-dict, 포트 5436)
- MODEL_DB_ENV : 모델 DB 설정 파일 위치 (ANIME_DB_USER · ANIME_DB_PASSWORD 가 든 model/db/.env).
  비밀번호를 복사하지 않고 그 파일을 그 자리에서 읽는다
- MENU_CATALOG_DIR : 작품 목록 폴더 (menu_works.csv · menu_work_entries.csv). 비우면 레포 맨 위의 catalog
- KMRB_SERVICE_KEY : 공공데이터포털 인증키 (영상물등급위원회 영화 등급분류 · 자체등급분류 API)
- KOBIS_API_KEY : 영화관입장권 통합전산망 오픈 API 키 (일본 애니 극장판 한국 관객 수)
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

from psycopg.conninfo import make_conninfo

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2]  # agents/rag
REPO_ROOT = PACKAGE_ROOT.parents[1]


def read_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _need(env: dict[str, str], key: str, where: str) -> str:
    value = env.get(key, "")
    if not value:
        raise SystemExit(f"{where} 에 {key} 값이 없음")
    return value


@dataclass(frozen=True)
class Settings:
    dict_conninfo: str = field(repr=False)
    model_conninfo: str | None = field(repr=False)
    catalog_dir: pathlib.Path
    kmrb_key: str | None = field(default=None, repr=False)  # 공공데이터포털 인증키 (영상물등급위원회)
    kobis_key: str | None = field(default=None, repr=False)  # 영화관입장권 통합전산망 오픈 API 키


def load_settings(*, need_model_db: bool = False) -> Settings:
    env = read_env_file(PACKAGE_ROOT / ".env")
    prefixes = ("MENU_DICT_", "MODEL_DB_", "MENU_CATALOG_", "KMRB_", "KOBIS_")
    env.update({k: v for k, v in os.environ.items() if k.startswith(prefixes)})

    dict_conninfo = make_conninfo(
        host=env.get("MENU_DICT_DB_HOST", "127.0.0.1"),
        port=env.get("MENU_DICT_DB_PORT", "5436"),
        dbname="menu_dict",
        user=_need(env, "MENU_DICT_DB_USER", ".env"),
        password=_need(env, "MENU_DICT_DB_PASSWORD", ".env"),
    )

    model_conninfo = None
    if need_model_db:
        model_env = read_env_file(pathlib.Path(_need(env, "MODEL_DB_ENV", ".env")))
        model_conninfo = make_conninfo(
            host=env.get("MODEL_DB_HOST", "127.0.0.1"),
            port=env.get("MODEL_DB_PORT", "5435"),
            dbname="anime_db",
            user=_need(model_env, "ANIME_DB_USER", "MODEL_DB_ENV 파일"),
            password=_need(model_env, "ANIME_DB_PASSWORD", "MODEL_DB_ENV 파일"),
        )

    catalog_dir = pathlib.Path(env.get("MENU_CATALOG_DIR") or REPO_ROOT / "catalog")
    return Settings(dict_conninfo, model_conninfo, catalog_dir, env.get("KMRB_SERVICE_KEY") or None,
                    env.get("KOBIS_API_KEY") or None)
