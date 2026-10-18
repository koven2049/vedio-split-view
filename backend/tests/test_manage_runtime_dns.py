"""manage.sh invariants across the container→process migration.

Historical note: the aardvark single-DNS trap (``--dns`` on a podman network
becomes the ONLY upstream; one UDP/53 blip = EAI_AGAIN everywhere) is why
runtime containers never pinned DNS. Process mode retires containers; the
guards below pin the new invariants instead.
"""

from __future__ import annotations

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
