"""Run one existing anime agent in a new isolated output directory.

This launches the agent's unchanged CLI. It never imports into the service DB,
creates publication approval, or substitutes another backend credential.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

from dotenv import dotenv_values


class GenerationError(ValueError):
    """An operator-safe error without credentials or provider response bodies."""


def credential(path: Path | None, inherited: dict[str, str]) -> str:
    values = dotenv_values(path, interpolate=False) if path is not None else inherited
    key = values.get("OPENAI_API_KEY") or values.get("LLM_KEY")
    if not isinstance(key, str) or not key or any(char.isspace() for char in key) or key[0] in "\"'" or key[-1] in "\"'":
        raise GenerationError("Provide a valid agent credential file or process credential.")
    return key


def child_environment(inherited: dict[str, str], key: str, output: Path) -> dict[str, str]:
    # Only the two explicitly selected model names may be inherited. Other agent
    # runtime settings must not attach this run to an old database or review bypass.
    env = {name: value for name, value in inherited.items()
           if not name.startswith(("ANI_MENU_", "OPENAI_", "LANGCHAIN_", "LANGSMITH_"))
           and name not in {"LLM_KEY", "DATABASE_URL"}}
    for name in ("ANI_MENU_MODEL", "ANI_MENU_VISUAL_LAYOUT_MODEL"):
        if inherited.get(name):
            env[name] = inherited[name]
    env.update({
        "OPENAI_API_KEY": key, "LLM_KEY": key,
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
        "ANI_MENU_DATABASE_BACKEND": "sqlite",
        "ANI_MENU_DATABASE_PATH": str(output / "agent.sqlite3"),
        "ANI_MENU_DATABASE_URL": "", "ANI_MENU_DUPLICATE_BASELINE_PATH": "",
        "ANI_MENU_REVIEW_PROFILE": "standard", "ANI_MENU_MAX_REVISION": "3",
    })
    return env


def redact(text: str, key: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_*\-]{6,}", "[redacted]", text.replace(key, "[redacted]"))


def run(*, agent_root: Path, output: Path, source: str, url: str,
        content_type: str, credentials_file: Path | None = None,
        agent_python: Path | None = None, max_images: int = 4,
        timeout_seconds: int = 1200, inherited: dict[str, str] | None = None) -> dict:
    agent_root = agent_root.resolve(strict=True)
    output = output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise GenerationError("Choose a new output directory below an existing parent.")
    if output.parent.resolve() != output.parent or output.is_relative_to(agent_root):
        raise GenerationError("Use an output directory outside the agent checkout and without linked parents.")
    if not (agent_root / "src" / "ani_menu_agent" / "cli.py").is_file():
        raise GenerationError("The existing anime agent checkout is required.")
    if (agent_root / ".env").exists():
        raise GenerationError("Use an isolated source copy without an implicit agent .env file.")
    executable = agent_python or agent_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file():
        raise GenerationError("The existing agent Python environment is required.")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise GenerationError("Provide a registered HTTPS source URL without credentials or fragment.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source) or not 1 <= max_images <= 20 or not 1 <= timeout_seconds <= 3600:
        raise GenerationError("Invalid source or execution bounds.")
    inherited = dict(os.environ) if inherited is None else inherited
    key = credential(credentials_file, inherited)
    output.mkdir()
    env = child_environment(inherited, key, output)
    # An existing virtualenv may point its editable installation at another
    # checkout. Always import the explicitly selected, unchanged agent source.
    env["PYTHONPATH"] = str(agent_root / "src")
    result_path = output / "result.json"
    command = [str(executable), "-B", "-m", "ani_menu_agent.cli", "crawl-generate",
               "--source", source, "--url", url, "--content-type", content_type,
               "--max-images", str(max_images), "--output", str(result_path)]
    try:
        completed = subprocess.run(command, cwd=agent_root, env=env, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds,
                                   check=False)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run terminates and waits for the child; do not auto-retry a paid run.
        log = "\n".join(value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
                        for value in (exc.stdout, exc.stderr))
        (output / "agent.log").write_text(redact(log, key), encoding="utf-8")
        raise GenerationError("Agent timed out; preserved output must be inspected before any retry.") from None
    (output / "agent.log").write_text(redact(completed.stdout + "\n" + completed.stderr, key), encoding="utf-8")
    if completed.returncode != 0:
        raise GenerationError("Agent failed; inspect the redacted local log. No automatic retry or service import ran.")
    if not result_path.is_file() or result_path.stat().st_size > 16 * 1024 * 1024:
        raise GenerationError("The agent did not export a bounded result JSON.")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        status = result.get("status")
        request_id = result.get("package", {}).get("request_id")
    except (ValueError, AttributeError, TypeError):
        raise GenerationError("The agent result JSON is invalid.") from None
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", request_id):
        raise GenerationError("The agent did not export a safe request identity.")
    summary = {"status": "generated_requires_staging" if status == "ready_for_feed" else "held_by_agent",
               "result": str(result_path), "cards": str(output / "cards" / request_id),
               "readyForStaging": status == "ready_for_feed", "serviceDatabaseChanged": False,
               "operatorReviewCreated": False}
    (output / "generation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-root", type=Path, required=True)
    parser.add_argument("--agent-python", type=Path)
    parser.add_argument("--credentials-file", type=Path, help="Explicit agent env file; backend env files are never searched.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--content-type", choices=("news", "event", "guide", "people", "goods", "behind", "review", "humor", "character", "curation", "music"), required=True)
    parser.add_argument("--max-images", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = vars(parser.parse_args())
    args["output"] = args.pop("output_dir")
    try:
        summary = run(**args)
    except (GenerationError, OSError):
        # Paths and provider messages may contain credentials; only fixed error
        # categories are printed here. Detailed child output remains redacted.
        print("Generation stopped. Check the input paths, agent credential, and redacted local log.", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["readyForStaging"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
