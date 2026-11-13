"""Runtime upgrades must preserve existing credentials byte for byte."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import setup_local


class SetupLocalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="menu-setup-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.destination = self.root / ".env"

    def test_new_credentials_are_distinct_bounded_and_not_rotated(self):
        self.assertEqual(setup_local.initialize(self.destination), "created")
        initial = self.destination.read_bytes()
        values = dict(line.split("=", 1) for line in initial.decode().splitlines() if "=" in line)
        self.assertRegex(values["FLOWER_BASIC_AUTH"], r"^operator:[0-9a-f]{48}$")
        self.assertRegex(values["CONTENT_JOB_DB_PASSWORD"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(values["FLOWER_BASIC_AUTH"].partition(":")[2], values["CONTENT_JOB_DB_PASSWORD"])
        self.assertEqual(setup_local.initialize(self.destination), "unchanged")
        self.assertEqual(self.destination.read_bytes(), initial)

    def test_legacy_config_gets_only_one_appended_password(self):
        initial = (
            b"# keep this comment\r\n"
            b"FLOWER_BASIC_AUTH=existing:unchanged-secret\r\n"
            b"FLOWER_PORT=5556\r\n"
            b"CELERY_BEAT_INTERVAL_SECONDS=60"
        )
        self.destination.write_bytes(initial)
        self.assertEqual(setup_local.initialize(self.destination), "updated")
        upgraded = self.destination.read_bytes()
        self.assertTrue(upgraded.startswith(initial))
        self.assertRegex(upgraded[len(initial):], rb"^\nCONTENT_JOB_DB_PASSWORD=[0-9a-f]{64}\n$")
        self.assertEqual(setup_local.initialize(self.destination), "unchanged")
        self.assertEqual(self.destination.read_bytes(), upgraded)

    def test_existing_jobs_password_is_preserved_without_rewriting(self):
        initial = (
            b"FLOWER_BASIC_AUTH=existing:keep\r\n"
            b"CONTENT_JOB_DB_PASSWORD=" + b"A" * 64 + b"\r\n# trailing comment\r\n"
        )
        self.destination.write_bytes(initial)
        self.assertEqual(setup_local.initialize(self.destination), "unchanged")
        self.assertEqual(self.destination.read_bytes(), initial)

    def test_invalid_empty_or_duplicate_jobs_password_is_never_replaced(self):
        for assignment in (
            "CONTENT_JOB_DB_PASSWORD=\n",
            "CONTENT_JOB_DB_PASSWORD=bad:password\n",
            "CONTENT_JOB_DB_PASSWORD=" + "a" * 64 + "\nCONTENT_JOB_DB_PASSWORD=" + "b" * 64 + "\n",
        ):
            with self.subTest(kind=assignment.split("=", 1)[0]):
                initial = ("FLOWER_BASIC_AUTH=existing:keep\n" + assignment).encode()
                self.destination.write_bytes(initial)
                with self.assertRaises(ValueError):
                    setup_local.initialize(self.destination)
                self.assertEqual(self.destination.read_bytes(), initial)

    def test_concurrent_initializer_cannot_append_while_lock_is_held(self):
        initial = b"FLOWER_BASIC_AUTH=existing:keep\n"
        self.destination.write_bytes(initial)
        with setup_local.setup_lock(self.destination):
            with self.assertRaises(OSError):
                setup_local.initialize(self.destination)
            self.assertEqual(self.destination.read_bytes(), initial)
        self.assertEqual(setup_local.initialize(self.destination), "updated")
        self.assertEqual(self.destination.read_bytes().count(b"CONTENT_JOB_DB_PASSWORD="), 1)

    def test_linked_configuration_does_not_modify_the_link_target(self):
        target = self.root / "external.env"
        initial = b"FLOWER_BASIC_AUTH=external:keep\n"
        target.write_bytes(initial)
        try:
            self.destination.symlink_to(target)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        with self.assertRaises(ValueError):
            setup_local.initialize(self.destination)
        self.assertEqual(target.read_bytes(), initial)

    def test_main_does_not_print_any_credential(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(setup_local, "__file__", str(self.root / "setup_local.py")):
            with redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(setup_local.main(), 0)
        data = self.destination.read_text()
        values = re.findall(r"(?:FLOWER_BASIC_AUTH|CONTENT_JOB_DB_PASSWORD)=(.+)", data)
        for value in values:
            self.assertNotIn(value, output.getvalue() + errors.getvalue())
        self.assertIn("No secrets were printed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
