# plasma-offpeak-tray

A KDE Plasma 6 **system-tray widget** that shows, at a glance, when LLM providers'
off-peak (discounted) rate windows are active — plus **boundary notifications**
via systemd user timers when a window opens or closes.

Built for a US/Pacific workstation tracking Chinese-market providers whose
windows are published in UTC+8.

## Why

| Provider | Window (UTC+8)              | Kind      | Effect in window                     |
| -------- | --------------------------- | --------- | ------------------------------------ |
| Z.AI GLM | Mon–Fri 14:00–18:00         | **peak**  | elevated quota consumption           |
| Qwen     | every day 22:00–08:00 (+1d) | **off-peak** | discounted credit consumption     |

Both zones are fixed UTC+8 (no DST), but the *local* equivalent shifts with US
daylight saving: on PDT, GLM peak is 11 PM–3 AM Sun–Thu nights and Qwen
off-peak is 7 AM–5 PM; on PST it shifts an hour earlier. All schedule math is
therefore done in the provider's zone, never in local time.

- GLM reference: <https://docs.z.ai/devpack/notice/usage-revision>
- Qwen reference: <https://www.alibabacloud.com/blog/qwen3-7-off-peak-rates_603404>

## Architecture

```
~/.config/offpeak-tray/schedules.json   ← single source of truth (plain text, user-editable)
        │
        ├── plasmoid (QML, tray)         reads it every 30 s via the executable dataengine
        │     icon (3-state) · tooltip · detail popup (one row per schedule)
        │     clicking a row opens docsUrl in the system browser  (stretch goal, shipped)
        │
        └── scripts/offpeak.py install   regenerates systemd user timers from the same JSON
              offpeak-tray-<slug>-start.timer / -end.timer → notify-send popups at boundaries
```

### Tray icon states

The tray icon is a custom compact representation: a clock whose tint shows
what share of schedules are currently off-peak.

| Appearance                           | Condition                                        |
| ------------------------------------ | ------------------------------------------------ |
| green clock + green `$` badge        | every schedule is in its discounted state        |
| half green / half red clock          | some schedules off-peak, others not              |
| red clock                            | no schedule is in its discounted state           |
| plain foreground clock               | no schedules configured                          |
| error icon                           | config unreadable/invalid (tooltip explains)     |

(`Plasmoid.icon` — used for window-list/alt-tab contexts — keeps the older
moon/warning/clock/error mapping.)

