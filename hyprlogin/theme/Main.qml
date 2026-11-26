// hyprlogin: an SDDM login screen in the hypr-ui look (the start menu, theme picker and popovers).
// Colours and wallpaper follow the Omarchy theme through `hyprlogin sync` (theme.conf.user).
//
//   enter      log in                  ↑ ↓      change session
//   alt ← →    change user             esc      clear / close the list
//   tab        move between controls   restart and shut down ask for a second press
import QtQuick
import QtQuick.Effects

Rectangle {
    id: root
    width: 1920
    height: 1080
    color: th.bg

    readonly property QtObject th: tokens
    Theme {
        id: tokens
        u: Math.max(0.75, Math.min(root.width / 1600, root.height / 1080)) * th.scale
        family: fontRegular.status === FontLoader.Ready ? fontRegular.name : th.conf("font", "JetBrainsMono Nerd Font")
    }
    // The greeter runs as the sddm user, which can't read ~/.local/share/fonts: install.sh copies
    // JetBrainsMono Nerd Font into fonts/ next to this file.
    FontLoader { id: fontRegular; source: "fonts/JetBrainsMonoNerdFont-Regular.ttf" }
    FontLoader { source: "fonts/JetBrainsMonoNerdFont-Bold.ttf" }
    FontLoader { id: fontLight; source: "fonts/JetBrainsMonoNerdFont-Light.ttf" }

    readonly property bool isPrimary: typeof primaryScreen === "undefined" || primaryScreen
                                      || th.conf("allScreens", "false") === "true"

    // --- state ---------------------------------------------------------------------------------
    property int userIndex: userModel.lastIndex >= 0 ? userModel.lastIndex : 0
    property int sessionIndex: sessionModel.lastIndex >= 0 ? sessionModel.lastIndex : 0
    property bool busy: th.demo === "busy"
    property bool failed: th.demo === "error"
    property bool sessionsOpen: th.demo === "sessions"
    property string armed: ""           // "reboot" / "poweroff" after the first press
    readonly property string host: sddm.hostName || th.conf("host", "localhost")
    // test mode can't power anything off, so the preview shows the buttons regardless
    readonly property bool preview: th.conf("preview", "false") === "true"
    readonly property bool caps: keyboard.capsLock || th.demo === "caps"

    // The models only expose their roles to delegates; mirror them so the screen can read them.
    Item {
        visible: false
        Repeater {
            id: users
            model: userModel
            delegate: Item {
                property string uName: model.name
                property string uReal: model.realName || ""
                property string uIcon: model.icon || ""
            }
        }
        Repeater {
            id: sessions
            model: sessionModel
            delegate: Item { property string sName: model.name || model.file || "" }
        }
    }
    function userAt(i) { return users.count > 0 ? users.itemAt(Math.max(0, Math.min(i, users.count - 1))) : null; }
    readonly property var user: users.count >= 0 ? userAt(userIndex) : null
    readonly property string userName: user ? user.uName : ""
    readonly property string realName: user ? (user.uReal !== "" ? user.uReal : user.uName) : "user"
    readonly property string userIcon: user ? user.uIcon : ""
    function sessionName(i) { const s = sessions.count > i ? sessions.itemAt(i) : null; return s ? s.sName : "session"; }
    function sessionGlyph(name) {
        const n = name.toLowerCase();
        if (n.indexOf("hyprland") >= 0) return "";
        if (n.indexOf("weston") >= 0 || n.indexOf("shell") >= 0) return "";
        if (n.indexOf("gnome") >= 0 || n.indexOf("plasma") >= 0) return "";
        return "";
    }

    // sddm-greeter leaves Qt.quit() unconnected, so `hyprlogin preview` watches for this line
    function closePreview() { console.log("hyprlogin:close-preview"); }
    function login() {
        // test mode can't log anyone in; leave the preview instead of trapping the screen
        if (preview) { closePreview(); return; }
        if (busy) return;
        failed = false;
        busy = true;
        sessionsOpen = false;
        sddm.login(userName, password.text, sessionIndex);
        loginTimeout.restart();
    }
    function cycleSession(step) {
        if (sessions.count < 2) return;
        sessionIndex = (sessionIndex + step + sessions.count) % sessions.count;
    }
    function cycleUser(step) {
        if (users.count < 2) return;
        userIndex = (userIndex + step + users.count) % users.count;
        password.text = "";
        failed = false;
    }
    function power(action) {
        if (action === "suspend") { sddm.suspend(); return; }
        if (armed !== action) { armed = action; disarm.restart(); return; }
        armed = "";
        if (action === "reboot") sddm.reboot(); else sddm.powerOff();
    }

    Connections {
        target: sddm
        function onLoginFailed() {
            root.busy = false;
            root.failed = true;
            loginTimeout.stop();
            password.text = "";
            password.forceActiveFocus();
            shake.restart();
        }
        function onLoginSucceeded() { loginTimeout.stop(); }
    }
    Timer { id: loginTimeout; interval: 20000; onTriggered: root.busy = false }
    Timer { id: disarm; interval: 3000; onTriggered: root.armed = "" }

    property date now: new Date()
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = new Date() }

    // --- desktop: the theme's wallpaper, blurred and dimmed like hyprmenu's veil ---------------
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0; color: th.surface }
            GradientStop { position: 1; color: th.bg }
        }
    }
    Image {
        id: wall
        anchors.fill: parent
        source: th.background
        fillMode: Image.PreserveAspectCrop
        asynchronous: false
        cache: false
        visible: false
    }
    MultiEffect {
        anchors.fill: wall
        source: wall
        visible: wall.status === Image.Ready
        blurEnabled: th.blur > 0
        blur: th.blur
        blurMax: 64
        blurMultiplier: 0.8     // about hyprlock-theme's blur_passes = 3, blur_size = 8
        autoPaddingEnabled: false
    }
    Rectangle { anchors.fill: parent; color: th.bg; opacity: th.dim }

    // --- top strip, the size of waybar --------------------------------------------------------
    Rectangle {
        id: bar
        anchors { top: parent.top; left: parent.left; right: parent.right }
        height: th.px(26)
        color: Qt.rgba(th.bg.r, th.bg.g, th.bg.b, 0.55)

        Row {
            anchors { left: parent.left; leftMargin: th.pad; verticalCenter: parent.verticalCenter }
            spacing: th.px(6)
            Txt { th: tokens; text: "󰌢"; color: th.accent; font.pixelSize: th.sizeBody }
            Txt { th: tokens; text: root.host; font.pixelSize: th.sizeCaption }
        }
        Txt {
            th: tokens
            anchors.centerIn: parent
            visible: !root.isPrimary
            text: Qt.formatDateTime(root.now, "ddd d MMM  HH:mm").toLowerCase()
            font.pixelSize: th.sizeCaption
        }
        Row {
            anchors { right: parent.right; rightMargin: th.pad; verticalCenter: parent.verticalCenter }
            spacing: th.px(12)
            Row {
                visible: root.caps
                spacing: th.px(5)
                Txt { th: tokens; text: "󰘲"; color: th.orange; font.pixelSize: th.sizeBody }
                Txt { th: tokens; text: "caps"; color: th.orange; font.pixelSize: th.sizeCaption }
            }
            Row {
                spacing: th.px(6)
                visible: keyboard.layouts.length > 0
                Txt { th: tokens; text: ""; color: th.muted; font.pixelSize: th.sizeBody }
                Txt {
                    th: tokens
                    text: keyboard.layouts.length > 0 ? keyboard.layouts[keyboard.currentLayout].shortName : ""
                    font.pixelSize: th.sizeCaption
                }
            }
        }
    }

    // --- the login stack, no box (matches hyprlock-theme v2) ---------------------------------
    // Clock, avatar, name, field, session and power sit straight on the dimmed wallpaper; text gets a
    // soft shadow so it reads on any wallpaper (a light one on light themes, where dark reads as a smudge).
    readonly property color textShadow: th.light ? Qt.rgba(1, 0.99, 0.94, 0.6) : Qt.rgba(0, 0, 0, 0.45)
    Component {
        id: softShadow
        MultiEffect {
            shadowEnabled: true
            shadowColor: root.textShadow
            shadowBlur: 0.45
            shadowOpacity: 1
            shadowVerticalOffset: th.px(1)
            autoPaddingEnabled: true
        }
    }

    Column {
        id: stack
        visible: root.isPrimary
        width: th.px(320)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        anchors.verticalCenterOffset: -th.px(14)
        spacing: 0
        transform: Translate { id: nudge; x: 0 }

        SequentialAnimation {
            id: shake
            NumberAnimation { target: nudge; property: "x"; to: -th.px(10); duration: 50 }
            NumberAnimation { target: nudge; property: "x"; to: th.px(9); duration: 70 }
            NumberAnimation { target: nudge; property: "x"; to: -th.px(6); duration: 60 }
            NumberAnimation { target: nudge; property: "x"; to: th.px(3); duration: 50 }
            NumberAnimation { target: nudge; property: "x"; to: 0; duration: 40 }
        }

        // clock and date
        Column {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: th.px(2)
            layer.enabled: true
            layer.effect: softShadow
            Txt {
                th: tokens
                anchors.horizontalCenter: parent.horizontalCenter
                text: Qt.formatTime(root.now, "HH:mm")
                color: th.bright
                font.family: fontLight.status === FontLoader.Ready ? fontLight.name : th.family
                font.pixelSize: th.px(76)
                font.weight: Font.Light
                renderType: Text.QtRendering
            }
            Txt {
                th: tokens
                anchors.horizontalCenter: parent.horizontalCenter
                text: Qt.formatDate(root.now, "dddd, d MMMM").toLowerCase()
                color: th.fg
                font.pixelSize: th.px(15)
                renderType: Text.QtRendering
            }
        }

        Item { width: 1; height: th.px(28) }

        // avatar: a 76 px circle with a 2 px frame, arrows beside it when there is more than one user
        Item {
            anchors.horizontalCenter: parent.horizontalCenter
            width: th.px(76); height: width

            RectangularShadow {
                anchors.fill: parent
                radius: width / 2
                offset.y: th.px(4)
                blur: th.px(12)
                color: th.light ? Qt.rgba(0, 0, 0, 0.08) : Qt.rgba(0, 0, 0, 0.38)
            }
            Rectangle {
                id: avatar
                anchors.fill: parent
                radius: width / 2
                color: th.raised
                readonly property bool hasFace: face.status === Image.Ready
                Txt {
                    th: tokens
                    anchors.centerIn: parent
                    visible: !avatar.hasFace
                    text: root.realName.charAt(0).toUpperCase()
                    color: th.accent
                    font.pixelSize: th.px(28)
                    font.weight: Font.Bold
                }
                Image {
                    id: face
                    anchors.fill: parent
                    source: root.userIcon !== "" && root.userIcon.indexOf("/sddm/faces/") < 0 ? root.userIcon : ""
                    fillMode: Image.PreserveAspectCrop
                    visible: false
                    smooth: true
                    mipmap: true
                }
                Rectangle { id: faceMask; anchors.fill: face; radius: width / 2; visible: false; layer.enabled: true }
                MultiEffect {
                    anchors.fill: face
                    source: face
                    visible: avatar.hasFace
                    maskEnabled: true
                    maskSource: faceMask
                }
            }
            Rectangle {
                anchors.fill: parent
                radius: width / 2
                color: "transparent"
                border.width: th.px(2)
                border.color: th.frame
            }
            Btn {
                th: tokens
                visible: users.count > 1
                variant: "flat"
                glyph: ""
                activeFocusOnTab: false
                anchors { right: parent.left; rightMargin: th.px(14); verticalCenter: parent.verticalCenter }
                onClicked: root.cycleUser(-1)
            }
            Btn {
                th: tokens
                visible: users.count > 1
                variant: "flat"
                glyph: ""
                activeFocusOnTab: false
                anchors { left: parent.right; leftMargin: th.px(14); verticalCenter: parent.verticalCenter }
                onClicked: root.cycleUser(1)
            }
        }

        Item { width: 1; height: th.px(10) }

        Column {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: th.px(2)
            layer.enabled: true
            layer.effect: softShadow
            Txt {
                th: tokens
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.realName
                color: th.bright
                font.pixelSize: th.px(17)
                font.weight: Font.Bold
                renderType: Text.QtRendering
            }
            Txt {
                th: tokens
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.userName + "@" + root.host
                color: th.fg
                font.pixelSize: th.sizeCaption
                renderType: Text.QtRendering
            }
        }

        Item { width: 1; height: th.px(16) }

        // password field: accent outline, orange with caps lock, green while logging in, red on a failure
        Item {
            anchors.horizontalCenter: parent.horizontalCenter
            width: th.px(300)
            height: th.px(36)

            RectangularShadow {
                anchors.fill: parent
                radius: field.radius
                offset.y: th.px(4)
                blur: th.px(12)
                color: th.light ? Qt.rgba(0, 0, 0, 0.06) : Qt.rgba(0, 0, 0, 0.25)
            }
            Rectangle {
                id: field
                anchors.fill: parent
                radius: th.radiusControl
                color: Qt.rgba(th.bg.r, th.bg.g, th.bg.b, 0.72)
                border.width: 1
                border.color: root.failed ? th.red : root.busy ? th.green : root.caps ? th.orange : th.accent
                Behavior on border.color { ColorAnimation { duration: 160 } }

                Txt {
                    id: lockGlyph
                    th: tokens
                    anchors { left: parent.left; leftMargin: th.px(12); verticalCenter: parent.verticalCenter }
                    text: root.failed ? "" : "󰌾"
                    color: root.failed ? th.red : th.muted
                    font.pixelSize: th.px(13)
                }
                TextInput {
                    id: password
                    anchors { left: parent.left; right: parent.right; leftMargin: th.px(34); rightMargin: th.px(34); verticalCenter: parent.verticalCenter }
                    horizontalAlignment: TextInput.AlignHCenter
                    focus: true
                    echoMode: TextInput.Password
                    passwordCharacter: "●"
                    passwordMaskDelay: 0
                    color: th.fg
                    selectionColor: th.accent
                    selectedTextColor: th.ink
                    font.family: th.family
                    font.pixelSize: th.px(9)
                    font.letterSpacing: th.px(5)
                    clip: true
                    enabled: !root.busy
                    opacity: root.busy ? 0.55 : 1
                    text: th.demo === "busy" || th.demo === "typing" ? "correct horse" : ""
                    onTextChanged: if (text !== "") root.failed = false
                    Keys.onReturnPressed: root.login()
                    Keys.onEnterPressed: root.login()
                    Keys.onUpPressed: root.cycleSession(-1)
                    Keys.onDownPressed: root.cycleSession(1)
                    Keys.onEscapePressed: { if (root.sessionsOpen) root.sessionsOpen = false; else if (text === "" && root.preview) root.closePreview(); else text = ""; }
                    Keys.onPressed: (e) => {
                        if ((e.modifiers & Qt.AltModifier) && (e.key === Qt.Key_Left || e.key === Qt.Key_Right)) {
                            root.cycleUser(e.key === Qt.Key_Left ? -1 : 1);
                            e.accepted = true;
                        }
                    }
                    Component.onCompleted: if (root.isPrimary) forceActiveFocus()
                    SequentialAnimation on opacity {
                        running: root.busy
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.3; duration: 450 }
                        NumberAnimation { to: 1; duration: 450 }
                    }
                }
                Txt {
                    th: tokens
                    anchors.centerIn: parent
                    visible: password.text === "" && !root.busy
                    text: root.failed ? "wrong password, try again" : "password"
                    color: root.failed ? th.red : th.faint
                    font.pixelSize: th.sizeBody
                }
                Btn {
                    id: go
                    th: tokens
                    anchors { right: parent.right; rightMargin: th.px(4); verticalCenter: parent.verticalCenter }
                    variant: "icon"
                    activeFocusOnTab: false
                    visible: password.text !== "" || root.busy
                    glyph: root.busy ? "󰑐" : "󰁔"
                    tint: th.accent
                    onClicked: root.login()
                    RotationAnimator on rotation { running: root.busy; from: 0; to: 360; duration: 900; loops: Animation.Infinite }
                }
            }
        }

        // one status line under the field: logging in / caps lock / nothing
        Item {
            width: parent.width
            height: status.text === "" ? th.px(10) : th.px(28)
            Txt {
                id: status
                th: tokens
                anchors.centerIn: parent
                text: root.busy ? "logging in to " + root.sessionName(root.sessionIndex).toLowerCase() + "…"
                    : root.caps ? "󰘲 caps lock is on" : ""
                color: root.busy ? th.green : th.orange
                font.pixelSize: th.sizeCaption
                renderType: Text.QtRendering
                layer.enabled: true
                layer.effect: softShadow
            }
        }

        // session: a quiet line under the field; the list opens in a small panel
        Rectangle {
            id: sessionChip
            anchors.horizontalCenter: parent.horizontalCenter
            width: chipRow.implicitWidth + th.px(20)
            height: th.px(28)
            radius: th.radiusControl
            color: chipMouse.containsMouse || activeFocus || root.sessionsOpen ? Qt.rgba(th.bg.r, th.bg.g, th.bg.b, 0.72) : "transparent"
            border.width: 1
            border.color: activeFocus || root.sessionsOpen ? th.accent : (chipMouse.containsMouse ? th.frame : "transparent")
            activeFocusOnTab: true
            Keys.onReturnPressed: root.sessionsOpen = !root.sessionsOpen
            Keys.onSpacePressed: root.sessionsOpen = !root.sessionsOpen
            Keys.onUpPressed: root.cycleSession(-1)
            Keys.onDownPressed: root.cycleSession(1)
            Keys.onEscapePressed: root.sessionsOpen = false

            Row {
                id: chipRow
                anchors.centerIn: parent
                spacing: th.px(8)
                layer.enabled: true
                layer.effect: softShadow
                Txt { th: tokens; text: root.sessionGlyph(root.sessionName(root.sessionIndex)); color: th.accent; font.pixelSize: th.sizeBody + th.px(1); renderType: Text.QtRendering }
                Txt { th: tokens; text: root.sessionName(root.sessionIndex); color: th.fg; font.pixelSize: th.sizeBody; renderType: Text.QtRendering }
                Txt {
                    th: tokens
                    visible: sessions.count > 1
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.sessionsOpen ? "" : ""
                    color: th.muted
                    font.pixelSize: th.sizeSmall
                    renderType: Text.QtRendering
                }
            }
            MouseArea {
                id: chipMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: if (sessions.count > 1) root.sessionsOpen = !root.sessionsOpen
            }
        }

        Item { width: 1; height: root.sessionsOpen ? th.px(6) : 0 }

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: root.sessionsOpen
            width: th.px(300)
            height: sessionList.implicitHeight + th.px(10)
            radius: th.radiusCard
            color: Qt.rgba(th.bg.r, th.bg.g, th.bg.b, 0.9)
            border.width: 1
            border.color: th.frame
            Column {
                id: sessionList
                x: th.px(5); y: th.px(5)
                width: parent.width - th.px(10)
                spacing: 1
                Repeater {
                    model: root.sessionsOpen ? sessions.count : 0
                    delegate: Rectangle {
                        required property int index
                        readonly property bool sel: index === root.sessionIndex
                        width: sessionList.width
                        height: th.controlHeight
                        radius: th.radiusControl
                        color: sel ? Qt.rgba(th.accent.r, th.accent.g, th.accent.b, 0.14)
                             : (rowMouse.containsMouse ? th.raised : "transparent")
                        border.width: 1
                        border.color: sel ? th.accent : "transparent"
                        Txt {
                            id: rg
                            th: tokens
                            anchors { left: parent.left; leftMargin: th.px(10); verticalCenter: parent.verticalCenter }
                            text: root.sessionGlyph(root.sessionName(index))
                            color: sel ? th.accent : th.muted
                            font.pixelSize: th.sizeBody + th.px(1)
                        }
                        Txt {
                            th: tokens
                            anchors { left: rg.right; leftMargin: th.px(9); verticalCenter: parent.verticalCenter }
                            text: root.sessionName(index)
                        }
                        Txt {
                            th: tokens
                            anchors { right: parent.right; rightMargin: th.px(10); verticalCenter: parent.verticalCenter }
                            visible: sel
                            text: ""
                            color: th.accent
                            font.pixelSize: th.sizeCaption
                        }
                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: { root.sessionIndex = index; root.sessionsOpen = false; password.forceActiveFocus(); }
                        }
                    }
                }
            }
        }

        Item { width: 1; height: th.px(14) }

        // power: quiet text buttons on the wallpaper, like hyprlock's footer line
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: th.px(6)
            Btn {
                th: tokens
                variant: "flat"
                visible: sddm.canSuspend || root.preview
                glyph: "󰤄"
                text: "suspend"
                onClicked: root.power("suspend")
            }
            Btn {
                th: tokens
                variant: "flat"
                visible: sddm.canReboot || root.preview
                danger: true
                armed: root.armed === "reboot"
                glyph: "󰜉"
                text: armed ? "again to restart" : "restart"
                onClicked: root.power("reboot")
            }
            Btn {
                th: tokens
                variant: "flat"
                visible: sddm.canPowerOff || root.preview
                danger: true
                armed: root.armed === "poweroff"
                glyph: ""
                text: armed ? "again to shut down" : "shut down"
                onClicked: root.power("poweroff")
            }
        }
    }

    // --- key hints, the same pill as hyprlock-theme ------------------------------------------------
    Rectangle {
        visible: root.isPrimary
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: th.px(24) }
        width: hints.implicitWidth + th.px(28)
        height: th.px(28)
        radius: height / 2
        color: Qt.rgba(th.bg.r, th.bg.g, th.bg.b, 0.82)
        border.width: 1
        border.color: th.frame

        Row {
            id: hints
            anchors.centerIn: parent
            spacing: th.px(14)
            Key { th: tokens; key: "enter"; hint: "log in" }
            Key { th: tokens; key: "↑↓"; hint: "session"; visible: sessions.count > 1 }
            Key { th: tokens; key: "alt ←→"; hint: "user"; visible: users.count > 1 }
            Key { th: tokens; key: "tab"; hint: "power" }
            Key { th: tokens; key: "esc"; hint: "clear" }
        }
    }
}
