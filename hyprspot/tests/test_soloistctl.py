from datetime import date

from hyprspot.soloistctl import days_left, parse_version, warn_level

V = "soloist 1.3.10.83 build 1795024918 (20261118) (g19412df6ea) (linux/x86_64)"


def test_parse_version():
    assert parse_version(V) == ("1.3.10.83", date(2026, 11, 18))


def test_parse_version_garbage():
    assert parse_version("") is None
    assert parse_version("soloist 9 (abc)") is None


def test_days_left():
    assert days_left(date(2026, 11, 18), date(2027, 2, 10)) == 6
    assert days_left(date(2026, 11, 18), date(2026, 11, 18)) == 90


def test_warn_levels():
    assert warn_level(15) == "ok"
    assert warn_level(14) == "soon"
    assert warn_level(1) == "soon"
    assert warn_level(0) == "expired"
    assert warn_level(-3) == "expired"
