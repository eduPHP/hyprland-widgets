//@ pragma Env QS_NO_RELOAD_POPUP=1
// hyprbt: Bluetooth panel for Hyprland, built on Quickshell.Bluetooth (BlueZ).
//   qs -c hyprbt                      run it (exec-once)
//   qs -c hyprbt ipc call bt toggle   open/close (waybar on-click, keybind)
import QtQuick
import QtQuick.Effects
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import Quickshell.Bluetooth

ShellRoot {
    id: root

    property bool open: false
    property double closedAt: 0

    // hypr-ui: while a screenshot overlay is up (flameshot-gui keeps this flag),
    // drop from OVERLAY to TOP so the fullscreen overlay covers the popover.
    property bool capturing: false
    FileView {
        path: Quickshell.env("XDG_RUNTIME_DIR") + "/hypr-ui-capture"
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: root.capturing = true
        onLoadFailed: root.capturing = false
    }
    readonly property var adapter: Bluetooth.defaultAdapter
    readonly property bool powered: adapter !== null && adapter.enabled
    readonly property bool blocked: adapter !== null && adapter.state === BluetoothAdapterState.Blocked

    readonly property var devices: adapter ? adapter.devices.values : []
    readonly property var connectedDevs: devices.filter(d => d.connected)
    readonly property var pairedDevs: devices.filter(d => !d.connected && (d.paired || d.bonded))
    readonly property var nearbyDevs: devices
        .filter(d => !d.connected && !d.paired && !d.bonded && d.deviceName !== "")
        .sort((a, b) => a.name.localeCompare(b.name))

    // hypr-ui: one popover at a time. The flag file tells hypr-ui-solo we're open;
    // opening runs it so the Wi-Fi and sound popovers close.
    readonly property string openFlag: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/hyprbt.open"
    function setOpen(v) {
        if (v === open) return;
        if (!v) closedAt = Date.now();
        open = v;
        if (v) {
            Quickshell.execDetached(["touch", openFlag]);
            Quickshell.execDetached([Quickshell.env("HOME") + "/.config/hypr-ui/bin/hypr-ui-solo", "hyprbt"]);
        } else {
            Quickshell.execDetached(["rm", "-f", openFlag]);
        }
    }

    // --- pairing agent (child process) -----------------------------------
    property var agentReq: null

    Process {
        id: agent
        running: true
        command: ["python3", Quickshell.shellDir + "/hyprbt-agent.py"]
        stdinEnabled: true
        stdout: SplitParser {
            onRead: line => {
                let m;
                try { m = JSON.parse(line); } catch (e) { return; }
                if (m.type === "done") { root.agentReq = null; return; }
                root.agentReq = m;
                root.setOpen(true);
            }
        }
        stderr: SplitParser { onRead: line => console.warn("hyprbt-agent:", line) }
        onExited: (code, status) => { root.agentReq = null; agentRestart.start(); }
    }
    Timer { id: agentRestart; interval: 3000; onTriggered: agent.running = true }

    function answer(accept) {
        agent.write(accept ? "accept\n" : "reject\n");
        agentReq = null;
    }

    // Clear a "type this code" prompt once the device finished pairing.
    Timer {
        interval: 1000; repeat: true
        running: root.agentReq !== null && root.agentReq.type === "display"
        onTriggered: {
            const d = root.devices.find(x => x.dbusPath === root.agentReq.device);
            if (!d || d.paired) root.agentReq = null;
        }
    }

    // Stop discovery on its own so the radio isn't left scanning.
    Timer {
        interval: 30000
        running: root.adapter !== null && root.adapter.discovering
        onTriggered: root.adapter.discovering = false
    }

    // --- IPC ----------------------------------------------------------------
    IpcHandler {
        target: "bt"
        function toggle(): void {
            // A click on the waybar icon first clears the focus grab (closing
            // the panel); don't reopen it straight away.
            if (!root.open && Date.now() - root.closedAt < 350) return;
            root.setOpen(!root.open);
        }
        function open(): void { root.setOpen(true); }
        function close(): void { root.setOpen(false); }
        function power(): void { if (root.adapter) root.adapter.enabled = !root.adapter.enabled; }
        function status(): string {
            if (!root.adapter) return "none";
            if (!root.powered) return "off";
            return root.connectedDevs.length ? "connected:" + root.connectedDevs.map(d => d.name).join(",") : "on";
        }
    }

    // --- panel ----------------------------------------------------------------
    PanelWindow {
        id: win
        visible: root.open || card.opacity > 0.01
        anchors { top: true; right: true }
        // hypr-ui surface: the card sits gapTop/gapRight px from the bar and screen
        // edge; the window is `margin` px bigger all round for the shadow, and that
        // margin is click-through (mask).
        margins { top: Tokens.surfaceGapTop - Tokens.surfaceMargin; right: Tokens.surfaceGapRight - Tokens.surfaceMargin }
        exclusionMode: ExclusionMode.Normal
        exclusiveZone: 0
        color: "transparent"
        implicitWidth: card.width + 2 * Tokens.surfaceMargin
        implicitHeight: Math.min(card.implicitHeight + 2 * Tokens.surfaceMargin, 900)
        mask: Region { item: card }

        WlrLayershell.namespace: "hyprbt"
        WlrLayershell.layer: root.capturing ? WlrLayer.Top : WlrLayer.Overlay
        WlrLayershell.keyboardFocus: root.open ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None

        HyprlandFocusGrab {
            windows: [win]
            active: root.open
            onCleared: root.setOpen(false)
        }

        Item {
            id: card
            x: Tokens.surfaceMargin; y: Tokens.surfaceMargin
            width: 380
            implicitHeight: frame.height
            opacity: root.open ? 1 : 0
            transform: Translate { y: root.open ? 0 : -10; Behavior on y { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } } }
            Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }

            focus: true
            Keys.onEscapePressed: root.setOpen(false)

            // Shadow comes from a hidden plain shape, so the card itself is drawn
            // once and nothing dark bleeds through the 1px border.
            Rectangle {
                id: shadowShape
                visible: false
                width: frame.width; height: frame.height
                radius: frame.radius
                color: Theme.panel
            }
            MultiEffect {
                source: shadowShape
                anchors.fill: frame
                shadowEnabled: true
                shadowColor: "#000000"
                shadowOpacity: Tokens.shadowAlpha
                blurMax: Tokens.shadowBlur
                shadowBlur: 1.0
                shadowVerticalOffset: Tokens.shadowY
                autoPaddingEnabled: true
            }

            // hypr-ui popover frame: 1px opaque border, same as the GTK widgets
            Rectangle {
                id: frame
                width: parent.width
                height: body.implicitHeight + 2
                radius: Tokens.radiusSurface
                color: Theme.panel
                border.width: 1
                border.color: Theme.frame

                Item {
                    id: inner
                    anchors.fill: parent
                    anchors.margins: 1

                    Column {
                        id: body
                        width: parent.width

                        // header
                        Item {
                            width: parent.width
                            height: Tokens.controlTile + 2 * Tokens.spacePad
                            Rectangle {
                                id: headTile
                                x: Tokens.spacePad; anchors.verticalCenter: parent.verticalCenter
                                width: Tokens.controlTile; height: Tokens.controlTile; radius: 8
                                color: Tokens.raised
                                Txt {
                                    anchors.centerIn: parent
                                    font.pixelSize: Tokens.sizeIcon
                                    text: root.powered ? Theme.iBluetooth : Theme.iBluetoothOff
                                    color: root.powered ? Theme.accent : Theme.dim
                                }
                            }
                            Column {
                                anchors.left: headTile.right; anchors.leftMargin: Tokens.spaceGap
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 1
                                Txt { text: "bluetooth"; font.pixelSize: Tokens.sizeTitle; font.weight: Font.Bold }
                                Txt {
                                    font.pixelSize: Tokens.sizeSmall; color: Theme.muted
                                    text: {
                                        if (!root.adapter) return "no adapter found";
                                        const id = root.adapter.adapterId;
                                        if (root.blocked) return id + " · blocked by rfkill";
                                        if (!root.powered) return id + " · powered off";
                                        if (root.adapter.discovering) return id + " · discovering";
                                        return id + " · " + root.connectedDevs.length + " connected";
                                    }
                                }
                            }
                            // power switch
                            Item {
                                anchors.right: parent.right; anchors.rightMargin: Tokens.spacePad
                                anchors.verticalCenter: parent.verticalCenter
                                width: 38; height: 30
                                visible: root.adapter !== null
                                // same switch as GTK's: 38x20 track, 16px knob
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: 38; height: 20; radius: 10
                                    color: root.powered ? Theme.accent : Theme.trackOff
                                    Behavior on color { ColorAnimation { duration: 150 } }
                                    Rectangle {
                                        y: 2; width: 16; height: 16; radius: 8
                                        x: root.powered ? 20 : 2
                                        color: root.powered ? Theme.accentInk : Theme.muted
                                        Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.adapter.enabled = !root.adapter.enabled
                                }
                            }
                        }

                        // ---- powered on ----
                        Column {
                            visible: root.powered
                            width: parent.width

                            Row {
                                x: Tokens.spacePad; width: parent.width - 2 * Tokens.spacePad
                                spacing: 8
                                bottomPadding: Tokens.spacePad
                                Btn {
                                    width: parent.width - visChip.width - 8
                                    variant: root.adapter && root.adapter.discovering ? "ghost" : "ghost"
                                    color: root.adapter && root.adapter.discovering ? Theme.alpha(Theme.accent, 0.12) : Theme.surface
                                    border.color: root.adapter && root.adapter.discovering ? Theme.alpha(Theme.accent, 0.35) : Theme.border
                                    glyph: root.adapter && root.adapter.discovering ? "" : Theme.iRefresh
                                    spinning: root.adapter !== null && root.adapter.discovering
                                    text: root.adapter && root.adapter.discovering ? "Scanning… click to stop" : "Scan for devices"
                                    onClicked: root.adapter.discovering = !root.adapter.discovering
                                }
                                Rectangle {
                                    id: visChip
                                    width: visRow.implicitWidth + 24; height: Tokens.controlHeight; radius: Tokens.radiusControl
                                    color: visMouse.containsMouse ? Theme.tile : Theme.surface
                                    border.width: 1; border.color: Theme.border
                                    Row {
                                        id: visRow
                                        anchors.centerIn: parent
                                        spacing: 8
                                        Rectangle {
                                            width: 7; height: 7; radius: 4
                                            anchors.verticalCenter: parent.verticalCenter
                                            color: root.adapter && root.adapter.discoverable ? Theme.ok : Theme.dim
                                        }
                                        Txt {
                                            font.pixelSize: Tokens.sizeCaption; color: Theme.muted
                                            text: root.adapter && root.adapter.discoverable ? "visible" : "hidden"
                                        }
                                    }
                                    MouseArea {
                                        id: visMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: root.adapter.discoverable = !root.adapter.discoverable
                                    }
                                }
                            }

                            Rectangle { width: parent.width; height: 1; color: Theme.line }

                            Flickable {
                                id: flick
                                width: parent.width
                                height: Math.min(list.implicitHeight, 520)
                                contentHeight: list.implicitHeight
                                clip: true
                                boundsBehavior: Flickable.StopAtBounds

                                Column {
                                    id: list
                                    x: 6; width: parent.width - 12
                                    bottomPadding: 6

                                    SectionHeader { label: "Connected"; count: root.connectedDevs.length; visible: count > 0 }
                                    Repeater {
                                        model: root.connectedDevs
                                        DeviceRow { required property var modelData; device: modelData; agentReq: root.agentReq; onAnswer: a => root.answer(a) }
                                    }
                                    SectionHeader { label: "Paired"; count: root.pairedDevs.length; visible: count > 0 }
                                    Repeater {
                                        model: root.pairedDevs
                                        DeviceRow { required property var modelData; device: modelData; agentReq: root.agentReq; onAnswer: a => root.answer(a) }
                                    }
                                    SectionHeader {
                                        label: "Nearby"; count: root.nearbyDevs.length
                                        scanning: root.adapter !== null && root.adapter.discovering
                                    }
                                    Txt {
                                        visible: root.nearbyDevs.length === 0
                                        width: parent.width
                                        leftPadding: 8; rightPadding: 8; topPadding: 4; bottomPadding: 10
                                        wrapMode: Text.WordWrap
                                        font.pixelSize: Tokens.sizeSmall
                                        color: Tokens.faint
                                        text: root.adapter && root.adapter.discovering
                                            ? "Looking for devices in pairing mode…"
                                            : "Nothing nearby. Put a device in pairing mode, then scan."
                                    }
                                    Repeater {
                                        model: root.nearbyDevs
                                        DeviceRow { required property var modelData; device: modelData; agentReq: root.agentReq; onAnswer: a => root.answer(a) }
                                    }
                                }
                            }
                        }

                        // ---- powered off ----
                        Column {
                            visible: !root.powered
                            width: parent.width
                            Rectangle { width: parent.width; height: 1; color: Theme.line }
                            Column {
                                width: parent.width
                                topPadding: 24; bottomPadding: 24
                                spacing: 8
                                Txt { anchors.horizontalCenter: parent.horizontalCenter; font.pixelSize: Tokens.sizeHero; text: Theme.iPower; color: Theme.dim }
                                Txt {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    text: root.blocked ? "Bluetooth is blocked" : root.adapter ? "Bluetooth is off" : "No Bluetooth adapter"
                                    font.weight: Font.Bold
                                }
                                Txt {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    width: 300
                                    horizontalAlignment: Text.AlignHCenter
                                    wrapMode: Text.WordWrap
                                    font.pixelSize: Tokens.sizeSmall
                                    color: Tokens.faint
                                    text: root.blocked ? "Unblock it with: rfkill unblock bluetooth"
                                        : root.adapter ? "Your paired devices are remembered. Turn it on to reconnect them."
                                        : "Is bluetooth.service running?"
                                }
                                Btn {
                                    visible: root.adapter !== null && !root.blocked
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    width: 96
                                    variant: "primary"
                                    text: "Turn on"
                                    onClicked: root.adapter.enabled = true
                                }
                            }
                        }

                        // footer: key hints, like the GTK widgets' .footer
                        Item {
                            width: parent.width; height: 30
                            Rectangle { x: Tokens.spacePad; width: parent.width - 2 * Tokens.spacePad; height: 1; color: Tokens.line }
                            Txt {
                                x: Tokens.spacePad; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 1
                                font.pixelSize: Tokens.sizeMicro; color: Tokens.faint
                                text: (root.connectedDevs.length + root.pairedDevs.length) + " paired · " + root.connectedDevs.length + " connected"
                            }
                            Row {
                                anchors.right: parent.right; anchors.rightMargin: Tokens.spacePad
                                anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 1
                                spacing: 4
                                Rectangle {
                                    width: escKey.implicitWidth + 8; height: escKey.implicitHeight + 2; radius: Tokens.radiusChip
                                    color: Tokens.line
                                    Txt { id: escKey; anchors.centerIn: parent; text: "esc"; font.pixelSize: Tokens.sizeMicro }
                                }
                                Txt { anchors.verticalCenter: parent.verticalCenter; text: "close"; font.pixelSize: Tokens.sizeMicro; color: Tokens.faint }
                            }
                        }
                    }
                }
            }
        }
    }
}
