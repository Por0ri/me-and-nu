from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_configuration_is_initialized():
    config_path = Path("alembic.ini")

    assert config_path.is_file()
    assert Path("alembic/env.py").is_file()
    assert Path("alembic/versions").is_dir()

    config = Config(config_path)
    script = ScriptDirectory.from_config(config)

    assert Path(script.dir).resolve() == Path("alembic").resolve()


def test_alembic_ini_does_not_contain_database_credentials():
    config_text = Path("alembic.ini").read_text(encoding="utf-8")

    assert "driver://user:pass" not in config_text
    assert "DATABASE_URL" in config_text
