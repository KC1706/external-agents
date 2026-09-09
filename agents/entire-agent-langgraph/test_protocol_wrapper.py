"""Regression tests for the wrapper's session sidecar behavior."""

import base64
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.jsonl"
        self.original = b'{"type":"user","content":"first"}\n'
        self.path.write_bytes(self.original)
        self.agent = Path(__file__).parent.name.removeprefix("entire-agent-")

    def command(self, command, payload):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("protocol_wrapper.py")),
             self.agent, command],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
        )
        return json.loads(result.stdout) if result.stdout else None

    def restore(self):
        self.command("write-session", {
            "session_id": "test-session", "session_ref": str(self.path),
            "agent_name": self.agent, "repo_path": self.temp.name,
            "start_time": "2026-09-09T12:00:00Z",
            "native_data": base64.b64encode(self.original).decode("ascii"),
        })

    def read(self):
        return self.command("read-session", {
            "session_id": "test-session", "session_ref": str(self.path),
        })

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions")
    def test_restore_creates_private_files(self):
        self.path.unlink()
        old_umask = os.umask(0)
        try:
            self.restore()
        finally:
            os.umask(old_umask)
        for path in (self.path, self.path.with_suffix(".jsonl.session.json")):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions")
    def test_restore_makes_existing_files_private(self):
        self.restore()
        paths = (self.path, self.path.with_suffix(".jsonl.session.json"))
        for path in paths:
            path.chmod(0o644)
        self.restore()
        for path in paths:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_sidecar_does_not_duplicate_transcript(self):
        self.restore()
        metadata = json.loads(self.path.with_suffix(".jsonl.session.json").read_text())
        self.assertNotIn("native_data", metadata)
        self.assertEqual(base64.b64decode(self.read()["native_data"]), self.original)

    def test_absent_native_data_preserves_existing_session(self):
        self.restore()
        sidecar = self.path.with_suffix(".jsonl.session.json")
        saved_metadata = sidecar.read_bytes()
        for native in ({}, {"native_data": None}):
            with self.subTest(native=native):
                self.command("write-session", {"session_ref": str(self.path), **native})
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual(sidecar.read_bytes(), saved_metadata)

    def test_absent_native_data_does_not_create_transcript(self):
        self.path.unlink()
        self.command("write-session", {"session_ref": str(self.path), "native_data": None})
        self.assertFalse(self.path.exists())

    def test_explicit_empty_native_data_restores_empty_transcript(self):
        self.command("write-session", {"session_ref": str(self.path), "native_data": ""})
        self.assertEqual(self.path.read_bytes(), b"")

    def test_read_after_append_round_trips_current_transcript(self):
        self.restore()
        current = self.original + b'{"type":"assistant","content":"later"}\n'
        self.path.write_bytes(current)
        session = self.read()
        self.assertEqual(base64.b64decode(session["native_data"]), current)
        self.assertEqual(session["session_id"], "test-session")
        self.assertEqual(session["start_time"], "2026-09-09T12:00:00Z")
        self.command("write-session", session)
        self.assertEqual(self.path.read_bytes(), current)

    def test_read_missing_transcript_does_not_resurrect_sidecar_bytes(self):
        self.restore()
        self.path.unlink()
        session = self.read()
        self.assertIsNone(session["native_data"])
        self.command("write-session", session)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
