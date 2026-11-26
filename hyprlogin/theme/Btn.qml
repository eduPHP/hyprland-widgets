import QtQuick
import QtQuick.Effects

// hypr-ui button. variant: "ghost" (outline), "primary" (accent fill), "icon" (borderless glyph),
// "flat" (text on the wallpaper with a soft shadow; a panel shows on hover or focus).
// danger: turns red on hover, like the power row in hyprmenu.
Rectangle {
    id: btn
    property QtObject th
    property string text: ""
    property string glyph: ""
    property string variant: "ghost"
    property bool danger: false
    property bool armed: false          // asked once, waiting for the second press
    property color tint: "transparent"  // glyph colour for "icon" buttons; transparent = the usual muted/fg
    signal clicked()

    readonly property bool hot: mouse.containsMouse || activeFocus || armed
    readonly property color tone: danger && hot ? th.red
                                : variant === "flat" ? (hot ? th.bright : th.fg)
                                : (hot ? th.fg : th.muted)

    activeFocusOnTab: true
    implicitHeight: variant === "primary" ? th.controlHeight + th.px(6) : th.px(26)
    implicitWidth: variant === "icon" ? implicitHeight
                 : row.implicitWidth + th.px(variant === "primary" ? 22 : 18)
    radius: th.radiusControl
    color: variant === "primary" ? (mouse.containsMouse ? Qt.lighter(th.accent, 1.1) : th.accent)
         : variant === "flat" ? (hot ? Qt.rgba(th.bg.r, th.bg.g, th.bg.b, 0.72) : "transparent")
         : (mouse.containsMouse && variant === "ghost") ? th.raised
         : "transparent"
    border.width: variant === "primary" ? 0
                : variant === "flat" ? (hot ? 1 : 0)
                : (variant === "icon" && !activeFocus ? 0 : 1)
    border.color: danger && hot ? th.red
                : activeFocus ? th.accent
                : variant === "flat" ? th.frame
                : (mouse.containsMouse ? th.faint : th.line)
    Behavior on color { ColorAnimation { duration: 120 } }
    Behavior on border.color { ColorAnimation { duration: 120 } }

    Keys.onReturnPressed: btn.clicked()
    Keys.onEnterPressed: btn.clicked()
    Keys.onSpacePressed: btn.clicked()

    Row {
        id: row
        anchors.centerIn: parent
        spacing: th.px(6)
        layer.enabled: btn.variant === "flat" && !btn.hot
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: btn.th.light ? Qt.rgba(1, 0.99, 0.94, 0.6) : Qt.rgba(0, 0, 0, 0.45)
            shadowBlur: 0.45
            shadowVerticalOffset: btn.th.px(1)
            autoPaddingEnabled: true
        }
        Txt {
            th: btn.th
            visible: btn.glyph !== ""
            anchors.verticalCenter: parent.verticalCenter
            text: btn.glyph
            font.pixelSize: btn.variant === "primary" ? th.sizeIcon + th.px(1) : th.sizeCaption + th.px(1)
            color: btn.variant === "primary" ? th.ink
                 : (btn.variant === "icon" && btn.tint.a > 0) ? btn.tint
                 : btn.tone
        }
        Txt {
            th: btn.th
            visible: btn.text !== ""
            anchors.verticalCenter: parent.verticalCenter
            text: btn.text
            font.pixelSize: th.sizeCaption
            font.weight: btn.variant === "primary" ? Font.Bold : Font.Normal
            color: btn.variant === "primary" ? th.ink : btn.tone
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: btn.clicked()
    }
}
