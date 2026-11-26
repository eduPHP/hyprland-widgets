import QtQuick

// hyprlogin tokens. The defaults are hypr-ui's tokens.json (Tokyo Night); `hyprlogin sync`
// writes the current Omarchy theme's colours and wallpaper into theme.conf.user, which SDDM
// reads over theme.conf. Every colour, size and radius in the theme comes from here.
QtObject {
    id: th

    function conf(key, dflt) {
        const v = config[key];
        return (v === undefined || v === null || String(v) === "") ? dflt : String(v);
    }
    function num(key, dflt) {
        const v = parseFloat(conf(key, ""));
        return isNaN(v) ? dflt : v;
    }

    // colours (hypr-ui names)
    readonly property color bg: conf("bg", "#16161e")
    readonly property color surface: conf("surface", "#1f2335")
    readonly property color raised: conf("raised", "#24283b")
    readonly property color line: conf("line", "#292e42")
    readonly property color frame: conf("frame", "#3f537d")
    readonly property color fg: conf("fg", "#c0caf5")
    readonly property color bright: conf("bright", "#e6e8ef")
    readonly property color muted: conf("muted", "#737aa2")
    readonly property color faint: conf("faint", "#565f89")
    readonly property color accent: conf("blue", "#7aa2f7")
    readonly property color green: conf("green", "#9ece6a")
    readonly property color amber: conf("amber", "#e0af68")
    readonly property color orange: conf("orange", "#ff9e64")
    readonly property color red: conf("red", "#f7768e")
    readonly property color ink: conf("ink", "#16161e")
    readonly property bool light: conf("mode", "dark") === "light"

    // the desktop: wallpaper, how much it is blurred and dimmed behind the card
    readonly property string background: conf("background", "background.jpg")
    readonly property real blur: num("blur", 1.0)
    readonly property real dim: num("dim", 0.35)
    readonly property string themeName: conf("themeName", "tokyo-night")

    // layout scale: 1.0 is the desktop at 1080 px tall; edu-dev's laptop runs Hyprland at 1.2,
    // and weston runs the greeter at 1.0, so 1.2 keeps the card the size of the other widgets.
    readonly property real scale: num("scale", 1.2)
    property real u: 1
    function px(n) { return Math.round(n * u); }

    property string family: conf("font", "JetBrainsMono Nerd Font")

    // hypr-ui sizes and radii (tokens.json)
    readonly property int sizeSmall: px(10)
    readonly property int sizeCaption: px(11)
    readonly property int sizeBody: px(12)
    readonly property int sizeTitle: px(13)
    readonly property int sizeIcon: px(15)
    readonly property int sizeBig: px(18)
    readonly property int sizeHero: px(22)
    readonly property int radiusSurface: px(12)
    readonly property int radiusCard: px(10)
    readonly property int radiusControl: px(7)
    readonly property int radiusChip: px(4)
    readonly property int pad: px(12)
    readonly property int gap: px(10)
    readonly property int controlHeight: px(30)
    readonly property real shadowAlpha: 0.38
    readonly property int shadowY: px(4)
    readonly property int shadowBlur: px(12)

    // preview states for screenshots (`hyprlogin preview --state error`); empty on a real login
    readonly property string demo: conf("demo", "")
}
