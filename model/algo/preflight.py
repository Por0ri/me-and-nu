"""Offline model-input checks. Never connect to a database, read .env, or download."""

import importlib.util
import os
from pathlib import Path


def check_inputs(model_dir: Path | None = None, mal_dir: Path | None = None, context: Path | str | None = None) -> dict:
    model_dir = model_dir or Path(__file__).resolve().parents[1]
    mal_dir = mal_dir or Path(os.environ.get("MAL_SAMPLE_DIR", model_dir / "data" / "mal_sample"))
    required = [model_dir / "catalog" / name for name in ("chips.csv", "menu_works.csv", "menu_work_entries.csv")]
    if context:   # 서버: 원본 대신 통계 문맥 파일 하나 (algo/service.save_context)
        required.append(Path(context))
        missing = [str(path) for path in required if not path.is_file() or path.stat().st_size == 0]
        packages = [name for name in ("numpy", "psycopg") if importlib.util.find_spec(name) is None]
        return {"ready": not missing and not packages, "missing_files": missing,
                "missing_packages": packages, "warnings": [],
                "limits": "Presence/size checks only; the context file's origin and the real DB integration are not verified."}
    required += [model_dir / "data" / "anilist" / name for name in
                 ("media.jsonl", "media_recent.jsonl", "detail.jsonl", "links.jsonl")]
    required.append(mal_dir / "anime.csv")
    missing = [str(path) for path in required if not path.is_file() or path.stat().st_size == 0]
    csv_path, cache = mal_dir / "interactions.csv", mal_dir / "interactions.npz"
    valid_csv = csv_path.is_file() and csv_path.stat().st_size > 0
    valid_cache = cache.is_file() and cache.stat().st_size > 0
    if not valid_csv and not valid_cache:
        missing.append(f"{csv_path} OR {cache}")
    warnings = []
    if not (model_dir / "data" / "anilist_users" / "entries.csv").is_file():
        warnings.append("Optional AniList user sample is absent: MAL-only cowatch and zero recent popularity fallback.")
    if valid_csv and (not valid_cache or cache.stat().st_mtime < csv_path.stat().st_mtime):
        warnings.append("Loading interactions.csv will create/refresh interactions.npz; prepare a writable model-data directory.")
    packages = [name for name in ("numpy", "psycopg") if importlib.util.find_spec(name) is None]
    return {"ready": not missing and not packages, "missing_files": missing,
            "missing_packages": packages, "warnings": warnings,
            "limits": "Presence/size checks only; data format, quality, memory and real DB integration are not verified."}


def require_inputs(context: Path | str | None = None) -> dict:
    report = check_inputs(context=context)
    if not report["ready"]:
        lines = ["Recommendation preflight failed; no DB connection was attempted."]
        lines += [f"  missing input: {path}" for path in report["missing_files"]]
        if report["missing_packages"]:
            lines.append("  missing packages: " + ", ".join(report["missing_packages"]))
        lines.append("Provision the approved model data and install model/requirements.txt in a separate environment.")
        lines.append("For an offline synthetic adapter check only: python -m model.scripts.daily_recommend --self-test")
        raise SystemExit("\n".join(lines))
    return report


if __name__ == "__main__":
    import json
    import sys
    result = check_inputs()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ready"] else 2)
