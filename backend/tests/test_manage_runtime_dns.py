"""manage.sh invariants across the container→process migration.

Historical note: the aardvark single-DNS trap (``--dns`` on a podman network
becomes the ONLY upstream; one UDP/53 blip = EAI_AGAIN everywhere) is why
runtime containers never pinned DNS. Process mode retires containers; the
guards below pin the new invariants instead.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MANAGE = Path(__file__).resolve().parents[2] / "manage.sh"


def _func_body(name: str) -> str:
    text = MANAGE.read_text(encoding="utf-8")
    start = text.index(f"{name}()")
    nxt = text.find("\nrun_", start + 1)
    return text[start : nxt if nxt != -1 else None]


def test_run_start_does_not_pass_dns_to_containers() -> None:
    body = _func_body("run_start")
    assert "_podman_dns_args" not in body
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert "--dns" not in code


def test_rebuild_is_process_mode_no_podman_build() -> None:
    """Container era is over: rebuild installs venv deps + builds the SPA,
    and must never shell out to podman build again."""
    body = _func_body("run_rebuild")
    assert "pip install" in body
    assert "npm run build" in body
    assert "podman" not in body


def test_legacy_watchdog_script_is_neutralized_template() -> None:
    """The shipped watchdog script doubles as a neutralization stub in process
    mode (install-launchd overwrites any live copy with exit 0)."""
    text = (Path(__file__).resolve().parents[2] / "scripts" / "launchd-start.sh").read_text(
        encoding="utf-8"
    )
    assert "VSLP-NEUTRALIZED" in text
    assert "exec ./manage.sh start" not in text
    assert "containers_up" not in text


def _run_stop_with_fake_launchctl(lingering_prints: int) -> subprocess.CompletedProcess[str]:
    """Execute the real run_stop against a launchctl stub whose `print`
    keeps finding the agent for N calls after bootout (launchd unloads
    asynchronously)."""
    script = f"""
set -euo pipefail
AGENT_DOMAIN=gui/501/test
STOP_MARKER="${{TMPDIR:-/tmp}}/vsplit-stop-marker-test"
rm -f "$STOP_MARKER"
booted_out=0; lingering={lingering_prints}
log_step() {{ :; }}; log_ok() {{ echo "OK $*"; }}; log_error() {{ echo "ERR $*"; }}
sleep() {{ :; }}
launchctl() {{
    case "$1" in
        bootout) booted_out=1 ;;
        print)
            [[ $booted_out == 0 ]] && return 0
            (( lingering > 0 )) && {{ lingering=$((lingering - 1)); return 0; }}
            return 113 ;;
    esac
}}
{_func_body("run_stop")}
run_stop
echo "left=$lingering"
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


def test_run_stop_waits_until_agent_unloaded() -> None:
    """Regression: restart = stop + start. If stop returns while launchctl
    still lists the agent, start kickstarts a dying agent (exit 37) and
    set -e aborts silently, leaving production down after rebuild."""
    result = _run_stop_with_fake_launchctl(lingering_prints=3)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK Stopped." in result.stdout
    assert "left=0" in result.stdout


def test_run_stop_fails_loudly_if_agent_never_unloads() -> None:
    result = _run_stop_with_fake_launchctl(lingering_prints=999)
    assert result.returncode != 0
    assert "ERR" in result.stdout


def test_run_start_kickstart_failure_is_reported() -> None:
    body = _func_body("run_start")
    assert 'kickstart -k "$AGENT_DOMAIN" \\\n            || { log_error' in body
