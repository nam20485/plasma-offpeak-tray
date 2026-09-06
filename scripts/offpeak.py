#!/usr/bin/env python3
"""Off-Peak Tray tooling: validate the schedule config, generate systemd user
timers from it, and install/uninstall the plasmoid.

Language note: python3 rather than the linux-system-agent pwsh house default —
this is a Linux-only app and python3 ships on stock Debian/Ubuntu installs
where pwsh (and jq) do not (maintainer decision, 2026-09-05).

Usage:
    python3 scripts/offpeak.py validate [CONFIG]
    python3 scripts/offpeak.py gen-timers [CONFIG]   # dry-run: print units
    python3 scripts/offpeak.py install [--config CONFIG]
    python3 scripts/offpeak.py uninstall
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

DAY_ORDER = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
VALID_DAYS = frozenset(DAY_ORDER)
KINDS = frozenset({"peak", "offPeak"})
HM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
URL_RE = re.compile(r"^https?://")
UNIT_PREFIX = "offpeak-tray-"
PLASMOID_ID = "org.nam20485.offpeaktray"

SERVICE_TEMPLATE = """[Unit]
Description=Off-Peak Tray notification: {desc}

[Service]
Type=oneshot
ExecCondition=/usr/bin/pgrep -x plasmashell
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus
ExecStart=/usr/bin/notify-send -u {urgency} -a "Off-Peak Tray" "{summary}" "{body}"
"""

TIMER_TEMPLATE = """[Unit]
Description=Off-Peak Tray timer: {desc}

[Timer]
OnCalendar={spec}
AccuracySec=1min

