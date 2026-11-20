# hyprcal

Calendar popover for Hyprland in the hypr-ui look (`~/Work/hypr-ui`): a clock, a month grid,
an agenda from Google Calendar, and Calendly bookings and open slots.

- **compact**: click the clock in waybar (`hyprcal`)
- **full**: right-click the clock, or `SUPER ALT C` (`hyprcal full`)

Keys: `h/l` day · `j/k` week · `[ ]` month · `t` today · `tab` calendar filter · `f` compact/full ·
`y` copy Calendly link · `r` sync · `e` edit config · `esc` close. Click an event to open its
Meet/Zoom link (or the event in Google Calendar).

## Reminder

The next event appears as a card centred under the bar, on top of everything, `lead` minutes
before it starts ([reminder] in the config, 10 by default), or at the event's own Google
reminder time when it has one. It stays until you dismiss it or the event ends.
Click the card to open the event in Google Calendar; **snooze** hides it for `snooze` minutes;
**join** opens the Meet/Zoom link when there is one. From a key bind: `hyprcal snooze`,
`hyprcal dismiss`, `hyprcal open-event`. Google only exports an event's own reminder in the
iCal feed, not your calendar's default one, so those events use `lead`.

## Files

| | |
|---|---|
| `hyprcal` | CLI + launcher (no GTK import): toggle, `--daemon`, `--status` for waybar, `auth google` |
| `widget.py` | GTK4 layer-shell popover, socket server, sync timer |
| `store.py` | syncs every source into `~/.cache/hyprcal/events.json`, reads it back |
| `gcal.py` | Google Calendar REST API, loopback OAuth with PKCE, read-only scope |
| `ics.py` | iCal reader with recurrence (for secret iCal addresses) |
| `calendly.py` | Calendly API v2: bookings, open slots, booking link |
| `config.py` | `~/.config/hyprcal/config.toml` over the defaults; see `config.example.toml` |
| `style.css` | only hyprcal's own rules; everything else comes from hypr-ui |

Wired in: `~/.local/bin/hyprcal` (symlink), `exec-once = hyprcal --daemon` in
`~/.config/hypr/autostart.conf`, `SUPER ALT C` in `bindings.conf`, `custom/hyprcal` in waybar
(replaces `clock`), and a block in `hypr-ui-solo`.

## Google Calendar

Two ways per calendar, set in `[[google.calendars]]`:

1. **`ics =`, no sign-in.** Google Calendar → settings → the calendar → *Secret address in iCal
   format*. Paste it as `ics = "https://calendar.google.com/calendar/ical/.../basic.ics"`.
   Updates lag a few minutes behind Google.
2. **`id =`, the API.** Live data and Meet links.
   1. Google Cloud Console → a project → enable *Google Calendar API*.
   2. *OAuth consent screen*: External, add yourself as a test user.
   3. *Credentials → Create OAuth client ID → Desktop app* → download the JSON to
      `~/.config/hyprcal/google-client.json`.
   4. `hyprcal auth google`, then `hyprcal google ls` to see calendar ids.

## Calendly

Calendly → Integrations → API & webhooks → *Personal access token*. Store it, e.g.
`pass insert calendly/pat`, then in the config:

```toml
[calendly]
enabled = true
token_cmd = "pass show calendly/pat"
```

Bookings that Calendly also wrote into Google Calendar are shown once.

## Formats

Every date and time in the popover and the bar is a strftime pattern in `[format]`
(`clock`, `date`, `agenda_time`, `month_title`, `day_title`, `waybar`) plus `locale`.
The config is read again each time the popover opens; the bar picks changes up within a second.
