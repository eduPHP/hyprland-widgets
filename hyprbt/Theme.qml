pragma Singleton
import QtQuick
import Quickshell

// hyprbt's names for the shared hypr-ui tokens (Tokens.qml is a symlink to
// ~/Work/hypr-ui/qml/Tokens.qml, generated from tokens.json). Retheme there,
// not here, so every widget changes together.
Singleton {
    readonly property color accent: Tokens.blue     // primary actions, scanning
    readonly property color ok: Tokens.green        // connected
    readonly property color warn: Tokens.orange     // low battery

    readonly property color panel: Tokens.bg
    readonly property color footer: Tokens.bg
    readonly property color surface: Tokens.surface
    readonly property color tile: Tokens.raised
    readonly property color line: Tokens.line
    readonly property color border: Tokens.line
    readonly property color borderStrong: Tokens.line
    readonly property color frame: Tokens.frame
    readonly property color trackOff: Tokens.line

    readonly property color text: Tokens.fg
    readonly property color textStrong: Tokens.bright
    readonly property color textSoft: Tokens.fg
    readonly property color muted: Tokens.muted
    readonly property color dim: Tokens.faint
    readonly property color accentInk: Tokens.ink

    readonly property string sans: Tokens.fontFamily
    readonly property string mono: Tokens.fontFamily

    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a); }

    // Nerd Font (Material Design) glyphs
    function glyph(cp) { return String.fromCodePoint(cp); }
    readonly property string iBluetooth: glyph(0xF00AF)
    readonly property string iBluetoothOff: glyph(0xF00B2)
    readonly property string iRefresh: glyph(0xF0450)
    readonly property string iTrash: glyph(0xF01B4)
    readonly property string iPower: glyph(0xF0425)

    // BlueZ reports a freedesktop icon name per device; map it to a glyph.
    function deviceGlyph(icon) {
        const i = icon || "";
        if (i.indexOf("headset") >= 0 || i.indexOf("headphone") >= 0) return glyph(0xF02CB);
        if (i.indexOf("audio") >= 0 || i.indexOf("speaker") >= 0) return glyph(0xF04C3);
        if (i.indexOf("mouse") >= 0) return glyph(0xF037D);
        if (i.indexOf("keyboard") >= 0) return glyph(0xF030C);
        if (i.indexOf("gaming") >= 0 || i.indexOf("joystick") >= 0) return glyph(0xF02B4);
        if (i.indexOf("phone") >= 0) return glyph(0xF03F2);
        if (i.indexOf("computer") >= 0) return glyph(0xF0322);
        if (i.indexOf("watch") >= 0) return glyph(0xF0589);
        if (i.indexOf("tablet") >= 0) return glyph(0xF04F7);
        return glyph(0xF00AF);
    }

    function deviceKind(icon) {
        const i = icon || "";
        if (i.indexOf("headset") >= 0 || i.indexOf("headphone") >= 0) return "Headphones";
        if (i.indexOf("audio") >= 0) return "Speaker";
        if (i.indexOf("mouse") >= 0) return "Mouse";
        if (i.indexOf("keyboard") >= 0) return "Keyboard";
        if (i.indexOf("gaming") >= 0) return "Gamepad";
        if (i.indexOf("phone") >= 0) return "Phone";
        if (i.indexOf("computer") >= 0) return "Computer";
        if (i.indexOf("watch") >= 0) return "Watch";
        return "Device";
    }
}
