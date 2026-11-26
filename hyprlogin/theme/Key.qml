import QtQuick

// key hint: `key` in a chip, then what it does (the hypr-ui .footer .key / .hint pair)
Row {
    id: k
    property QtObject th
    property string key: ""
    property string hint: ""
    spacing: th.px(5)

    Rectangle {
        anchors.verticalCenter: parent.verticalCenter
        width: kt.implicitWidth + th.px(10)
        height: th.px(18)
        radius: th.radiusChip
        color: th.raised
        Txt { id: kt; th: k.th; anchors.centerIn: parent; text: k.key; font.pixelSize: th.sizeSmall; color: th.fg }
    }
    Txt { th: k.th; anchors.verticalCenter: parent.verticalCenter; text: k.hint; font.pixelSize: th.sizeSmall + 0.5; color: th.muted }
}
