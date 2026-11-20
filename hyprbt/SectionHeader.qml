import QtQuick

Item {
    id: h
    property string label: ""
    property int count: 0
    property bool scanning: false

    width: parent ? parent.width : 380
    height: visible ? 28 : 0

    Row {
        id: r
        x: 8; anchors.bottom: parent.bottom; anchors.bottomMargin: 6
        spacing: 8
        Txt {
            text: h.label.toUpperCase()
            font.pixelSize: Tokens.sizeSmall
            font.letterSpacing: 1
            color: Tokens.faint
        }
        Txt {
            text: h.count
            font.pixelSize: Tokens.sizeSmall
            color: Tokens.faint
        }
        Item {
            visible: h.scanning
            width: 8; height: 8
            anchors.verticalCenter: parent.verticalCenter
            Rectangle {
                id: ping
                anchors.centerIn: parent
                width: 8; height: 8; radius: 4
                color: Theme.accent
                SequentialAnimation on scale {
                    running: h.scanning; loops: Animation.Infinite
                    NumberAnimation { from: 0.6; to: 2.2; duration: 1400; easing.type: Easing.OutCubic }
                }
                SequentialAnimation on opacity {
                    running: h.scanning; loops: Animation.Infinite
                    NumberAnimation { from: 0.9; to: 0; duration: 1400; easing.type: Easing.OutCubic }
                }
            }
            Rectangle { anchors.centerIn: parent; width: 6; height: 6; radius: 3; color: Theme.accent }
        }
    }
    Rectangle {
        anchors.left: r.right; anchors.leftMargin: 8
        anchors.right: parent.right; anchors.rightMargin: 8
        anchors.verticalCenter: r.verticalCenter
        height: 1
        color: Tokens.line
    }
}
