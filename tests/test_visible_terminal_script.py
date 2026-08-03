from pathlib import Path
from subprocess import run


SCRIPT = Path(__file__).parents[1] / "scripts" / "open-visible-terminal.sh"


def test_visible_terminal_script_has_a_parseable_help_contract():
    syntax = run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    help_output = run([str(SCRIPT), "--help"], capture_output=True, text=True)

    assert syntax.returncode == 0, syntax.stderr
    assert help_output.returncode == 0, help_output.stderr
    assert "--cwd DIR" in help_output.stdout
    assert "--wait" in help_output.stdout
    assert "--caffeinate" in help_output.stdout
    assert "COMMAND" in help_output.stdout
