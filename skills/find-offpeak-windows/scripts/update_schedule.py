#!/usr/bin/env python3
"""Propose or apply updates to the Off-Peak Tray schedule file.

Usage:
    update_schedule.py propose ENTRY [--config PATH]
    update_schedule.py apply   ENTRY [--config PATH]

ENTRY is a JSON file containing ONE schedule object, or '-' to read stdin.
`propose` validates the entry, merges it into the config (replacing an
existing schedule with the same name, otherwise appending) and prints the
resulting diff — it writes nothing. `apply` does the same, then backs the
config up alongside itself (schedules.json.bak-YYYYMMDD-HHMMSS) before
writing, and regenerates the systemd notification timers via the repo's
scripts/offpeak.py install.

Language note: python3 rather than the linux-system-agent pwsh house
default — Linux-only app, python3 ships on stock Debian/Ubuntu (maintainer
decision, 2026-09-05).
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import offpeak  # noqa: E402

DEFAULT_CONFIG = "~/.config/offpeak-tray/schedules.json"


def load_doc(config_path: Path) -> dict:
    """Load the raw config document; missing file → empty default."""
    if not config_path.exists():
        return {"schedules": []}
    return json.loads(config_path.read_text(encoding="utf-8"))


def merge_entry(doc: dict, entry: dict) -> dict:
    """Return a new doc with entry merged in (replace by name, else append)."""
    schedules = [dict(s) for s in doc.get("schedules", [])]
    replaced = False
    for i, s in enumerate(schedules):
        if s.get("name") == entry["name"]:
            schedules[i] = dict(entry)
            replaced = True
            break
    if not replaced:
        schedules.append(dict(entry))
    return {"schedules": schedules}


def render(doc: dict) -> str:
    return json.dumps(doc, indent=4, ensure_ascii=False) + "\n"


def print_diff(old: str, new: str) -> None:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="current", tofile="proposed")
    sys.stdout.writelines(diff)


def load_entry(entry_arg: str) -> dict:
    raw = sys.stdin.read() if entry_arg == "-" else Path(entry_arg).read_text(encoding="utf-8")
    entry = json.loads(raw)
    offpeak.validate_config({"schedules": [entry]})
    return entry


def cmd_propose(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser()
    entry = load_entry(args.entry)
    old_doc = load_doc(config_path)
    new_doc = merge_entry(old_doc, entry)
    print_diff(render(old_doc), render(new_doc))
    action = "replaces" if any(s.get("name") == entry["name"]
                               for s in old_doc.get("schedules", [])) else "appends"
    print(f"proposal: {action} {entry['name']!r} into {config_path} (nothing written)")


def cmd_apply(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser()
    entry = load_entry(args.entry)
    old_doc = load_doc(config_path)
    new_doc = merge_entry(old_doc, entry)

    backup = None
    if config_path.exists():
        backup = config_path.with_suffix(
            config_path.suffix + ".bak-" + datetime.now().strftime("%Y-%m-%d-%H%M%S"))
        backup.write_text(render(old_doc), encoding="utf-8")
        print(f"backup: {backup}")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render(new_doc), encoding="utf-8")
    print(f"written: {config_path}")

    installer = REPO_ROOT / "scripts" / "offpeak.py"
    result = subprocess.run(
        [sys.executable, str(installer), "install", "--config", str(config_path)],
        capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"error: config written but timer install failed "
                         f"(exit {result.returncode}); re-run: python3 {installer} install")
    print("plasmoid re-reads the config within 30 seconds — no restart needed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("propose", "validate + show diff, write nothing"),
                            ("apply", "validate, back up, write, regenerate timers")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("entry", help="JSON file with one schedule object, or '-' for stdin")
        p.add_argument("--config", default=DEFAULT_CONFIG)

    args = parser.parse_args(argv)
    try:
        {"propose": cmd_propose, "apply": cmd_apply}[args.command](args)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