The detail popup (click the tray icon) lists every schedule as a bordered
card row that expands to fill the popup height; each row shows a green/red
dot, the state, when the discounted state changes (`Off-peak starts Mon
7:00 am · in 5h 12m`, in the user's local timezone), and the optional
`subtext` detail text. Clicking a schedule row opens that provider's
discount documentation (`docsUrl`) in the default browser.

## Configuration

`~/.config/offpeak-tray/schedules.json` — plain JSON, an object with a
`schedules` array. Fields per schedule:

| Field           | Type     | Notes                                                        |
| --------------- | -------- | ------------------------------------------------------------ |
| `name`          | string   | shown in popup, tooltip, notifications                       |
| `kind`          | string   | `"peak"` (window IS the peak/surcharge period; off-peak = complement) or `"offPeak"` (window IS the discount period) |
| `timezone`      | string   | IANA name; used **only** by systemd timers (tzdata, DST-correct) |
| `offsetMinutes` | int      | fixed UTC offset (e.g. `480`); used **only** by the tray icon |
| `days`          | string[] | start days, any of `Sun Mon Tue Wed Thu Fri Sat`             |
| `start`, `end`  | `"HH:MM"`| window bounds; `end <= start` means the window crosses midnight and attaches to the **start** day |
| `docsUrl`       | string?  | optional `http(s)://` link opened when the popup row is clicked |
| `subtext`       | string?  | optional detail text shown under the schedule inside its popup row (discount details, etc.) |

Example (the shipped default):

```json
{
  "schedules": [
    {
      "name": "Z.AI GLM",
      "kind": "peak",
      "timezone": "Asia/Singapore",
      "offsetMinutes": 480,
      "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
      "start": "14:00",
      "end": "18:00",
      "docsUrl": "https://docs.z.ai/devpack/notice/usage-revision"
    },
    {
      "name": "Qwen",
      "kind": "offPeak",
      "timezone": "Asia/Shanghai",
      "offsetMinutes": 480,
      "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
      "start": "22:00",
      "end": "08:00",
      "docsUrl": "https://www.alibabacloud.com/blog/qwen3-7-off-peak-rates_603404"
    }
  ]
}
```

State semantics: a moment is *in-window* iff (its zone-day ∈ `days` and
zone-time ≥ `start`) or (previous zone-day ∈ `days` and zone-time < `end`).
The displayed state is `kind` when in-window, its opposite otherwise.
All-day midnight-crossing windows (Qwen) degenerate correctly.

## Install

```bash
python3 scripts/offpeak.py install
```

Idempotent. Installs the default config only if absent (never overwrites),
copies the plasmoid to `~/.local/share/plasma/plasmoids/`, generates and
enables the systemd user timers, refreshes the KDE cache. Removing a schedule
from the JSON and re-running disables and deletes its stale timers.

One manual step remains: add the widget to the tray — right-click the panel →
*Enter Edit Mode* → *Add Widgets* → drag **Off-Peak Tray** into the system
tray (it registers as a tray-capable applet). If it does not appear in the
list yet, restart the shell first: `systemctl --user restart
plasma-plasmashell.service`.

## Verification (what was actually run on the dev host)

- `systemd-analyze calendar "Mon..Fri *-*-* 14:00:00 Asia/Singapore"` → next
  elapse honors the PDT↔SGT conversion (Sun 23:00 PDT = Mon 14:00 SGT).
- `python3 -m unittest discover -s tests` — all unit tests pass.
- `plasmawindowed org.nam20485.offpeaktray` — QML compiles with no errors and
  logs `loaded 2 schedule(s)` (dataengine config read works, tilde included).
- `systemctl --user list-timers 'offpeak-tray-*'` — 4 timers scheduled.
- Manual `systemctl --user start offpeak-tray-<slug>-<boundary>.service` →
  notification popup appears.

## Development

- Tooling language: **python3 + bash** — deliberate exception to the
  linux-system-agent pwsh house rule (maintainer decision 2026-09-05): this is
  a Linux-only app and python3 is present on stock Debian/Ubuntu installs
  where pwsh (and jq) are not. Noted here per the coding-style rule.
- Tests: `python3 -m unittest discover -s tests -v` (stdlib only, no deps).
- Gate: `./validation.sh` — build (py_compile + JSON syntax), scan (gitleaks
  when present), test (unittest). No CI workflow yet; choices documented in
  the script header.

## Skill: find-offpeak-windows

`skills/find-offpeak-windows/` is an [Agent Skills](https://agentskills.io/specification)-compliant
skill for agentic use: research a provider's discount hours on the web,
propose a schedule entry, and — only after explicit user approval — back up
and update the schedule file, regenerating the timers in the same step.
Deterministic parts live in `skills/find-offpeak-windows/scripts/update_schedule.py`:

```bash
# validate + show diff, writes nothing:
python3 skills/find-offpeak-windows/scripts/update_schedule.py propose entry.json
# back up + write + regenerate timers:
python3 skills/find-offpeak-windows/scripts/update_schedule.py apply entry.json
```

See the skill's SKILL.md for the full research-and-approval workflow.

## Limitations

- `offsetMinutes` is a **fixed** offset: the tray icon is wrong for DST-observing
  zones (systemd timers stay correct via `timezone`). Fine for UTC+8 providers;
  documented, not silently wrong.
- Qt 6 blocks local-file `XMLHttpRequest` in QML, so the plasmoid reads the
  config through Plasma's `executable` dataengine (`cat` every 30 s, `~`
  expanded by KShell) — this is why config edits appear live without a shell
  restart.
- Window-state logic exists twice by necessity: in `main.qml` (live display,
  QML cannot exec processes) and mirrored in the python generator's day-shift
  semantics for timer emission. Both follow the semantics section above and
  are covered by `tests/test_offpeak.py`. The next-change text in the detail
  popup (next-open/next-close scanning, including midnight-crossing windows)
  is QML-only and not covered by the python suite.
- Boundary notifications fire only while a Plasma session is running
  (`ExecCondition pgrep plasmashell`); missed boundaries are skipped, not
  replayed.
- The tray's expanded popup has a Plasma-enforced minimum height
  (`gridUnit * 24` in the systemtray's `ExpandedRepresentation.qml`), so with
  few schedules the popup shows empty space below the rows. That floor is
  stock plasma-workspace behavior shared by every tray popup; this widget
  cannot shrink below it without patching system QML.
- Plasma 6 QML idioms only (`PlasmoidItem` root, direct `toolTip*` /
  representation properties). Not backwards-compatible with Plasma 5.

## Uninstall / rollback

```bash
python3 scripts/offpeak.py uninstall
```

Removes timers and the plasmoid; leaves `~/.config/offpeak-tray/schedules.json`
in place (delete manually if desired). Host-level change history lives in the
`linux-system-agent` repo, `system-changes/`.
