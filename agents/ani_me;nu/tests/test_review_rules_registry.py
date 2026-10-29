import ast
import re
from pathlib import Path

from ani_menu_agent.agents.content_generation.detail_writer import DETAIL_WRITER_PROMPT
from ani_menu_agent.agents.reviewer.prompt import REVIEWER_PROMPT
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.prompts import COPY_WRITER
from ani_menu_agent.quality import score_quality
from ani_menu_agent.review_rules import RULES, decision_metadata, make_issue


def test_literal_issue_codes_are_registered() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "ani_menu_agent"
    emitted: set[str] = set()
    for path in root.rglob("*.py"):
        if path.name == "review_rules.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name not in {"_issue", "make_issue"}:
                continue
            code_node = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg == "code"), None
            )
            if isinstance(code_node, ast.Constant) and isinstance(code_node.value, str):
                emitted.add(code_node.value)
    assert emitted <= RULES.keys()


def test_every_registered_owner_has_a_real_route() -> None:
    valid_owners = {"copy_writer", "detail_writer", "image_curator", "layout_director", "crawler"}
    assert all(rule.owner in valid_owners for rule in RULES.values())
    assert all(rule.on_fail in {"revise_owner", "return_to_crawler"} for rule in RULES.values())
    assert all(
        rule.owner == "crawler"
        for rule in RULES.values()
        if rule.on_fail == "return_to_crawler"
    )
    assert decision_metadata([make_issue(code="BODY_TOO_LONG")])[1] == ["BODY_TOO_LONG"]
    assert decision_metadata([make_issue(code="EVENT_ALREADY_ENDED")])[2] is True


def test_warning_only_does_not_block_candidate() -> None:
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
    package = ContentPackage.model_validate_json(sample.read_text(encoding="utf-8"))
    report = score_quality(
        package,
        [make_issue(code="EVENT_LOCATION_MISSING")],
        language_score=5,
        safety_score=5,
        thresholds={"pass": 0, "card_minimum": 0, "safety_minimum_per_card": 0},
    )
    assert report["decision"] == "pass_candidate"
    assert report["warnings"] == ["EVENT_LOCATION_MISSING"]
    assert report["actionable_issues"] == []


def test_generation_and_reviewer_share_registered_rule_text() -> None:
    for rule in RULES.values():
        if rule.owner in {"copy_writer", "crawler"} and rule.check in {"both", "code", "llm"}:
            assert rule.rule in COPY_WRITER
            assert rule.rule in REVIEWER_PROMPT
        if rule.owner == "detail_writer":
            assert rule.rule in DETAIL_WRITER_PROMPT
            assert rule.rule in REVIEWER_PROMPT


def test_issue_owner_and_severity_are_not_hardcoded_outside_registry() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "ani_menu_agent"
    hardcode = re.compile(
        r"\b(?:owner|severity)\s*=\s*['\"](?:"
        r"copy_writer|detail_writer|image_curator|layout_director|crawler|"
        r"blocker|revise|warning)['\"]"
    )
    findings = []
    for path in root.rglob("*.py"):
        if path.name == "review_rules.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if hardcode.search(line):
                findings.append(f"{path}:{number}: {line.strip()}")
    assert findings == []
