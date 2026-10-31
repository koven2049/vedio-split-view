"""Watchdog invariants for process-mode self-healing (tickets T40/T41).

2026-10-31 S1 outage: the web LaunchAgent was booted out around 11:10 (app log
ends cleanly at 11:09:53 — no crash, no traceback) and the site served 502 for
hours. launchd KeepAlive only revives a *loaded* agent, and the podman→process
migration had removed the old 30s container watchdog, so nothing reloaded it.

Guard restored here: LaunchAgent ``com.koven.vsplit.watchdog`` runs
``scripts/watchdog.sh`` every 30s and re-bootstraps the web agent unless the
deliberate-stop marker (``logs/.service-stopped``, written by ``manage.sh
stop``, cleared by ``start``) says hands-off.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANAGE = REPO / "manage.sh"
WATCHDOG_SH = REPO / "scripts" / "watchdog.sh"
WATCHDOG_PLIST = REPO / "scripts" / "com.koven.vsplit.watchdog.plist"


def _func_body(name: str) -> str:
    text = MANAGE.read_text(encoding="utf-8")
    start = text.index(f"{name}()")
    nxt = text.find("\nrun_", start + 1)
    return text[start : nxt if nxt != -1 else None]


# ── scripts/watchdog.sh behaviour (real script, stubbed launchctl/HOME) ──────


def _run_watchdog(
    tmp: Path, *, loaded: bool, marker: bool, plist: bool, bootstrap_ok: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run the real watchdog.sh anchored at a tmp ROOT with a fake launchctl."""
    (tmp / "scripts").mkdir()
    (tmp / "logs").mkdir()
    shutil.copy2(WATCHDOG_SH, tmp / "scripts" / "watchdog.sh")

    home = tmp / "home"
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    if plist:
        (agents / "com.koven.vsplit.web.plist").write_text("<plist/>", encoding="utf-8")
    if marker:
        (tmp / "logs" / ".service-stopped").write_text("", encoding="utf-8")

    stub = tmp / "bin"
    stub.mkdir()
    (stub / "launchctl").write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            case "$1" in
                print)
                    [[ {int(loaded)} == 1 ]] && exit 0
                    exit 113
                    ;;
                bootstrap)
                    echo "launchctl $*" >> {tmp}/calls.log
                    [[ {int(bootstrap_ok)} == 1 ]] && exit 0
                    exit 5
                    ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    (stub / "launchctl").chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{stub}:{env['PATH']}"
    env["HOME"] = str(home)
    return subprocess.run(
        ["bash", str(tmp / "scripts" / "watchdog.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _calls(tmp: Path) -> str:
    p = tmp / "calls.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _watchdog_log(tmp: Path) -> str:
    p = tmp / "logs" / "watchdog.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_watchdog_bootstraps_unloaded_agent(tmp_path: Path) -> None:
    """Core regression: agent booted out + no deliberate stop → re-bootstrap."""
    result = _run_watchdog(tmp_path, loaded=False, marker=False, plist=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "bootstrap" in _calls(tmp_path)
    assert "bootstrapped" in _watchdog_log(tmp_path)


def test_watchdog_respects_deliberate_stop_marker(tmp_path: Path) -> None:
    """`manage.sh stop` must win: marker present → no launchctl call at all."""
    result = _run_watchdog(tmp_path, loaded=False, marker=True, plist=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(tmp_path) == ""
    assert _watchdog_log(tmp_path) == ""


def test_watchdog_quiet_when_agent_loaded(tmp_path: Path) -> None:
    """Loaded agent is KeepAlive's business — no bootstrap, no log spam."""
    result = _run_watchdog(tmp_path, loaded=True, marker=False, plist=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(tmp_path) == ""
    assert _watchdog_log(tmp_path) == ""


def test_watchdog_noop_before_install_launchd(tmp_path: Path) -> None:
    """Fresh clone without the installed plist — nothing to guard yet."""
    result = _run_watchdog(tmp_path, loaded=False, marker=False, plist=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(tmp_path) == ""


def test_watchdog_logs_bootstrap_failure_without_dying(tmp_path: Path) -> None:
    """A failing bootstrap must be visible in watchdog.log, exit 0 regardless."""
    result = _run_watchdog(
        tmp_path, loaded=False, marker=False, plist=True, bootstrap_ok=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "bootstrap FAILED" in _watchdog_log(tmp_path)


def test_watchdog_plist_has_no_keepalive(tmp_path: Path) -> None:
    """KeepAlive on a <1s script would respawn-flood; cadence is StartInterval."""
    text = WATCHDOG_PLIST.read_text(encoding="utf-8")
    assert "<key>KeepAlive</key>" not in text
    assert "<key>StartInterval</key>" in text
    assert "<integer>30</integer>" in text


# ── manage.sh invariants (stop flags, start un-flags, install wires both) ────


def _run_stop_with_fake_launchctl(tmp_path: Path, initially_loaded: bool) -> None:
    marker = tmp_path / "marker"
    script = f"""
set -euo pipefail
AGENT_DOMAIN=gui/501/test
STOP_MARKER={marker}
booted_out=0; initially={int(initially_loaded)}
log_step() {{ :; }}; log_ok() {{ :; }}; log_error() {{ :; }}
sleep() {{ :; }}
launchctl() {{
    case "$1" in
        bootout) booted_out=1 ;;
        print)
            [[ $initially == 0 ]] && return 113
            [[ $booted_out == 0 ]] && return 0
            return 113
            ;;
    esac
}}
{_func_body("run_stop")}
run_stop
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists(), "stop must leave the deliberate-stop marker behind"


def test_run_stop_writes_marker_when_agent_was_loaded(tmp_path: Path) -> None:
    _run_stop_with_fake_launchctl(tmp_path, initially_loaded=True)


def test_run_stop_writes_marker_when_already_down(tmp_path: Path) -> None:
    """Stopping an already-down service must still arm the marker — otherwise
    the watchdog would revive it 30s later and undo the operator's intent."""
    _run_stop_with_fake_launchctl(tmp_path, initially_loaded=False)


def test_run_start_clears_marker_before_touching_launchd() -> None:
    body = _func_body("run_start")
    clear = body.index('rm -f "$STOP_MARKER"')
    assert clear < body.index("launchctl print"), "marker must be cleared first"
    assert "rm -f" in body


def test_install_launchd_wires_watchdog_agent() -> None:
    body = _func_body("run_install_launchd")
    assert "WATCHDOG_PLIST_SRC" in body
    assert "WATCHDOG_PLIST_INSTALL" in body
    assert 'bootstrap "gui/$(id -u)" "$WATCHDOG_PLIST_INSTALL"' in body
    # install implies desired-up state: stale markers must not survive it
    assert 'rm -f "$STOP_MARKER"' in body


def test_status_reports_launchd_and_watchdog_state() -> None:
    body = _func_body("run_status")
    assert "AGENT_DOMAIN" in body
    assert "WATCHDOG_DOMAIN" in body
    assert "STOP_MARKER" in body
