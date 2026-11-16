"""Run inside the generation image with --network none and a writable /tmp."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from PIL import Image


def main():
    root = Path("/opt/ani-agent")
    python = "/opt/ani-venv/bin/python"
    if (root / ".env").exists() or Path("/run/secrets/anime_llm_key").exists():
        raise RuntimeError("Run this image check without a key file")
    for source in (root / "config/sources.json", root / "config/source_type_map.json",
                   Path("/catalog/menu_works.csv")):
        if source.read_bytes() != (Path("/app/anime-catalog") / source.name).read_bytes():
            raise RuntimeError("Generation and admission catalogs differ")
    # Caller-provided credentials, session stores, and tracing settings stay outside the smoke child.
    with TemporaryDirectory() as temporary:
        env = {
            "PATH": os.defpath, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "HOME": temporary,
            "ANI_MENU_DATABASE_BACKEND": "sqlite", "ANI_MENU_DATABASE_PATH": temporary + "/smoke.sqlite3",
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
        }
        output = Path(temporary) / "mismatched-work.json"
        mismatch = subprocess.run(
            [python, "-m", "ani_menu_agent.cli", "crawl-generate", "--source", "one_piece_official",
             "--url", "https://one-piece.com/news/82812/index.html", "--content-type", "goods",
             "--expected-work-id", "1", "--output", str(output)],
            cwd=root, env={**env, "NO_COLOR": "1", "COLUMNS": "160"}, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )
        if mismatch.returncode != 2 or "does not match the registered source" not in mismatch.stderr or output.exists():
            raise RuntimeError("Installed crawl-generate CLI did not reject the mismatched work ID")
        render_check = '''
import sys
from PIL import Image, ImageDraw
from ani_menu_agent.agents.content_generation.renderer import _font
from ani_menu_agent.sources import load_source_registry
from ani_menu_agent.work_catalog import load_work_entries

assert load_source_registry().automatic_sources
assert load_work_entries()
font_image = Image.new("RGB", (320, 120), "white")
draw = ImageDraw.Draw(font_image)
for y, bold in ((2, False), (60, True)):
    draw.text((2, y), "한글 카드", fill="black", font=_font(32, bold=bold))
font_image.save(sys.argv[1], "PNG")
'''
        png = Path(temporary) / "font-smoke.png"
        subprocess.run([python, "-c", render_check, str(png)], cwd=root, env=env, check=True, timeout=60)
        with Image.open(png) as image:
            image.verify()
        subprocess.run([python, "-m", "ani_menu_agent.cli", "graph-smoke"], cwd=root, env=env,
                       check=True, timeout=120)
    print("Generation image: CLI, registry, catalog parity, Korean fonts, PNG and offline graph checks passed.")


if __name__ == "__main__":
    main()
