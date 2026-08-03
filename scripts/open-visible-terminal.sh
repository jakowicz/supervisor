#!/usr/bin/env bash
# Run a command in a visible macOS terminal tab.  This is deliberately small
# and dependency-free so Supervisor can use it for long-running repair work.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: open-visible-terminal.sh [--cwd DIR] [--wait] -- COMMAND [ARG ...]

Open an iTerm2 tab (or a Terminal.app window when iTerm2 is unavailable), run
COMMAND visibly, and optionally wait for it to finish.  With --wait, the
script returns COMMAND's exit status after the visible terminal reports it.
EOF
}

working_directory="$PWD"
wait_for_completion=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cwd)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      working_directory="$2"
      shift 2
      ;;
    --wait)
      wait_for_completion=true
      shift
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

[[ $# -gt 0 ]] || { usage >&2; exit 2; }
[[ -d "$working_directory" ]] || { echo "Directory does not exist: $working_directory" >&2; exit 2; }
[[ "$(uname)" == "Darwin" ]] || { echo "A visible macOS terminal is only available on macOS." >&2; exit 2; }

quoted_command=""
for argument in "$@"; do
  printf -v escaped_argument '%q' "$argument"
  quoted_command+="${quoted_command:+ }${escaped_argument}"
done
printf -v escaped_directory '%q' "$working_directory"

status_file=""
if [[ "$wait_for_completion" == true ]]; then
  status_file="$(mktemp "${TMPDIR:-/tmp}/supervisor-visible-terminal.XXXXXX")"
  rm -f "$status_file"
  printf -v escaped_status_file '%q' "$status_file"
  terminal_command="cd $escaped_directory; $quoted_command; supervisor_visible_exit=\$?; printf '%s' \"\$supervisor_visible_exit\" > $escaped_status_file; exit \$supervisor_visible_exit"
else
  terminal_command="cd $escaped_directory; exec $quoted_command"
fi

# Pass AppleScript source through stdin so arbitrary command text never has to
# be interpolated into an AppleScript string literal.
if osascript - "$terminal_command" <<'APPLESCRIPT' >/dev/null 2>&1
on run argv
  set terminalCommand to item 1 of argv
  tell application id "com.googlecode.iterm2"
    activate
    create window with default profile
    tell current session of current window
      write text terminalCommand
    end tell
  end tell
end run
APPLESCRIPT
then
  :
else
  osascript - "$terminal_command" <<'APPLESCRIPT'
on run argv
  tell application "Terminal"
    activate
    do script (item 1 of argv)
  end tell
end run
APPLESCRIPT
fi

[[ "$wait_for_completion" == true ]] || exit 0
while [[ ! -f "$status_file" ]]; do
  sleep 1
done
exit_code="$(<"$status_file")"
rm -f "$status_file"
exit "$exit_code"
