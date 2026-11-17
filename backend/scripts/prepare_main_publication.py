"""Build/validate a fixed publication plan from an existing read-only JSON export.

No database, network, key, migration or apply command is available here.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.main_publication_transition import build_plan, validate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="JSON object with rows, allowed_content_ids, as_of and operator_id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export = json.loads(args.export.read_text(encoding="utf-8"))
    plan = validate_plan(build_plan(export["rows"], export["allowed_content_ids"], as_of=export["as_of"], operator_id=export["operator_id"]))
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"manifest_sha256": plan["manifest_sha256"], "target_count": len(plan["entries"]),
        "existing_preview_count": sum(e["classification"] == "existing_checking_preview" for e in plan["entries"]),
        "already_approved_count": sum(e["classification"] == "already_approved" for e in plan["entries"]),
        "service_database_writes": 0, "llm_calls": 0}))


if __name__ == "__main__":
    main()
