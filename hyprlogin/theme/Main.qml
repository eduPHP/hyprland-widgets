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

    // The models only expose their roles to delegates; mirror them so the card can read them.
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

    function login() {
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

    // --- clock --------------------------------------------------------------------------------
    Column {
        id: clock
        visible: root.isPrimary
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: card.top
        anchors.bottomMargin: th.px(30)
        spacing: th.px(2)
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "#000000"
            shadowOpacity: th.light ? 0.18 : 0.45
            shadowBlur: 0.8
            shadowVerticalOffset: th.px(2)
            autoPaddingEnabled: true
        }

        Txt {
            th: tokens
            anchors.horizontalCenter: parent.horizontalCenter
            text: Qt.formatTime(root.now, "HH:mm")
            color: th.bright
            font.family: fontLight.status === FontLoader.Ready ? fontLight.name : th.family
            font.pixelSize: th.px(76)
            font.weight: Font.Light
        }
        Txt {
            th: tokens
            anchors.horizontalCenter: parent.horizontalCenter
            text: Qt.formatDate(root.now, "dddd, d MMMM").toLowerCase()
            color: th.fg
            font.pixelSize: th.sizeTitle + th.px(2)
        }
    }

    // --- the card -----------------------------------------------------------------------------
    Item {
        id: card
        visible: root.isPrimary
        width: th.px(380)
        height: frame.height
        anchors.centerIn: parent
        anchors.verticalCenterOffset: th.px(40)
        transform: Translate { id: nudge; x: 0 }

        SequentialAnimation {
            id: shake
            loops: 1
            NumberAnimation { target: nudge; property: "x"; to: -th.px(10); duration: 50 }
            NumberAnimation { target: nudge; property: "x"; to: th.px(9); duration: 70 }
            NumberAnimation { target: nudge; property: "x"; to: -th.px(6); duration: 60 }
            NumberAnimation { target: nudge; property: "x"; to: th.px(3); duration: 50 }
            NumberAnimation { target: nudge; property: "x"; to: 0; duration: 40 }
        }

        // hypr-ui surface: the token shadow under a 1px opaque frame
        RectangularShadow {
            anchors.fill: frame
            radius: frame.radius
            offset.y: th.shadowY
            blur: th.shadowBlur
            color: Qt.rgba(0, 0, 0, th.shadowAlpha)
        }

        Rectangle {
            id: frame
            width: parent.width
            height: body.implicitHeight + foot.height + 2
            radius: th.radiusSurface
            color: th.bg
            border.width: 1
            border.color: root.failed ? th.red : th.frame
            Behavior on border.color { ColorAnimation { duration: 160 } }
            Behavior on height { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
            clip: true

            Column {
                id: body
                x: 1; y: 1
                width: parent.width - 2
                topPadding: th.px(18)
                bottomPadding: th.px(16)
                leftPadding: th.px(18)
                rightPadding: th.px(18)
                spacing: th.px(14)

                // user: avatar, name, user@host, and arrows when there is more than one user
                Item {
                    width: body.width - body.leftPadding - body.rightPadding
                    height: th.px(48)

                    Rectangle {
                        id: avatar
                        width: th.px(48); height: width
                        radius: th.radiusCard
                        color: th.raised
                        border.width: 1
                        border.color: th.line
                        readonly property bool hasFace: face.status === Image.Ready

                        Txt {
                            th: tokens
                            anchors.centerIn: parent
                            visible: !avatar.hasFace
                            text: root.realName.charAt(0).toUpperCase()
                            color: th.accent
                            font.pixelSize: th.sizeBig + th.px(2)
                            font.weight: Font.Bold
                        }
                        Image {
                            id: face
                            anchors.fill: parent
                            anchors.margins: 1
                            source: root.userIcon !== "" && root.userIcon.indexOf("/sddm/faces/") < 0 ? root.userIcon : ""
                            fillMode: Image.PreserveAspectCrop
                            visible: false
                            smooth: true
                            mipmap: true
                        }
                        Rectangle { id: faceMask; anchors.fill: face; radius: th.radiusCard - 1; visible: false; layer.enabled: true }
                        MultiEffect {
                            anchors.fill: face
                            source: face
                            visible: avatar.hasFace
                            maskEnabled: true
                            maskSource: faceMask
                        }
                    }

                    Column {
                        anchors { left: avatar.right; leftMargin: th.px(12); right: userArrows.left; rightMargin: th.px(8); verticalCenter: avatar.verticalCenter }
                        spacing: th.px(3)
                        Txt {
                            th: tokens
                            width: parent.width
                            text: root.realName
                            color: th.bright
                            font.pixelSize: th.sizeTitle + th.px(2)
                            font.weight: Font.Bold
                            elide: Text.ElideRight
                        }
                        Txt {
                            th: tokens
                            width: parent.width
                            text: root.userName + "@" + (root.host)
                            color: th.muted
                            font.pixelSize: th.sizeCaption
                            elide: Text.ElideRight
                        }
                    }

                    Row {
                        id: userArrows
                        anchors { right: parent.right; verticalCenter: avatar.verticalCenter }
                        spacing: th.px(2)
                        visible: users.count > 1
                        width: visible ? implicitWidth : 0
                        Btn { th: tokens; variant: "icon"; glyph: ""; activeFocusOnTab: false; onClicked: root.cycleUser(-1) }
                        Btn { th: tokens; variant: "icon"; glyph: ""; activeFocusOnTab: false; onClicked: root.cycleUser(1) }
                    }
                }

                // password + log in
                Row {
                    spacing: th.px(8)
                    width: body.width - body.leftPadding - body.rightPadding

                    Rectangle {
                        id: field
                        width: parent.width - go.width - parent.spacing
                        height: th.controlHeight + th.px(6)
                        radius: th.radiusControl
                        color: th.surface
                        border.width: 1
                        border.color: root.failed ? th.red : (password.activeFocus ? th.accent : th.line)
                        Behavior on border.color { ColorAnimation { duration: 120 } }

                        Txt {
                            id: lockGlyph
                            th: tokens
                            anchors { left: parent.left; leftMargin: th.px(11); verticalCenter: parent.verticalCenter }
                            text: root.failed ? "" : ""
                            color: root.failed ? th.red : (password.activeFocus ? th.accent : th.muted)
                            font.pixelSize: th.sizeBody + th.px(1)
                        }
                        TextInput {
                            id: password
                            anchors { left: lockGlyph.right; leftMargin: th.px(10); right: reveal.left; rightMargin: th.px(6); verticalCenter: parent.verticalCenter }
                            focus: true
                            echoMode: reveal.shown ? TextInput.Normal : TextInput.Password
                            passwordCharacter: "•"
                            passwordMaskDelay: 0
                            color: th.fg
                            selectionColor: th.accent
                            selectedTextColor: th.ink
                            font.family: th.family
                            font.pixelSize: th.sizeBody + th.px(1)
                            font.letterSpacing: reveal.shown ? 0 : th.px(2)
                            clip: true
                            enabled: !root.busy
                            text: th.demo === "busy" || th.demo === "typing" ? "correct horse" : ""
                            onTextChanged: if (text !== "") root.failed = false
                            Keys.onReturnPressed: root.login()
                            Keys.onEnterPressed: root.login()
                            Keys.onUpPressed: root.cycleSession(-1)
                            Keys.onDownPressed: root.cycleSession(1)
                            Keys.onEscapePressed: { if (root.sessionsOpen) root.sessionsOpen = false; else text = ""; }
                            Keys.onPressed: (e) => {
                                if ((e.modifiers & Qt.AltModifier) && (e.key === Qt.Key_Left || e.key === Qt.Key_Right)) {
                                    root.cycleUser(e.key === Qt.Key_Left ? -1 : 1);
                                    e.accepted = true;
                                }
                            }
                            Component.onCompleted: if (root.isPrimary) forceActiveFocus()

                            Txt {
                                th: tokens
                                anchors.verticalCenter: parent.verticalCenter
                                visible: password.text === "" && !root.busy
                                text: root.failed ? "wrong password, try again" : "password"
                                color: root.failed ? th.red : th.faint
                                font.pixelSize: th.sizeBody + th.px(1)
                            }
                        }
                        Btn {
                            id: reveal
                            th: tokens
                            property bool shown: false
                            anchors { right: parent.right; rightMargin: th.px(4); verticalCenter: parent.verticalCenter }
                            variant: "icon"
                            activeFocusOnTab: false
                            visible: password.text !== ""
                            glyph: shown ? "" : ""
                            onClicked: { shown = !shown; password.forceActiveFocus(); }
                        }
                    }

                    Btn {
                        id: go
                        th: tokens
                        variant: "primary"
                        width: field.height
                        height: field.height
                        glyph: root.busy ? "󰑐" : "󰁔"
                        activeFocusOnTab: false
                        onClicked: root.login()
                        RotationAnimator on rotation { running: root.busy; from: 0; to: 360; duration: 900; loops: Animation.Infinite }
                    }
                }

                // one status line: logging in / caps lock / nothing
                Item {
                    width: body.width - body.leftPadding - body.rightPadding
                    height: status.text === "" ? 0 : th.px(14)
                    visible: height > 0
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: th.px(6)
                        Txt {
                            th: tokens
                            visible: status.text !== ""
                            text: root.busy ? "" : "󰘲"
                            color: status.color
                            font.pixelSize: th.sizeCaption
                        }
                        Txt {
                            id: status
                            th: tokens
                            text: root.busy ? "logging in to " + root.sessionName(root.sessionIndex).toLowerCase() + "…"
                                : root.caps ? "caps lock is on" : ""
                            color: root.busy ? th.green : th.orange
                            font.pixelSize: th.sizeCaption
                        }
                    }
                }

                // session chooser
                Column {
                    width: body.width - body.leftPadding - body.rightPadding
                    spacing: th.px(4)

                    Rectangle {
                        id: sessionChip
                        width: parent.width
                        height: th.controlHeight
                        radius: th.radiusControl
                        color: chipMouse.containsMouse ? th.raised : "transparent"
                        border.width: 1
                        border.color: activeFocus || root.sessionsOpen ? th.accent : th.line
                        activeFocusOnTab: true
                        Keys.onReturnPressed: root.sessionsOpen = !root.sessionsOpen
                        Keys.onSpacePressed: root.sessionsOpen = !root.sessionsOpen
                        Keys.onUpPressed: root.cycleSession(-1)
                        Keys.onDownPressed: root.cycleSession(1)
                        Keys.onEscapePressed: root.sessionsOpen = false

                        Txt {
                            id: sGlyph
                            th: tokens
                            anchors { left: parent.left; leftMargin: th.px(10); verticalCenter: parent.verticalCenter }
                            text: root.sessionGlyph(root.sessionName(root.sessionIndex))
                            color: th.accent
                            font.pixelSize: th.sizeBody + th.px(1)
                        }
                        Txt {
                            th: tokens
                            anchors { left: sGlyph.right; leftMargin: th.px(9); right: sMeta.left; rightMargin: th.px(8); verticalCenter: parent.verticalCenter }
                            text: root.sessionName(root.sessionIndex)
                            elide: Text.ElideRight
                        }
                        Row {
                            id: sMeta
                            anchors { right: parent.right; rightMargin: th.px(10); verticalCenter: parent.verticalCenter }
                            spacing: th.px(8)
                            Txt { th: tokens; text: "session"; color: th.faint; font.pixelSize: th.sizeSmall }
                            Txt {
                                th: tokens
                                visible: sessions.count > 1
                                text: root.sessionsOpen ? "" : ""
                                color: th.muted
                                font.pixelSize: th.sizeSmall
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

                    // the list opens inside the card, rows like the start menu's
                    Column {
                        width: parent.width
                        visible: root.sessionsOpen
                        spacing: 1
                        Repeater {
                            model: root.sessionsOpen ? sessions.count : 0
                            delegate: Rectangle {
                                required property int index
                                readonly property bool sel: index === root.sessionIndex
                                width: parent.width
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
            }

            // footer: power row, like the start menu's
            Rectangle {
                id: foot
                x: 1
                y: body.implicitHeight + 1
                width: parent.width - 2
                height: th.px(44)
                color: Qt.tint(th.bg, Qt.rgba(th.surface.r, th.surface.g, th.surface.b, 0.45))
                bottomLeftRadius: th.radiusSurface - 1
                bottomRightRadius: th.radiusSurface - 1
                Rectangle { anchors { top: parent.top; left: parent.left; right: parent.right } height: 1; color: th.line }

                Row {
                    anchors { left: parent.left; leftMargin: th.px(18) - 1; verticalCenter: parent.verticalCenter }
                    spacing: th.px(4)
                    Btn {
                        th: tokens
                        visible: sddm.canSuspend || root.preview
                        glyph: "󰤄"
                        text: "suspend"
                        onClicked: root.power("suspend")
                    }
                    Btn {
                        th: tokens
                        visible: sddm.canReboot || root.preview
                        danger: true
                        armed: root.armed === "reboot"
                        glyph: "󰜉"
                        text: armed ? "again to restart" : "restart"
                        onClicked: root.power("reboot")
                    }
                    Btn {
                        th: tokens
                        visible: sddm.canPowerOff || root.preview
                        danger: true
                        armed: root.armed === "poweroff"
                        glyph: ""
                        text: armed ? "again to shut down" : "shut down"
                        onClicked: root.power("poweroff")
                    }
                }
                Txt {
                    th: tokens
                    anchors { right: parent.right; rightMargin: th.px(18) - 1; verticalCenter: parent.verticalCenter }
                    visible: root.armed === ""
                    text: th.themeName
                    color: th.faint
                    font.pixelSize: th.sizeSmall
                }
            }
        }
    }

    // --- key hints, a toast-shaped pill at the bottom ------------------------------------------
    Rectangle {
        visible: root.isPrimary
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: th.px(28) }
        width: hints.implicitWidth + th.px(28)
        height: th.px(32)
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
