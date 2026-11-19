import subprocess
import sys

from hyprspot import __version__


def test_version():
    out = subprocess.run([sys.executable, "-m", "hyprspot", "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == f"hyprspot {__version__}"
