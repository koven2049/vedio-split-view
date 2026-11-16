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


# ---------------------------------------------------------------------------
# Hosting contract (2026-11-13): production = system LaunchDaemon
# com.koven.vsplit.web (UserName=admin, KeepAlive); Test checkouts = pidfile +
# nohup. Real helper bodies are executed against stubs; the 2026-10 LaunchAgent
# tests (user/<uid> domain, kickstart, /api/health) were retired with it.
# ---------------------------------------------------------------------------

LABEL = "com.koven.vsplit.web"


def _run_bash(script: str) -> subprocess.CompletedProcess[str]:
    prelude = f"""
set -uo pipefail
LAUNCHD_LABEL={LABEL}
log_step() {{ echo "STEP $*"; }}; log_ok() {{ echo "OK $*"; }}
log_info() {{ echo "INFO $*"; }}; log_warn() {{ echo "WARN $*"; }}
log_error() {{ echo "ERR $*"; }}
sleep() {{ :; }}
get_frontend_port() {{ echo 5180; }}
get_app_port() {{ echo 8080; }}
"""
    return subprocess.run(
        ["bash", "-c", prelude + script], capture_output=True, text=True, check=False
    )


def _ports_healthy_with_fake_curl(fail_url: str) -> subprocess.CompletedProcess[str]:
    """Run the real _ports_healthy against a curl stub that fails for one URL."""
    return _run_bash(f"""
probed=""
curl() {{
    probed="$probed ${{@: -1}}"
    [[ "${{@: -1}}" == "{fail_url}" ]] && return 7
    return 0
}}
{_helper_body("_ports_healthy")}
_ports_healthy && echo healthy=yes || echo healthy=no
echo "probed:$probed"
""")


def test_ports_healthy_passes_when_both_listeners_answer() -> None:
    result = _ports_healthy_with_fake_curl(fail_url="")
    assert "healthy=yes" in result.stdout, result.stdout + result.stderr
    assert "http://localhost:5180/health" in result.stdout
    assert "http://localhost:8080/health" in result.stdout


def test_ports_healthy_fails_when_compat_listener_is_dead() -> None:
    """T54: one port up without the other is a broken start, not a healthy one."""
    result = _ports_healthy_with_fake_curl(fail_url="http://localhost:8080/health")
    assert "healthy=no" in result.stdout, result.stdout + result.stderr


def test_start_and_restart_gate_on_wait_health() -> None:
    """Reporting success without both listeners answering is how a dead
    service got reported as "restarted"; wait_health must give up (rc 1)."""
    result = _run_bash(f"""
_ports_healthy() {{ return 1; }}
HEALTH_WAIT=3
{_helper_body("wait_health")}
wait_health && echo rc=0 || echo rc=1
""")
    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert "wait_health" in _func_body("run_start")
    assert "wait_health" in _func_body("run_restart")


def test_daemon_managed_here_is_directory_scoped() -> None:
    """Same machine, same label: a Test checkout must not mistake the
    ~/Services production instance for its own managed daemon."""
    result = _run_bash(f"""
system_managed() {{ return 0; }}
confirmed_server_pids() {{ :; }}
{_helper_body("daemon_managed_here")}
daemon_managed_here && echo managed=yes || echo managed=no
""")
    assert "managed=no" in result.stdout, result.stdout + result.stderr


def _guard(svc_ctl_rc: int, managed: bool = True) -> subprocess.CompletedProcess[str]:
    return _run_bash(f"""
calls=""
daemon_managed_here() {{ return {0 if managed else 1}; }}
svc_ctl() {{ calls="$calls svc_ctl:$*"; return {svc_ctl_rc}; }}
confirmed_server_pids() {{ echo 4242; }}
stop_pid() {{ calls="$calls stop_pid:$1"; return 0; }}
{_helper_body("guard_prod_daemon_stop")}
guard_prod_daemon_stop; echo "rc=$?"
echo "calls:$calls"
""")


def test_guard_ignores_unmanaged_instances() -> None:
    result = _guard(svc_ctl_rc=0, managed=False)
    assert "rc=1" in result.stdout
    assert "svc_ctl" not in result.stdout


def test_guard_really_stops_managed_daemon_via_svc_ctl() -> None:
    """A soft kill is pointless under KeepAlive; stop = svc-ctl disable."""
    result = _guard(svc_ctl_rc=0)
    assert "rc=0" in result.stdout, result.stdout + result.stderr
    assert f"svc_ctl:disable system/{LABEL}" in result.stdout


def test_guard_reports_missing_helper_with_sudo_hint() -> None:
    result = _guard(svc_ctl_rc=1)
    assert "rc=2" in result.stdout, result.stdout + result.stderr
    assert "sudo" in result.stdout
    assert "stop_pid" not in result.stdout


def test_run_stop_refuses_fake_success_when_helper_missing() -> None:
    """Managed + no svc-ctl: stop must fail, never soft-kill and claim OK."""
    result = _run_bash(f"""
guard_prod_daemon_stop() {{ return 2; }}
# Stubs that would let the unmanaged soft-stop path "succeed" if reached.
PIDFILE=/nonexistent/multi_listen.pid; SERVICE=vedio-split-view
port_held_elsewhere() {{ return 1; }}
confirmed_server_pids() {{ echo 4242; }}
service_pid() {{ :; }}
stop_pid() {{ echo "SOFT-KILL $1"; }}
{_func_body("run_stop")}
( run_stop ); echo "rc=$?"
""")
    assert "rc=0" not in result.stdout, result.stdout + result.stderr
    assert "SOFT-KILL" not in result.stdout
    assert "OK" not in result.stdout


def test_run_start_is_noop_when_managed_here() -> None:
    result = _run_bash(f"""
daemon_managed_here() {{ return 0; }}
nohup() {{ echo "NOHUP $*"; }}
{_func_body("run_start")}
run_start; echo "rc=$?"
""")
    assert "rc=0" in result.stdout, result.stdout + result.stderr
    assert "NOHUP" not in result.stdout


def test_daemon_plist_template_matches_hosting_contract() -> None:
    plist = (Path(__file__).resolve().parents[2] / "deploy" / f"{LABEL}.plist").read_text(
        encoding="utf-8"
    )
    assert f"<string>{LABEL}</string>" in plist
    assert "<key>UserName</key>\n    <string>admin</string>" in plist
    assert "<key>KeepAlive</key>\n    <true/>" in plist
    assert "scripts/multi_listen.py</string>" in plist
    # ffmpeg/ffprobe/deno live in Homebrew; launchd's default PATH lacks it.
    assert "/opt/homebrew/bin" in plist
    # LaunchAgent-only key; a system daemon must not carry it.
    assert "LimitLoadToSessionType" not in plist
