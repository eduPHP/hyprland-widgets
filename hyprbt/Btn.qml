import QtQuick

// variant: "primary" (accent fill), "ghost" (outline), "busy" (disabled grey),
// "icon" (borderless square for a glyph)
Rectangle {
    id: btn
    property string text: ""
    property string glyph: ""
    property string variant: "ghost"
    property bool spinning: false
    property color tint: Theme.accent
    signal clicked()

    readonly property bool enabledBtn: variant !== "busy"
    implicitHeight: Tokens.controlHeight
    implicitWidth: variant === "icon" ? Tokens.controlHeight : Math.max(72, row.implicitWidth + 24)
    radius: Tokens.radiusControl
    color: variant === "primary" ? (mouse.containsMouse ? Qt.lighter(tint, 1.12) : tint)
         : variant === "busy" ? Theme.surface
         : mouse.containsMouse && variant !== "icon" ? Tokens.raised : "transparent"
    border.width: variant === "icon" || variant === "primary" ? 0 : 1
    border.color: variant === "ghost" && mouse.containsMouse ? Tokens.faint : Tokens.line
    Behavior on color { ColorAnimation { duration: 120 } }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 7
        Txt {
            visible: btn.glyph !== "" || btn.spinning
            anchors.verticalCenter: parent.verticalCenter
            font.pixelSize: Tokens.sizeIcon - 1
            text: btn.spinning ? String.fromCodePoint(0xF0450) : btn.glyph
            color: label.color
            RotationAnimator on rotation { running: btn.spinning; from: 0; to: 360; duration: 900; loops: Animation.Infinite }
            onVisibleChanged: if (!btn.spinning) rotation = 0
        }
        Txt {
            id: label
            visible: btn.text !== ""
            anchors.verticalCenter: parent.verticalCenter
            text: btn.text
            font.pixelSize: Tokens.sizeCaption
            font.weight: btn.variant === "primary" ? Font.Bold : Font.Normal
            color: btn.variant === "primary" ? Theme.accentInk
                 : btn.variant === "busy" ? Theme.dim
                 : btn.variant === "icon" ? (mouse.containsMouse ? Theme.text : Theme.muted)
                 : Theme.text
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: btn.enabledBtn ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (btn.enabledBtn) btn.clicked()
    }
}
