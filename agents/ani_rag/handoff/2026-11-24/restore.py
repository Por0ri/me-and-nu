"""Restore and verify the RAG handoff into a new directory (Python 3)."""
import argparse
import hashlib
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

PACKAGE = Path(__file__).resolve().parent
ARCHIVE = "me-nu-rag.tar.gz."
BUFFER_SIZE = 1024 * 1024


class JoinedParts:
    def __init__(self, parts):
        self.parts = iter(parts)
        self.current = None

    def read(self, size):
        data = bytearray()
        while len(data) < size:
            if self.current is None:
                path = next(self.parts, None)
                if path is None:
                    break
                self.current = path.open("rb")
            chunk = self.current.read(size - len(data))
            if chunk:
                data.extend(chunk)
            else:
                self.close()
        return bytes(data)

    def close(self):
        if self.current is not None:
            self.current.close()
            self.current = None


def restore(destination):
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Choose a new directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    parts = sorted(PACKAGE.glob(ARCHIVE + "[0-9][0-9][0-9]"))
    if not parts or any(p.name != ARCHIVE + f"{i:03d}" for i, p in enumerate(parts, 1)):
        raise ValueError("Missing archive parts; run git lfs pull")
    with parts[0].open("rb") as first:
        if first.read(2) != b"\x1f\x8b":
            raise ValueError("Download the Git LFS objects first (see README.md)")
    checks = {}
    for line in (PACKAGE / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        checks[name] = digest
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".rag-restore-") as tmp:
        stage = Path(tmp) / "snapshot"
        stage.mkdir()
        joined = JoinedParts(parts)
        try:
            with tarfile.open(fileobj=joined, mode="r|gz", bufsize=BUFFER_SIZE) as archive:
                for member in archive:
                    name = member.name
                    if name not in checks or not member.isfile():
                        raise ValueError(f"Unexpected archive member: {name}")
                    path = PurePosixPath(name)
                    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                        raise ValueError(f"Invalid archive path: {name}")
                    target = stage.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.extractfile(member) as source, target.open("xb") as output:
                        while True:
                            chunk = source.read(BUFFER_SIZE)
                            if not chunk:
                                break
                            output.write(chunk)
                            digest.update(chunk)
                    if digest.hexdigest() != checks.pop(name):
                        raise ValueError(f"SHA-256 mismatch: {name}")
        finally:
            joined.close()
        if checks:
            raise ValueError(f"Archive incomplete; {len(checks)} files missing")
        stage.rename(destination)
    print(f"Verified and restored: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New output directory")
    restore(parser.parse_args().destination)
