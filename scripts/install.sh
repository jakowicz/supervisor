#!/usr/bin/env bash
# Install the Supervisor CLI into user-local directories. No administrator
# privileges, global Python packages, or project files are required.
set -euo pipefail

repository_url="${SUPERVISOR_REPOSITORY_URL:-https://github.com/jakowicz/supervisor.git}"
install_dir="${SUPERVISOR_INSTALL_DIR:-$HOME/.local/share/supervisor}"
bin_dir="${SUPERVISOR_BIN_DIR:-$HOME/.local/bin}"
requested_python="${SUPERVISOR_PYTHON:-}"

fail() { printf 'Supervisor installer: %s\n' "$*" >&2; exit 1; }

choose_python() {
  local candidate version major minor
  local candidates=(python3.14 python3.13 python3.12 python3.11 python3.10 python3 python)
  if [[ -n "$requested_python" ]]; then
    candidates=("$requested_python")
  fi
  for candidate in "${candidates[@]}"; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || continue
    major="${version%%.*}"
    minor="${version#*.}"
    if [[ "$major" -gt 3 || ( "$major" -eq 3 && "$minor" -ge 10 ) ]]; then
      command -v "$candidate"
      return 0
    fi
  done
  fail "Python 3.10+ was not found on PATH. Set SUPERVISOR_PYTHON to a compatible interpreter."
}

command -v git >/dev/null 2>&1 || fail "git is required."
python_bin="$(choose_python)"

if [[ -e "$install_dir" ]]; then
  [[ -d "$install_dir/.git" ]] || fail "Install directory exists but is not a Supervisor Git checkout: $install_dir"
  printf 'Updating Supervisor in %s\n' "$install_dir"
  git -C "$install_dir" pull --ff-only origin main
else
  printf 'Cloning Supervisor into %s\n' "$install_dir"
  mkdir -p "$(dirname "$install_dir")"
  git clone "$repository_url" "$install_dir"
fi

printf 'Creating virtual environment with %s\n' "$python_bin"
"$python_bin" -m venv "$install_dir/.venv"
printf 'Installing Supervisor CLI\n'
"$install_dir/.venv/bin/python" -m pip install -e "$install_dir[dev]"

mkdir -p "$bin_dir"
for command_name in supervisor supervisor-run supervisor-reports supervisor-dashboard supervisor-observability-import; do
  target="$bin_dir/$command_name"
  if [[ -e "$target" && ! -L "$target" ]]; then
    fail "Refusing to replace non-symlink command: $target"
  fi
  ln -sfn "$install_dir/.venv/bin/$command_name" "$target"
done

printf '\nSupervisor installed.\n'
printf 'Next: supervisor init ~/Dev/my-project\n'
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) printf 'Add this to your shell profile, then open a new terminal:\nexport PATH="%s:$PATH"\n' "$bin_dir" ;;
esac
