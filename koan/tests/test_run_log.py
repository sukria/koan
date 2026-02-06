"""Tests for run.sh colored log() function."""

import subprocess


_LOG_FUNCTION = """
log() {
  local cat="$1"; shift
  local color
  case "$cat" in
    koan)    color="${_C_CYAN}" ;;
    error)   color="${_C_BOLD}${_C_RED}" ;;
    init)    color="${_C_BLUE}" ;;
    health)  color="${_C_YELLOW}" ;;
    git)     color="${_C_MAGENTA}" ;;
    mission) color="${_C_GREEN}" ;;
    quota)   color="${_C_BOLD}${_C_YELLOW}" ;;
    pause)   color="${_C_DIM}${_C_BLUE}" ;;
    *)       color="${_C_WHITE}" ;;
  esac
  echo -e "${color}[${cat}]${_C_RESET} $*"
}
"""

_COLORS_TTY = """
_C_RESET='\\033[0m'
_C_BOLD='\\033[1m'
_C_DIM='\\033[2m'
_C_RED='\\033[31m'
_C_GREEN='\\033[32m'
_C_YELLOW='\\033[33m'
_C_BLUE='\\033[34m'
_C_MAGENTA='\\033[35m'
_C_CYAN='\\033[36m'
_C_WHITE='\\033[37m'
"""

_COLORS_PLAIN = """
_C_RESET='' _C_BOLD='' _C_DIM=''
_C_RED='' _C_GREEN='' _C_YELLOW=''
_C_BLUE='' _C_MAGENTA='' _C_CYAN='' _C_WHITE=''
"""


def _run_log(category, message, tty=False):
    """Run the log() function from run.sh and capture output."""
    colors = _COLORS_TTY if tty else _COLORS_PLAIN
    script = f"{colors}\n{_LOG_FUNCTION}\nlog {category} {message}"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


class TestLogColors:
    """Test that the log function produces correct colored output."""

    def test_koan_no_color(self):
        """Without TTY, log outputs plain text with brackets."""
        output = _run_log("koan", "Starting.")
        assert output == "[koan] Starting."

    def test_error_no_color(self):
        output = _run_log("error", "Something failed.")
        assert output == "[error] Something failed."

    def test_init_no_color(self):
        output = _run_log("init", "Booting up.")
        assert output == "[init] Booting up."

    def test_health_no_color(self):
        output = _run_log("health", "Memory cleanup.")
        assert output == "[health] Memory cleanup."

    def test_git_no_color(self):
        output = _run_log("git", "Running sync.")
        assert output == "[git] Running sync."

    def test_mission_no_color(self):
        output = _run_log("mission", "Picker result: x")
        assert output == "[mission] Picker result: x"

    def test_quota_no_color(self):
        output = _run_log("quota", "Budget low.")
        assert output == "[quota] Budget low."

    def test_pause_no_color(self):
        output = _run_log("pause", "Contemplative mode.")
        assert output == "[pause] Contemplative mode."

    def test_unknown_category_no_color(self):
        output = _run_log("other", "Something.")
        assert output == "[other] Something."


class TestLogColorsWithTTY:
    """Test that TTY mode produces ANSI escape codes."""

    def test_koan_has_cyan(self):
        output = _run_log("koan", "test", tty=True)
        assert "\033[36m" in output  # cyan
        assert "[koan]" in output
        assert "\033[0m" in output  # reset

    def test_error_has_bold_red(self):
        output = _run_log("error", "fail", tty=True)
        assert "\033[1m" in output  # bold
        assert "\033[31m" in output  # red

    def test_init_has_blue(self):
        output = _run_log("init", "start", tty=True)
        assert "\033[34m" in output  # blue

    def test_health_has_yellow(self):
        output = _run_log("health", "check", tty=True)
        assert "\033[33m" in output  # yellow

    def test_git_has_magenta(self):
        output = _run_log("git", "sync", tty=True)
        assert "\033[35m" in output  # magenta

    def test_mission_has_green(self):
        output = _run_log("mission", "go", tty=True)
        assert "\033[32m" in output  # green

    def test_quota_has_bold_yellow(self):
        output = _run_log("quota", "low", tty=True)
        assert "\033[1m" in output  # bold
        assert "\033[33m" in output  # yellow

    def test_pause_has_dim_blue(self):
        output = _run_log("pause", "zzz", tty=True)
        assert "\033[2m" in output  # dim
        assert "\033[34m" in output  # blue

    def test_message_preserved_after_reset(self):
        """The actual message text follows the reset code."""
        output = _run_log("koan", "hello world", tty=True)
        assert "hello world" in output


class TestLogMultipleArgs:
    """Test that log handles multiple arguments correctly."""

    def test_multiple_words(self):
        output = _run_log("koan", '"hello beautiful world"')
        assert output == "[koan] hello beautiful world"

    def test_with_special_chars(self):
        output = _run_log("mission", '"Run 1/10"')
        assert output == "[mission] Run 1/10"
