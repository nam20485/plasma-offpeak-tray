"""Unit tests for skills/find-offpeak-windows/scripts/update_schedule.py.

Run: python3 -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "find-offpeak-windows" / "scripts"))

import update_schedule  # noqa: E402

ENTRY = {
    "name": "Test Provider",
    "kind": "offPeak",
    "timezone": "Asia/Shanghai",
    "offsetMinutes": 480,
    "days": ["Mon", "Tue"],
    "start": "22:00",
    "end": "08:00",
    "docsUrl": "https://example.com/pricing",
    "subtext": "test discount",
}


class UpdateScheduleTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "schedules.json"

    def write_config(self, schedules):
        self.config.write_text(
            json.dumps({"schedules": schedules}, indent=4) + "\n", encoding="utf-8")

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = update_schedule.main(list(argv))
        return code, out.getvalue()

    def entry_file(self, entry, name="entry.json"):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(entry), encoding="utf-8")
        return str(path)


class ProposeTests(UpdateScheduleTestBase):
    def test_propose_appends_without_writing(self):
        self.write_config([])
        code, out = self.run_main("propose", self.entry_file(ENTRY), "--config", str(self.config))
        self.assertEqual(code, 0)
        self.assertIn("Test Provider", out)
        self.assertIn("nothing written", out)
        self.assertIn("appends", out)
        doc = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(doc["schedules"], [])  # unchanged

    def test_propose_replace_notice(self):
        self.write_config([dict(ENTRY, start="23:00")])
        code, out = self.run_main("propose", self.entry_file(ENTRY), "--config", str(self.config))
        self.assertEqual(code, 0)
        self.assertIn("replaces", out)
        self.assertIn('-            "start": "23:00"', out)

    def test_propose_invalid_entry_fails(self):
        self.write_config([])
        bad = self.entry_file(dict(ENTRY, kind="cheap"), "bad.json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self.run_main("propose", bad, "--config", str(self.config))[0]
        self.assertEqual(code, 1)
        self.assertIn("kind", err.getvalue())


class ApplyTests(UpdateScheduleTestBase):
    def setUp(self):
        super().setUp()
        # Never touch the host systemd from tests: fake the installer call.
        fake = mock.Mock(returncode=0, stdout="install ok\n", stderr="")
        patcher = mock.patch.object(update_schedule.subprocess, "run", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_apply_appends_and_backs_up(self):
        self.write_config([{"name": "Existing", "kind": "peak", "timezone": "UTC",
                            "offsetMinutes": 0, "days": ["Mon"], "start": "01:00",
                            "end": "02:00"}])
        old_text = self.config.read_text(encoding="utf-8")
        code, out = self.run_main("apply", self.entry_file(ENTRY), "--config", str(self.config))
        self.assertEqual(code, 0, out)
        self.assertIn("backup:", out)
        backups = list(self.config.parent.glob("schedules.json.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), old_text)
        doc = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual([s["name"] for s in doc["schedules"]], ["Existing", "Test Provider"])

    def test_apply_replaces_by_name(self):
        self.write_config([dict(ENTRY, start="23:00")])
        code, out = self.run_main("apply", self.entry_file(ENTRY), "--config", str(self.config))
        self.assertEqual(code, 0, out)
        doc = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(len(doc["schedules"]), 1)
        self.assertEqual(doc["schedules"][0]["start"], "22:00")

    def test_apply_creates_missing_config_without_backup(self):
        code, out = self.run_main("apply", self.entry_file(ENTRY), "--config", str(self.config))
        self.assertEqual(code, 0, out)
        self.assertNotIn("backup:", out)
        self.assertEqual(
            json.loads(self.config.read_text(encoding="utf-8"))["schedules"][0]["name"],
            "Test Provider")

    def test_apply_invalid_entry_writes_nothing(self):
        self.write_config([])
        bad = self.entry_file(dict(ENTRY, days=["Funday"]), "bad.json")
        with contextlib.redirect_stderr(io.StringIO()):
            code = self.run_main("apply", bad, "--config", str(self.config))[0]
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["schedules"], [])
        self.assertEqual(list(self.config.parent.glob("*.bak-*")), [])


class MergeAndRenderTests(unittest.TestCase):
    def test_merge_does_not_mutate_input(self):
        doc = {"schedules": [dict(ENTRY, start="23:00")]}
        new = update_schedule.merge_entry(doc, ENTRY)
        self.assertEqual(doc["schedules"][0]["start"], "23:00")
        self.assertEqual(new["schedules"][0]["start"], "22:00")

    def test_render_trailing_newline_and_indent(self):
        text = update_schedule.render({"schedules": []})
        self.assertTrue(text.endswith("\n"))
        self.assertIn('\n    "schedules"', text)


if __name__ == "__main__":
    unittest.main()
