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


def _helper_body(name: str) -> str:
    """Extract a top-level helper (ends at the first column-0 closing brace)."""
    text = MANAGE.read_text(encoding="utf-8")
    start = text.index(f"{name}()")
    return text[start : text.index("\n}\n", start) + 2]


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
    """Execute the real _agent_target + run_stop against a launchctl stub
    whose `print` keeps finding the agent for N calls after bootout (launchd
    unloads asynchronously)."""
    script = f"""
set -euo pipefail
AGENT_LABEL=com.koven.vsplit.web.test
AGENT_BOOT_DOMAIN=user/501
booted_out=0; lingering={lingering_prints}; booted_target=""
log_step() {{ :; }}; log_ok() {{ echo "OK $*"; }}; log_error() {{ echo "ERR $*"; }}
sleep() {{ :; }}
launchctl() {{
    case "$1" in
        bootout) booted_out=1; booted_target="$2" ;;
        print)
            [[ $booted_out == 0 ]] && return 0
            (( lingering > 0 )) && {{ lingering=$((lingering - 1)); return 0; }}
            return 113 ;;
    esac
}}
{_helper_body("_agent_target")}
{_func_body("run_stop")}
run_stop
echo "left=$lingering"
echo "booted=$booted_target"
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


def test_run_stop_boots_out_background_domain_instance() -> None:
    """The loaded agent lives in user/<uid> (background domain) since the
    2026-10-31 outage fix; stop must boot out exactly that instance."""
    result = _run_stop_with_fake_launchctl(lingering_prints=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "booted=user/501/com.koven.vsplit.web.test" in result.stdout


def test_run_stop_fails_loudly_if_agent_never_unloads() -> None:
    result = _run_stop_with_fake_launchctl(lingering_prints=999)
    assert result.returncode != 0
    assert "ERR" in result.stdout


def test_run_start_kickstart_failure_is_reported() -> None:
    body = _func_body("run_start")
    assert 'launchctl kickstart -k "$target" \\\n            || { log_error "kickstart failed' in body


def test_run_start_bootstraps_into_background_domain() -> None:
    """2026-10-31 outage: with another user holding the console there is no
    gui/<uid> session, and `bootstrap gui/<uid>` from SSH/LaunchDaemon fails
    with error 125 — start must target the user background domain."""
    body = _func_body("run_start")
    assert 'launchctl bootstrap "$AGENT_BOOT_DOMAIN"' in body
    assert 'bootstrap "gui/' not in body


def test_run_start_gates_start_on_both_listeners() -> None:
    """T54 acceptance: restart must prove BOTH listeners (:5180 SPA+API and
    :8080 API compat) answer /api/health. The old gate probed only the web
    port, so a start with a dead compat listener still reported OK."""
    body = _func_body("run_start")
    assert "_ports_healthy && break" in body
    assert "if _ports_healthy; then" in body
    helper = _helper_body("_ports_healthy")
    assert '"$(get_frontend_port)" "$(get_app_port)"' in helper
    assert "/api/health" in helper


def _ports_healthy_with_fake_curl(fail_url: str) -> subprocess.CompletedProcess[str]:
    """Run the real _ports_healthy against a curl stub that fails for one URL."""
    script = f"""
set -euo pipefail
get_frontend_port() {{ echo 5180; }}
get_app_port() {{ echo 8080; }}
probed=""
curl() {{
    probed="$probed ${{@: -1}}"
    [[ "${{@: -1}}" == "{fail_url}" ]] && return 7
    return 0
}}
{_helper_body("_ports_healthy")}
_ports_healthy && echo healthy=yes || echo healthy=no
echo "probed:$probed"
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


def test_ports_healthy_passes_when_both_listeners_answer() -> None:
    result = _ports_healthy_with_fake_curl(fail_url="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "healthy=yes" in result.stdout
    assert "http://localhost:5180/api/health" in result.stdout
    assert "http://localhost:8080/api/health" in result.stdout


def test_ports_healthy_fails_when_compat_listener_is_dead() -> None:
    result = _ports_healthy_with_fake_curl(fail_url="http://localhost:8080/api/health")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "healthy=no" in result.stdout


def test_agent_target_looks_in_background_domain_first() -> None:
    """Lookups prefer user/<uid> and only fall back to gui/<uid> for a
    leftover instance from an older install, so stop/restart never misses
    an already-loaded agent."""
    body = _helper_body("_agent_target")
    assert 'for d in "$AGENT_BOOT_DOMAIN" "gui/$(id -u)"' in body


def test_agent_plist_loads_into_background_session() -> None:
    """LimitLoadToSessionType=Background is what makes the plist loadable
    into user/<uid>; a plain Aqua agent is rejected there (error 125)."""
    plist = (
        Path(__file__).resolve().parents[2] / "scripts" / "com.koven.vsplit.web.plist"
    ).read_text(encoding="utf-8")
    assert "<key>LimitLoadToSessionType</key>" in plist
    assert "<string>Background</string>" in plist
