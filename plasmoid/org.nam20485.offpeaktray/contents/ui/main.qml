import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid 2.0
import org.kde.plasma.components 3.0 as PC3
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property date now: new Date()
    property var schedules: []
    property string configError: ""
    property int lastGoodCount: -1

    readonly property var dayNames: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    readonly property string configPath: "~/.config/offpeak-tray/schedules.json"

    function parseHM(hm) {
        const p = String(hm).split(":");
        return parseInt(p[0], 10) * 60 + parseInt(p[1], 10);
    }

    // Wall clock of the schedule zone as a "pretend UTC" Date: epoch shifted
    // by the zone offset only (reading with getUTC* then yields zone wall
    // time). getTimezoneOffset() must NOT be added — it would double-count
    // the local offset (+7h skew in PDT).
    function zoned(now, offsetMinutes) {
        return new Date(now.getTime() + offsetMinutes * 60000);
    }

    function scheduleState(sch, t) {
        t = t || root.now;
        const z = zoned(t, sch.offsetMinutes ?? 0);
        const day = z.getUTCDay();
        const mins = z.getUTCHours() * 60 + z.getUTCMinutes();
        const s = sch._s, e = sch._e;
        const crossing = e <= s;
        const inWindow = crossing
            ? (sch.days.includes(dayNames[day]) && mins >= s)
              || (sch.days.includes(dayNames[(day + 6) % 7]) && mins < e)
            : sch.days.includes(dayNames[day]) && mins >= s && mins < e;
        return {
            zone: z, day: day, mins: mins, inWindow: inWindow,
            state: (inWindow === (sch.kind === "peak")) ? "peak" : "offPeak"
        };
    }

    // Next window-open strictly after now: {d: day offset 0..7, m: zone minutes}.
    function nextOpen(sch, day, mins) {
        const s = parseHM(sch.start);
        if (sch.days.includes(dayNames[day]) && mins < s)
            return { d: 0, m: s };
        for (let i = 1; i <= 7; i++) {
            const d = (day + i) % 7;
            if (sch.days.includes(dayNames[d]))
                return { d: i, m: s };
        }
        return null;
    }

    // Next window-close strictly after now. A midnight-crossing window closes
    // on the day AFTER its start day, so an active-via-yesterday window closes
    // today, while one just opened today closes tomorrow.
    function nextClose(sch, day, mins) {
        const s = parseHM(sch.start), e = parseHM(sch.end);
        const crossing = e <= s;
        const activeToday = sch.days.includes(dayNames[day]) && mins >= s
            && (crossing || mins < e);
        const activeViaYesterday = crossing
            && sch.days.includes(dayNames[(day + 6) % 7]) && mins < e;
        if (activeViaYesterday && !activeToday)
            return { d: 0, m: e };
        if (activeToday)
            return crossing ? { d: 1, m: e } : { d: 0, m: e };
        const open = nextOpen(sch, day, mins);
        if (!open)
            return null;
        return crossing ? { d: open.d + 1, m: e } : { d: open.d, m: e };
    }

    function fmtDelta(totalMins) {
        const d = Math.floor(totalMins / 1440);
        const h = Math.floor((totalMins % 1440) / 60);
        const m = totalMins % 60;
        if (d > 0) return qsTr("in %1d %2h").arg(d).arg(h);
        if (h > 0) return qsTr("in %1h %2m").arg(h).arg(m);
        return qsTr("in %1m").arg(m);
    }

    // Shared renderer for a boundary line: absolute moment (now + delta
    // minutes) in the user's local timezone, am/pm, weekday prefix only when
    // the boundary falls on a different local day.
    function fmtBoundary(prefix, delta) {
        const bd = new Date(root.now.getTime() + delta * 60000);
        const dayPrefix = bd.getDate() !== root.now.getDate() ? Qt.formatDateTime(bd, "ddd ") : "";
        return prefix + dayPrefix + Qt.formatDateTime(bd, "h:mm ap") + " · " + fmtDelta(delta);
    }

    function boundaryText(sch, st) {
        const discounted = st.state === "offPeak";
        let b;
        if (sch.kind === "offPeak")
            b = discounted ? nextClose(sch, st.day, st.mins) : nextOpen(sch, st.day, st.mins);
        else
            b = discounted ? nextOpen(sch, st.day, st.mins) : nextClose(sch, st.day, st.mins);
        if (!b)
            return "";
        return fmtBoundary(discounted ? qsTr("Off-peak ends ") : qsTr("Off-peak starts "),
                           b.d * 1440 + b.m - st.mins);
    }

    // Combined (dynamic) schedule: the intersection of every schedule's
    // discounted state. State now:
    function combinedOffPeak(t) {
        for (const s of root.schedules)
            if (scheduleState(s, t).state !== "offPeak")
                return false;
        return root.schedules.length > 0;
    }

    // Minutes until the combined state next flips (1-minute forward scan,
    // 7-day horizon — simple and correct for arbitrary window shapes).
    function combinedBoundaryDelta(cur) {
        for (let i = 1; i <= 7 * 1440; i++) {
            if (combinedOffPeak(new Date(root.now.getTime() + i * 60000)) !== cur)
                return i;
        }
        return null;
    }

    function applyConfig(ok, text, err) {
        try {
            if (!ok)
                throw new Error(qsTr("cannot read %1 (%2)").arg(root.configPath).arg(err));
            if (!text.trim())
                throw new Error(qsTr("config at %1 is empty").arg(root.configPath));
            const doc = JSON.parse(text);
            if (!doc || !Array.isArray(doc.schedules))
                throw new Error(qsTr('config is missing a "schedules" array'));
            root.schedules = doc.schedules.map(function (s) {
                s._s = parseHM(s.start);
                s._e = parseHM(s.end);
                return s;
            });
            root.configError = "";
            if (root.schedules.length !== root.lastGoodCount) {
                console.info("offpeak-tray: loaded", root.schedules.length, "schedule(s) from", root.configPath);
                root.lastGoodCount = root.schedules.length;
            }
        } catch (e) {
            root.schedules = [];
            root.configError = String(e);
            console.warn("offpeak-tray:", e);
        }
    }

    // Qt 6 blocks local-file XMLHttpRequest in QML, so the config is read via
    // the executable dataengine (cat, re-run every 30 s — JSON edits are
    // picked up live without restarting the shell).
    P5Support.DataSource {
        id: configSource

        engine: "executable"
        connectedSources: ["cat " + root.configPath]
        interval: 30000
        onNewData: function (source, data) {
            root.applyConfig(data["exit code"] == 0, String(data["stdout"]),
                             String(data["stderr"] || ("exit code " + data["exit code"])));
        }
    }

    Timer {
        interval: 30000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.now = new Date()
    }

    readonly property var states: {
        const out = [];
        const inventory = [];
        for (const s of root.schedules) {
            const st = scheduleState(s);
            out.push({ sch: s, st: st, boundary: boundaryText(s, st) });
            inventory.push(s.name + ": " + (st.state === "offPeak"
                ? qsTr("off-peak") : qsTr("peak")));
        }
        // Dynamic combined row: the intersection of all discount windows.
        if (root.schedules.length > 1) {
            const cur = combinedOffPeak(root.now);
            const delta = combinedBoundaryDelta(cur);
            out.push({
                sch: {
                    name: qsTr("Combined off-peak"),
                    docsUrl: null,
                    subtext: inventory.join("\n")
                },
                st: { state: cur ? "offPeak" : "peak" },
                boundary: delta === null ? qsTr("No state change in the next 7 days")
                          : fmtBoundary(cur ? qsTr("Off-peak ends ") : qsTr("Off-peak starts "), delta)
            });
        }
        return out;
    }
    readonly property int totalSchedules: root.schedules.length
    readonly property int offSchedules: {
        let n = 0;
        for (const s of root.schedules)
            if (scheduleState(s).state === "offPeak")
                n++;
        return n;
    }
    readonly property bool allOffPeak: root.totalSchedules > 0 && root.offSchedules === root.totalSchedules
    readonly property bool allPeak: root.totalSchedules > 0 && root.offSchedules === 0
    readonly property bool mixedSchedules: root.offSchedules > 0
        && root.offSchedules < root.totalSchedules

    Plasmoid.icon: root.configError !== "" ? "state-error"
        : root.schedules.length === 0 ? "clock"
        : root.allOffPeak ? "weather-clear-night"
        : root.allPeak ? "state-warning"
        : "clock"

    // Custom tray icon, four states by share of schedules currently
    // off-peak: green clock + "$" badge (all), split green/red clock (some),
    // red clock (none), plain foreground clock (no schedules); error icon on
    // config failure.
    compactRepresentation: Item {
        id: compact

        Layout.minimumWidth: Kirigami.Units.iconSizes.small
        Layout.minimumHeight: Kirigami.Units.iconSizes.small

        readonly property bool isError: root.configError !== ""
        readonly property bool isMixed: root.mixedSchedules
        readonly property real iconSize: Math.min(width, height)

        Kirigami.Icon {
            id: baseIcon

            visible: !compact.isMixed
            anchors.centerIn: parent
            width: compact.iconSize
            height: width
            source: compact.isError ? "state-error" : "clock"
            isMask: !compact.isError && root.totalSchedules > 0
            color: root.allOffPeak ? Kirigami.Theme.positiveTextColor
                                   : Kirigami.Theme.negativeTextColor
        }

        Item {
            id: mixedIcon

            visible: compact.isMixed
            anchors.centerIn: parent
            width: compact.iconSize
            height: width

            Item {
                width: mixedIcon.width / 2
                height: mixedIcon.height
                clip: true

                Kirigami.Icon {
                    width: mixedIcon.width
                    height: mixedIcon.height
                    source: "clock"
                    isMask: true
                    color: Kirigami.Theme.positiveTextColor
                }
            }
            Item {
                width: mixedIcon.width / 2
                height: mixedIcon.height
                anchors.right: parent.right
                clip: true

                Kirigami.Icon {
                    width: mixedIcon.width
                    height: mixedIcon.height
                    x: -mixedIcon.width / 2
                    source: "clock"
                    isMask: true
                    color: Kirigami.Theme.negativeTextColor
                }
            }
        }

        Rectangle {
            visible: root.allOffPeak
            anchors.right: baseIcon.right
            anchors.bottom: baseIcon.bottom
            width: Math.round(baseIcon.width * 0.6)
            height: width
            radius: width / 2
            color: Kirigami.Theme.positiveTextColor
            border.color: Kirigami.Theme.backgroundColor
            border.width: Math.max(1, Math.round(width * 0.08))

            Text {
                anchors.centerIn: parent
                text: "$"
                color: "#ffffff"
                font.pixelSize: parent.height * 0.7
                font.bold: true
            }
        }
    }

    toolTipMainText: root.configError !== ""
        ? qsTr("Off-Peak Tray — config error")
        : root.totalSchedules === 0 ? qsTr("No schedules configured")
        : root.allOffPeak ? qsTr("All schedules off-peak")
        : root.allPeak ? qsTr("All schedules peak")
        : qsTr("Mixed states")
    toolTipSubText: root.configError !== ""
        ? root.configError
        : root.schedules.map(s => {
              const st = scheduleState(s);
              return s.name + ": " + (st.state === "offPeak" ? qsTr("off-peak") : qsTr("peak"));
          }).join("\n")

    fullRepresentation: ColumnLayout {
        spacing: Kirigami.Units.smallSpacing

        Repeater {
            model: root.states

            delegate: PC3.ItemDelegate {
                id: rowDelegate

                Layout.fillWidth: true
                // Rows share the popup's height equally (Plasma enforces a
                // gridUnit*24 minimum for tray popups; this fills it).
                Layout.fillHeight: true
                padding: Kirigami.Units.smallSpacing * 2
                onClicked: {
                    if (modelData.sch.docsUrl)
                        Qt.openUrlExternally(modelData.sch.docsUrl);
                }

                background: Rectangle {
                    radius: Kirigami.Units.smallSpacing / 2
                    color: rowDelegate.hovered ? Kirigami.Theme.alternateBackgroundColor
                                               : "transparent"
                    border.color: Qt.rgba(Kirigami.Theme.textColor.r,
                                          Kirigami.Theme.textColor.g,
                                          Kirigami.Theme.textColor.b, 0.25)
                    border.width: 1
                }

                contentItem: ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing

                    RowLayout {
                        Kirigami.Icon {
                            visible: !!modelData.sch.docsUrl
                            source: Qt.resolvedUrl("../icons/windowpane.svg")
                            isMask: true
                            color: Kirigami.Theme.textColor
                            implicitWidth: Kirigami.Units.smallSpacing * 3
                            implicitHeight: Kirigami.Units.smallSpacing * 3
                        }
                        PC3.Label {
                            text: modelData.sch.name
                            font.bold: true
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        PC3.Label {
                            text: modelData.st.state === "offPeak" ? qsTr("Off-peak") : qsTr("Peak")
                            color: modelData.st.state === "offPeak"
                                   ? Kirigami.Theme.positiveTextColor
                                   : Kirigami.Theme.negativeTextColor
                        }
                    }

                    PC3.Label {
                        text: modelData.boundary
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }

                    PC3.Label {
                        text: modelData.sch.subtext ?? ""
                        visible: !!modelData.sch.subtext
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        verticalAlignment: Text.AlignTop
                        Layout.fillHeight: true
                    }
                }
            }
        }

        PC3.Label {
            visible: root.configError !== ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Kirigami.Theme.negativeTextColor
            text: root.configError
        }
        PC3.Label {
            visible: root.schedules.length === 0 && root.configError === ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Kirigami.Theme.disabledTextColor
            text: qsTr("No schedules configured.\nEdit ~/.config/offpeak-tray/schedules.json")
        }
    }
}
