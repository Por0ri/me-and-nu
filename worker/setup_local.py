"""Create runtime credentials and append only a missing jobs database password."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import sys


@contextmanager
def setup_lock(destination: Path):
    # Keep one stable lock inode: a crashed initializer releases the OS lock.
    lock_path = destination.with_name(destination.name + ".setup.lock")
    if lock_path.is_symlink():
        raise ValueError("Refusing a linked setup lock.")
    with lock_path.open("a+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def initialize(destination: Path) -> str:
    with setup_lock(destination):
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise ValueError("Runtime configuration must be a regular file.")
        if not destination.exists():
            # Exclusive creation never overwrites an existing Flower credential.
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    "# Local Celery runtime only; do not commit this file.\n"
                    f"FLOWER_BASIC_AUTH=operator:{secrets.token_hex(24)}\n"
                    "FLOWER_PORT=5555\n"
                    "CELERY_BEAT_INTERVAL_SECONDS=30\n"
                    f"CONTENT_JOB_DB_PASSWORD={secrets.token_hex(32)}\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            return "created"

        original = destination.read_bytes()
        entries = []
        for line in original.decode("utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip().removeprefix("export ").strip() == "CONTENT_JOB_DB_PASSWORD":
                entries.append(value.strip())
        if entries:
            if len(entries) != 1 or not re.fullmatch(r"[0-9a-fA-F]{64}", entries[0]):
                raise ValueError("Existing jobs password must be one nonempty 64-character hex value; it was not replaced.")
            return "unchanged"

        # Append bytes without rewriting any existing setting or Flower credential.
        suffix = (b"\n" if original and not original.endswith((b"\n", b"\r")) else b"")
        suffix += f"CONTENT_JOB_DB_PASSWORD={secrets.token_hex(32)}\n".encode("ascii")
        with destination.open("ab") as stream:
            stream.write(suffix)
            stream.flush()
            os.fsync(stream.fileno())
        destination.chmod(0o600)
        return "updated"


def main() -> int:
    try:
        outcome = initialize(Path(__file__).resolve().parent / ".env")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print("Runtime setup failed. Check file permissions or another initializer; existing credentials were not replaced.", file=sys.stderr)
        return 1
    messages = {
        "created": "Created worker/.env with local Flower and jobs credentials. No secrets were printed.",
        "updated": "Added the missing jobs password to worker/.env. Existing Flower credentials were preserved.",
        "unchanged": "Existing worker/.env preserved. No changes made.",
    }
    print(messages[outcome])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
