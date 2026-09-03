"""Runtime containers must not pin aardvark to a single public DNS.

On a dns-enabled Podman network, ``--dns`` is not written to resolv.conf;
aardvark takes it as the only upstream. One UDP/53 timeout then becomes
``EAI_AGAIN`` for every outbound host. ``podman_dns`` stays for image builds.
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
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--dns" not in code


def test_rebuild_still_passes_dns_to_build() -> None:
    body = _func_body("run_rebuild")
    assert "--dns" in body
    assert "PODMAN_DNS" in body
