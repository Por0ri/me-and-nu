"""Create local runtime credentials without reading or replacing existing env files."""
from pathlib import Path
import secrets


def main() -> None:
    destination = Path(__file__).resolve().parent / ".env"
    if destination.exists() or destination.is_symlink():
        print("Existing worker/.env preserved. No changes made.")
        return
    # Exclusive creation also protects against another initializer racing us.
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "# Local Celery runtime only; do not commit this file.\n"
            f"FLOWER_BASIC_AUTH=operator:{secrets.token_hex(24)}\n"
            "FLOWER_PORT=5555\n"
            "CELERY_BEAT_INTERVAL_SECONDS=30\n"
        )
    destination.chmod(0o600)
    print("Created worker/.env. Flower login is stored there; credentials were not printed.")


if __name__ == "__main__":
    main()