[Install]
WantedBy=timers.target
"""


def parse_hm(value: str) -> int:
    if not HM_RE.match(value):
        raise ValueError(f"invalid HH:MM value {value!r}")
    return int(value[:2]) * 60 + int(value[3:])


def validate_config(doc: object) -> list[dict]:
    """Validate a parsed schedules document; return the schedules list.

    Raises ValueError with a human-readable message on any problem.
    """
    if (not isinstance(doc, dict) or not isinstance(doc.get("schedules"), list)
            or not doc["schedules"]):
        raise ValueError('config must be an object with a non-empty "schedules" array')
    for i, sch in enumerate(doc["schedules"]):
        label = f"schedules[{i}]"
        if not isinstance(sch, dict):
            raise ValueError(f"{label}: must be an object")
        for field in ("name", "kind", "timezone", "days", "start", "end", "offsetMinutes"):
            if field not in sch:
                raise ValueError(f"{label}: missing required field {field!r}")
        if not isinstance(sch["name"], str) or not sch["name"].strip():
            raise ValueError(f"{label}.name: non-empty string required")
        if sch["kind"] not in KINDS:
            raise ValueError(f"{label}.kind: must be one of {sorted(KINDS)}")
        if not isinstance(sch["timezone"], str) or not sch["timezone"].strip():
            raise ValueError(f"{label}.timezone: non-empty IANA name required")
        for field in ("start", "end"):
            if not isinstance(sch[field], str) or not HM_RE.match(sch[field]):
                raise ValueError(f"{label}.{field}: must be HH:MM in 00:00-23:59")
        days = sch["days"]
        if (not isinstance(days, list) or not days
                or not all(isinstance(d, str) for d in days)
                or not set(days) <= VALID_DAYS
                or len(set(days)) != len(days)):
            raise ValueError(f"{label}.days: non-empty list of unique day names {DAY_ORDER}")
        off = sch["offsetMinutes"]
        if not isinstance(off, int) or isinstance(off, bool) or not -720 <= off <= 840:
            raise ValueError(f"{label}.offsetMinutes: integer minutes in [-720, 840] required")
        url = sch.get("docsUrl")
        if url is not None and (not isinstance(url, str) or not URL_RE.match(url)):
            raise ValueError(f"{label}.docsUrl: optional http(s):// URL required")
        subtext = sch.get("subtext")
        if subtext is not None and not isinstance(subtext, str):
            raise ValueError(f"{label}.subtext: optional string required")
    return doc["schedules"]


def day_field(days: list[str]) -> str:
    ordered = [d for d in DAY_ORDER if d in days]
    if len(ordered) == 7:
        return ""
    if ordered == DAY_ORDER[1:6]:
        return "Mon..Fri "
    return ",".join(ordered) + " "


def shift_days(days: list[str]) -> list[str]:
    return [DAY_ORDER[(DAY_ORDER.index(d) + 1) % 7] for d in days]


def calendar_spec(sch: dict, boundary: str) -> str:
    """OnCalendar expression. The end boundary of a midnight-crossing window
    lands on the day after a start day, so its day set shifts forward one."""
    time = sch["start"] if boundary == "start" else sch["end"]
    days = sch["days"]
    if boundary == "end" and parse_hm(sch["end"]) <= parse_hm(sch["start"]):
        days = shift_days(days)
    return f"{day_field(days)}*-*-* {time}:00 {sch['timezone']}"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "schedule"


def boundary_message(sch: dict, boundary: str) -> tuple[str, str, str]:
    name, tz = sch["name"], sch["timezone"]
    if sch["kind"] == "peak":
        if boundary == "start":
            return (f"{name}: peak hours started",
                    f"Elevated quota rates until {sch['end']} {tz}", "critical")
        return (f"{name}: peak hours ended",
                "Off-peak rates until the next window", "normal")
    if boundary == "start":
        return (f"{name}: off-peak started",
                f"Discounted rates until {sch['end']} {tz}", "normal")
    return (f"{name}: off-peak ended",
            "Standard rates until the next window", "normal")


def systemd_escape(text: str) -> str:
    return text.replace("%", "%%")


def gen_units(schedules: list[dict]) -> list[dict]:
    units = []
    for sch in schedules:
        base = f"{UNIT_PREFIX}{slugify(sch['name'])}"
        for boundary in ("start", "end"):
            summary, body, urgency = boundary_message(sch, boundary)
            desc = f"{sch['name']} {boundary}"
            units.append({
                "base": f"{base}-{boundary}",
                "timer": TIMER_TEMPLATE.format(desc=desc, spec=calendar_spec(sch, boundary)),
                "service": SERVICE_TEMPLATE.format(
                    desc=desc, urgency=urgency,
                    summary=systemd_escape(summary), body=systemd_escape(body)),
            })
    return units


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_config(config_path: Path) -> list[dict]:
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {config_path}: {exc}") from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {config_path}: {exc}") from exc
    return validate_config(doc)


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"warning: {' '.join(cmd)} failed: {result.stderr.strip()}",
              file=sys.stderr)


def cmd_install(args: argparse.Namespace) -> None:
    root = repo_root()
    config_path = Path(args.config).expanduser()
    plasmoid_dst = Path.home() / ".local/share/plasma/plasmoids" / PLASMOID_ID
    unit_dir = Path.home() / ".config/systemd/user"

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(root / "config" / "schedules.example.json", config_path)
        print(f"created default config at {config_path}")
    schedules = load_config(config_path)

    units = gen_units(schedules)
    wanted = {u["base"] for u in units}

    unit_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(unit_dir.glob(f"{UNIT_PREFIX}*")):
        suffix = ".service" if stale.name.endswith(".service") else ".timer"
        base = stale.name[: -len(suffix)]
        if base not in wanted:
            if stale.name.endswith(".timer"):
                run(["systemctl", "--user", "disable", "--now", stale.name])
            stale.unlink()
            print(f"removed stale unit {stale.name}")

    for u in units:
        (unit_dir / f"{u['base']}.service").write_text(u["service"], encoding="utf-8")
        (unit_dir / f"{u['base']}.timer").write_text(u["timer"], encoding="utf-8")
    run(["systemctl", "--user", "daemon-reload"])
    for u in units:
        run(["systemctl", "--user", "enable", "--now", f"{u['base']}.timer"])
    print(f"installed {len(units)} timer units for {len(schedules)} schedule(s)")

    if plasmoid_dst.exists():
        shutil.rmtree(plasmoid_dst)
    shutil.copytree(root / "plasmoid" / PLASMOID_ID, plasmoid_dst)
    print(f"installed plasmoid at {plasmoid_dst}")

    if shutil.which("kbuildsycoca6"):
        run(["kbuildsycoca6", "--noincremental"])

    print('next: add "Off-Peak Tray" to the system tray (panel Edit Mode → Add Widgets)')
    print("      if it is not listed yet: systemctl --user restart plasma-plasmashell.service")


def cmd_uninstall(_args: argparse.Namespace) -> None:
    unit_dir = Path.home() / ".config/systemd/user"
    plasmoid_dst = Path.home() / ".local/share/plasma/plasmoids" / PLASMOID_ID
    for unit in sorted(unit_dir.glob(f"{UNIT_PREFIX}*.timer")):
        run(["systemctl", "--user", "disable", "--now", unit.name])
    for unit in sorted(unit_dir.glob(f"{UNIT_PREFIX}*")):
        unit.unlink()
    run(["systemctl", "--user", "daemon-reload"])
    if plasmoid_dst.exists():
        shutil.rmtree(plasmoid_dst)
    if shutil.which("kbuildsycoca6"):
        run(["kbuildsycoca6", "--noincremental"])
    print("removed timers and plasmoid; config left at ~/.config/offpeak-tray/schedules.json")


def cmd_validate(args: argparse.Namespace) -> None:
    schedules = load_config(Path(args.config).expanduser())
    print(f"ok: {len(schedules)} schedule(s)")
    for sch in schedules:
        print(f"  {sch['name']}: {calendar_spec(sch, 'start')} / {calendar_spec(sch, 'end')}")


def cmd_gen_timers(args: argparse.Namespace) -> None:
    for u in gen_units(load_config(Path(args.config).expanduser())):
        print(f"### {u['base']}.service\n{u['service']}")
        print(f"### {u['base']}.timer\n{u['timer']}")


def main(argv: list[str] | None = None) -> int:
    default_config = "~/.config/offpeak-tray/schedules.json"
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate a schedules config")
    p.add_argument("config", nargs="?", default=default_config)
    p = sub.add_parser("gen-timers", help="print generated units (dry run)")
    p.add_argument("config", nargs="?", default=default_config)

    p = sub.add_parser("install", help="install config/plasmoid/timers (idempotent)")
    p.add_argument("--config", default=default_config)

    sub.add_parser("uninstall", help="remove plasmoid and timers")

    args = parser.parse_args(argv)
    try:
        {"validate": cmd_validate, "gen-timers": cmd_gen_timers,
         "install": cmd_install, "uninstall": cmd_uninstall}[args.command](args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
