import QtQuick

Text {
    property QtObject th
    color: th.fg
    font.family: th.family
    font.pixelSize: th.sizeBody
    renderType: Text.NativeRendering
    verticalAlignment: Text.AlignVCenter
}
