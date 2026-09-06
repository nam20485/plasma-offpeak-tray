---
name: find-offpeak-windows
description: Research an LLM provider's off-peak or peak discount hours on the web (search engines plus the provider's official documentation), propose a schedule entry for the Off-Peak Tray KDE Plasma widget, and after explicit user approval back up and update ~/.config/offpeak-tray/schedules.json. Use when the user asks to find off-peak discounts or hours for a provider, or to add or refresh a provider in the Off-Peak Tray schedule file.
license: MIT
compatibility: Requires python3, network access for web research, and the plasma-offpeak-tray repository layout.
metadata:
  author: nam20485
  version: "1.0"
---

# Find Off-Peak Windows

Research a provider's discount hours and turn them into a validated
Off-Peak Tray schedule entry — proposing first, writing only after the user
approves.

## Workflow

### 1. Research the provider's windows

Search the web for the provider's discount hours, then confirm on the
provider's **official** documentation (docs site, pricing blog, plan-terms
page). Prefer official pages over third-party summaries; third-party pages
may still be useful to locate the official one.

Useful query shapes: `<provider> off-peak hours discount`, `<provider> peak
hours quota surcharge UTC+8`, `<provider> coding plan pricing off-peak`.

Extract and record:

- **Window(s)**: start/end times and the timezone they are published in
  (Chinese providers typically publish in UTC+8 / "Beijing time" /
  "Singapore time").
- **Kind**: is the published window the *peak* (surcharge) period or the
  *off-peak* (discount) period? Both are supported; pick whichever the
  provider publishes. If only a discount percentage "during off-peak" is
  given without explicit hours, keep searching — an entry needs exact hours.
- **Days**: which weekdays the window covers (windows crossing midnight
  attach to the **start** day: `start: "22:00", end: "08:00"` covers
  22:00→08:00 next morning).
- **Discount**: the magnitude (e.g. "50% off-peak rate", "up to 80% off").
- **Link**: the best official URL for `docsUrl`.
- **Date verified** (mention it to the user; promo windows expire).

### 2. Build the schedule entry

Write the proposed entry to a temp JSON file (e.g. `/tmp/<provider>.json`),
one object with these fields:

| Field           | Req | Notes                                                             |
| --------------- | --- | ----------------------------------------------------------------- |
| `name`          | ✓   | Provider display name, e.g. `"Z.AI GLM"`                           |
| `kind`          | ✓   | `"peak"` (window IS the surcharge) or `"offPeak"` (window IS the discount) |
| `timezone`      | ✓   | IANA name for the timers, e.g. `"Asia/Singapore"`                  |
| `offsetMinutes` | ✓   | Fixed UTC offset in minutes (UTC+8 → `480`); tray icon uses this   |
| `days`          | ✓   | Subset of `["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]`, start-day based |
| `start`,`end`   | ✓   | `"HH:MM"` in the provider zone; `end <= start` crosses midnight    |
| `docsUrl`       |     | Official page describing the discount                              |
| `subtext`       |     | One-line discount summary shown in the popup row                   |

### 3. Propose (writes nothing)

From the repository root:

```bash
python3 skills/find-offpeak-windows/scripts/update_schedule.py propose /tmp/<provider>.json
```

This validates the entry and prints a diff against the live config. Present
to the user, in one message:

1. The found window(s), discount magnitude, and timezone — as a table.
2. The source link.
3. The proposed JSON entry and the diff (append vs. replace-by-name).
4. The date you verified the information.

### 4. Ask for approval

Ask the user to approve or amend. **Never run `apply` without an explicit
yes.** If the user amends, update the temp JSON and re-run `propose`.

### 5. Apply on approval

```bash
python3 skills/find-offpeak-windows/scripts/update_schedule.py apply /tmp/<provider>.json
```

This backs up the config alongside itself
(`schedules.json.bak-YYYYMMDD-HHMMSS`), writes the merged config, and
regenerates the systemd timers via `scripts/offpeak.py install`. The tray
plasmoid re-reads the config within 30 seconds — no shell restart needed.

### 6. Verify

Run `python3 scripts/offpeak.py validate` (should print `ok`), then point
the user at the tray popup to confirm the new row. Boundary notification
timers for the new schedule appear in `systemctl --user list-timers
'offpeak-tray-*'`.

## Notes

- The config file is the single source of truth — never edit it directly;
  go through this skill's script so validation, backup, and timer
  regeneration always happen together.
- `propose` is safe to run any number of times; only `apply` writes.
- The widget computes schedules in the provider zone (fixed
  `offsetMinutes`, no DST) while timers use the IANA `timezone` — if a
  provider ever publishes in a DST-observing zone, the timers stay correct
  and the icon may drift by an hour (documented README limitation).
