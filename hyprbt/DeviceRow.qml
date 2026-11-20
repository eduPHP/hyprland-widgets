import QtQuick
import Quickshell.Bluetooth

Rectangle {
    id: row
    required property var device
    property var agentReq: null
    signal answer(bool accept)

    // Set when the user pressed Pair here, so we trust + connect once bonded.
    property bool wantConnect: false

    readonly property int st: device ? device.state : BluetoothDeviceState.Disconnected
    readonly property bool isConnected: device && device.connected
    readonly property bool isPaired: device && (device.paired || device.bonded)
    readonly property bool isPairing: device && device.pairing
    readonly property bool busy: st === BluetoothDeviceState.Connecting
                              || st === BluetoothDeviceState.Disconnecting
                              || isPairing
    readonly property bool asking: agentReq !== null && agentReq.device === device.dbusPath
    readonly property bool hasBattery: isConnected && device.batteryAvailable
    readonly property int batteryPct: hasBattery ? Math.round(device.battery * 100) : 0

    readonly property string subText: {
        if (asking && agentReq.type === "display") return "Type the code on the device";
        if (asking) return "Confirm pairing";
        if (st === BluetoothDeviceState.Disconnecting) return "Disconnecting";
        if (st === BluetoothDeviceState.Connecting) return "Connecting";
        if (isPairing) return "Pairing";
        if (isConnected) return "Connected";
        if (isPaired) return "Paired";
        return Theme.deviceKind(device.icon);
    }

    width: parent ? parent.width : 380
    implicitHeight: col.implicitHeight
    radius: Tokens.radiusControl
    color: isConnected ? Theme.alpha(Theme.ok, 0.08)
         : asking ? Theme.alpha(Theme.accent, 0.08)
         : hover.hovered ? Theme.alpha(Tokens.raised, 0.6) : "transparent"
    border.width: 1
    border.color: isConnected ? Theme.alpha(Theme.ok, 0.30)
                : asking ? Theme.alpha(Theme.accent, 0.35) : "transparent"
    Behavior on color { ColorAnimation { duration: 160 } }

    HoverHandler { id: hover }

    Connections {
        target: row.device
        function onPairedChanged() {
            if (row.device.paired && row.wantConnect) {
                row.wantConnect = false;
                row.device.trusted = true;
                row.device.connect();
            }
        }
    }

    function primary() {
        if (isConnected) device.disconnect();
        else if (isPaired) device.connect();
        else { wantConnect = true; device.pair(); }
    }

    Column {
        id: col
        width: parent.width

        Item {
            width: parent.width
            height: 46

            Rectangle {
                id: tile
                width: Tokens.controlTile; height: Tokens.controlTile; radius: 8
                anchors.left: parent.left; anchors.leftMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                color: row.isConnected ? Theme.alpha(Theme.ok, 0.14) : Theme.alpha(Tokens.fg, 0.05)
                Txt {
                    anchors.centerIn: parent
                    font.pixelSize: Tokens.sizeIcon
                    text: Theme.deviceGlyph(row.device.icon)
                    color: row.isConnected ? Theme.ok : Theme.textSoft
                }
            }

            Column {
                anchors.left: tile.right; anchors.leftMargin: 10
                anchors.right: actions.left; anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                spacing: 3

                Txt {
                    width: parent.width
                    text: row.device.name || row.device.address
                    font.weight: Font.Bold
                    color: Tokens.bright
                    elide: Text.ElideRight
                }
                Row {
                    spacing: 6
                    Rectangle {
                        visible: row.isConnected && !row.busy
                        width: 6; height: 6; radius: 3
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.ok
                    }
                    Txt {
                        id: spin
                        visible: row.busy
                        anchors.verticalCenter: parent.verticalCenter
                        font.pixelSize: Tokens.sizeSmall
                        text: String.fromCodePoint(0xF0450)
                        color: Theme.muted
                        RotationAnimator on rotation { running: spin.visible; from: 0; to: 360; duration: 900; loops: Animation.Infinite }
                    }
                    Txt {
                        anchors.verticalCenter: parent.verticalCenter
                        text: row.subText
                        font.pixelSize: Tokens.sizeSmall
                        color: row.isConnected && !row.busy ? Theme.ok : row.asking ? Theme.accent : Theme.muted
                    }
                    // battery
                    Row {
                        visible: row.hasBattery
                        spacing: 4
                        anchors.verticalCenter: parent.verticalCenter
                        Row {
                            spacing: 1
                            anchors.verticalCenter: parent.verticalCenter
                            Rectangle {
                                width: 20; height: 10; radius: 3
                                color: "transparent"
                                border.width: 1.5; border.color: Theme.dim
                                Rectangle {
                                    x: 2.5; y: 2.5
                                    height: 5; radius: 1
                                    width: Math.max(2, Math.round(row.batteryPct / 100 * 15))
                                    color: row.batteryPct <= 20 ? Theme.warn : Theme.ok
                                }
                            }
                            Rectangle { width: 2; height: 4; radius: 1; color: Theme.dim; anchors.verticalCenter: parent.verticalCenter }
                        }
                        Txt {
                            anchors.verticalCenter: parent.verticalCenter
                            text: row.batteryPct + "%"
                            font.pixelSize: Tokens.sizeSmall
                            color: Theme.textSoft
                        }
                    }
                }
            }

            Row {
                id: actions
                anchors.right: parent.right; anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4

                Btn {
                    visible: (row.isConnected || row.isPaired) && !row.busy
                    variant: "icon"
                    glyph: Theme.iTrash
                    onClicked: row.device.forget()
                }
                Btn {
                    visible: !row.asking
                    width: 88
                    variant: row.busy ? "busy" : (row.isConnected ? "ghost" : "primary")
                    spinning: false
                    text: row.busy ? "Working…" : row.isConnected ? "Disconnect" : row.isPaired ? "Connect" : "Pair"
                    onClicked: row.primary()
                }
                Btn {
                    visible: row.isPairing && !row.asking
                    variant: "icon"
                    glyph: String.fromCodePoint(0xF0156) // close
                    onClicked: { row.wantConnect = false; row.device.cancelPair(); }
                }
            }
        }

        // passkey / authorization box from the agent
        Item {
            visible: row.asking
            width: parent.width
            height: visible ? box.height + 10 : 0

            Rectangle {
                id: box
                x: 8; width: parent.width - 16
                height: boxCol.implicitHeight + 28
                radius: 8
                color: Tokens.surface
                border.width: 1; border.color: Theme.border

                Column {
                    id: boxCol
                    x: 14; y: 14
                    width: parent.width - 28
                    spacing: 12

                    Txt {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.muted
                        text: !row.asking ? ""
                            : row.agentReq.type === "display" ? "Type this code on " + row.device.name + ", then press Enter."
                            : row.agentReq.type === "authorize" ? "Allow " + row.device.name + " to pair with this computer?"
                            : "Check that this passkey matches the one shown on " + row.device.name + "."
                    }
                    Txt {
                        visible: row.asking && (row.agentReq.passkey || "") !== ""
                        text: row.asking && row.agentReq.passkey ? row.agentReq.passkey.replace(/^(\d{3})(\d{3})$/, "$1 $2") : ""
                        font.pixelSize: Tokens.sizeHero
                        font.weight: Font.Bold
                        font.letterSpacing: 4
                        color: Theme.accent
                    }
                    Row {
                        visible: row.asking && row.agentReq.type !== "display"
                        width: parent.width
                        spacing: 8
                        Btn {
                            width: (parent.width - 8) / 2
                            variant: "ghost"
                            text: "Cancel"
                            onClicked: { row.wantConnect = false; row.answer(false); }
                        }
                        Btn {
                            width: (parent.width - 8) / 2
                            variant: "primary"
                            text: "Pair"
                            onClicked: row.answer(true)
                        }
                    }
                    Btn {
                        visible: row.asking && row.agentReq.type === "display"
                        width: parent.width
                        variant: "ghost"
                        text: "Cancel"
                        onClicked: { row.wantConnect = false; row.device.cancelPair(); }
                    }
                }
            }
        }
    }
}
