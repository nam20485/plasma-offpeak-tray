"""Unit tests for scripts/offpeak.py — run: python3 -m unittest discover -s tests -v"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import offpeak  # noqa: E402

EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "config" / "schedules.example.json"
EXAMPLE = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def schedule(**over):
    sch = {
        "name": "T", "kind": "peak", "timezone": "Asia/Shanghai",
        "offsetMinutes": 480, "days": ["Mon"],
        "start": "10:00", "end": "12:00",
    }
    sch.update(over)
    return sch


def invalid_doc(doc):
    try:
        offpeak.validate_config(doc)
    except ValueError:
        return True
    return False


class ValidateConfigTests(unittest.TestCase):
    def test_example_config_valid(self):
        schedules = offpeak.validate_config(EXAMPLE)
        self.assertEqual(len(schedules), 2)

    def test_rejects_missing_schedules(self):
        self.assertTrue(invalid_doc({}))

    def test_rejects_empty_schedules(self):
        self.assertTrue(invalid_doc({"schedules": []}))

    def test_rejects_non_object_schedule(self):
        self.assertTrue(invalid_doc({"schedules": ["nope"]}))

    def test_rejects_missing_field(self):
        sch = schedule()
        del sch["days"]
        self.assertTrue(invalid_doc({"schedules": [sch]}))

    def test_rejects_empty_name(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(name=" ")]}))

    def test_rejects_bad_kind(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(kind="cheap")]}))

    def test_rejects_bad_time_format(self):
        for value in ("9:00", "24:00", "10:5", "1000", "10:60"):
            self.assertTrue(invalid_doc({"schedules": [schedule(start=value)]}), value)

    def test_rejects_unknown_day(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(days=["Yesterday"])]}))

    def test_rejects_empty_days(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(days=[])]}))

    def test_rejects_duplicate_days(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(days=["Mon", "Mon"])]}))

    def test_rejects_out_of_range_offset(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(offsetMinutes=999)]}))

    def test_rejects_bool_offset(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(offsetMinutes=True)]}))

    def test_rejects_non_http_docs_url(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(docsUrl="ftp://example.com")]}))

    def test_accepts_optional_docs_url(self):
        schedules = offpeak.validate_config({"schedules": [schedule(docsUrl="https://x.example")]})
        self.assertEqual(schedules[0]["docsUrl"], "https://x.example")

    def test_accepts_optional_subtext(self):
        schedules = offpeak.validate_config({"schedules": [schedule(subtext="half price at night")]})
        self.assertEqual(schedules[0]["subtext"], "half price at night")

    def test_rejects_non_string_subtext(self):
        self.assertTrue(invalid_doc({"schedules": [schedule(subtext=42)]}))


class CalendarSpecTests(unittest.TestCase):
    def setUp(self):
        self.glm = EXAMPLE["schedules"][0]
        self.qwen = EXAMPLE["schedules"][1]

    def test_glm_start(self):
        self.assertEqual(offpeak.calendar_spec(self.glm, "start"),
                         "Mon..Fri *-*-* 14:00:00 Asia/Singapore")

    def test_glm_end_same_day(self):
        self.assertEqual(offpeak.calendar_spec(self.glm, "end"),
                         "Mon..Fri *-*-* 18:00:00 Asia/Singapore")

    def test_qwen_start_all_days_omits_day_field(self):
        self.assertEqual(offpeak.calendar_spec(self.qwen, "start"),
                         "*-*-* 22:00:00 Asia/Shanghai")

    def test_qwen_end_all_days_stays_all_days(self):
        self.assertEqual(offpeak.calendar_spec(self.qwen, "end"),
                         "*-*-* 08:00:00 Asia/Shanghai")

    def test_crossing_end_shifts_weekdays(self):
        sch = schedule(days=["Mon", "Tue", "Wed", "Thu", "Fri"],
                       start="22:00", end="06:00")
        self.assertEqual(offpeak.calendar_spec(sch, "end"),
                         "Tue,Wed,Thu,Fri,Sat *-*-* 06:00:00 Asia/Shanghai")

    def test_single_day(self):
        self.assertEqual(offpeak.calendar_spec(schedule(days=["Wed"]), "start"),
                         "Wed *-*-* 10:00:00 Asia/Shanghai")


class GenUnitsTests(unittest.TestCase):
    def setUp(self):
        self.units = offpeak.gen_units(EXAMPLE["schedules"])
        self.by_base = {u["base"]: u for u in self.units}

    def test_unit_names(self):
        self.assertEqual(
            set(self.by_base),
            {"offpeak-tray-z-ai-glm-start", "offpeak-tray-z-ai-glm-end",
             "offpeak-tray-qwen-start", "offpeak-tray-qwen-end"})

    def test_service_sends_notification(self):
        svc = self.by_base["offpeak-tray-z-ai-glm-start"]["service"]
        self.assertIn("/usr/bin/notify-send", svc)
        self.assertIn("-u critical", svc)  # peak start is the alarming one
        self.assertIn("ExecCondition=/usr/bin/pgrep -x plasmashell", svc)
        self.assertIn("Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus", svc)

    def test_offpeak_start_is_normal_urgency(self):
        svc = self.by_base["offpeak-tray-qwen-start"]["service"]
        self.assertIn("-u normal", svc)

    def test_timer_contents(self):
        timer = self.by_base["offpeak-tray-qwen-end"]["timer"]
        self.assertIn("OnCalendar=*-*-* 08:00:00 Asia/Shanghai", timer)
        self.assertIn("WantedBy=timers.target", timer)

    def test_percent_is_escaped_for_systemd(self):
        units = {u["base"]: u for u in offpeak.gen_units([schedule(name="50% Off")])}
        svc = units["offpeak-tray-50-off-start"]["service"]
        self.assertIn("50%% Off", svc)
        self.assertNotIn('"50% ', svc)

    def test_boundary_messages_non_empty(self):
        for sch in EXAMPLE["schedules"]:
            for boundary in ("start", "end"):
                summary, body, urgency = offpeak.boundary_message(sch, boundary)
                self.assertTrue(summary)
                self.assertTrue(body)
                self.assertIn(urgency, ("critical", "normal"))


class SlugifyTests(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(offpeak.slugify("Z.AI GLM"), "z-ai-glm")
        self.assertEqual(offpeak.slugify("Qwen"), "qwen")

    def test_all_symbol_name_falls_back(self):
        self.assertEqual(offpeak.slugify("///"), "schedule")


class ParseHMTests(unittest.TestCase):
    def test_parses(self):
        self.assertEqual(offpeak.parse_hm("00:00"), 0)
        self.assertEqual(offpeak.parse_hm("23:59"), 23 * 60 + 59)
        self.assertEqual(offpeak.parse_hm("08:30"), 8 * 60 + 30)

    def test_rejects_invalid(self):
        with self.assertRaises(ValueError):
            offpeak.parse_hm("8:30")


if __name__ == "__main__":
    unittest.main()
