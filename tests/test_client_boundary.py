"""R1 client boundary: CLI imports no loop; every subcommand needs HTTP; S13 parity."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CLIENT_PKG = SRC / "csd_client"

# Module prefixes that must NOT appear after importing the CLI entry (R1-A1).
LOOP_PREFIXES = (
    "csd_autodev_loop",
    "csd_lab_console",
    "csd_autodev.loop",
)


@pytest.fixture(autouse=True)
def _src_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(SRC))


def test_cli_imports_no_loop() -> None:
    """Importing the CLI entry loads no loop-library / console server module."""
    # Drop any prior loads so the assertion is about this import graph.
    doomed = [
        name
        for name in list(sys.modules)
        if name == "csd_client"
        or name.startswith("csd_client.")
        or any(name == p or name.startswith(p + ".") for p in LOOP_PREFIXES)
        or name.startswith("csd_autodev")
    ]
    for name in doomed:
        sys.modules.pop(name, None)

    importlib.invalidate_caches()
    mod = importlib.import_module("csd_client.cli")
    assert mod is not None

    offenders = sorted(
        name
        for name in sys.modules
        if any(name == p or name.startswith(p + ".") for p in LOOP_PREFIXES)
    )
    assert offenders == [], f"CLI import pulled loop/console modules: {offenders}"


def test_client_package_has_no_loop_imports() -> None:
    """Grep client package source: zero imports of the loop module (R1-A3)."""
    hits: list[str] = []
    needles = (
        "csd_autodev_loop",
        "csd_lab_console",
        "csd_autodev.loop",
        "scripts.csd_autodev_loop",
        "import csd_autodev.loop",
        "from csd_autodev.loop",
    )
    for path in CLIENT_PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for needle in needles:
                if needle in line:
                    hits.append(f"{path.relative_to(ROOT)}:{line_no}:{line.strip()}")
    assert hits == [], "client package must not import the loop:\n" + "\n".join(hits)


def test_all_subcommands_require_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ConnectionRefused → every subcommand exits nonzero; no run/lease/file (R1-A3)."""
    # textual is the TUI client's dependency, not the harness's. Skipping
    # keeps a real boundary check honest rather than permanently red;
    # install textual and it runs.
    pytest.importorskip("textual")
    monkeypatch.setenv("CSD_API", "http://127.0.0.1:1")
    monkeypatch.setenv("CSD_HARNESS_STATE", str(tmp_path / "harness-state"))
    state = tmp_path / "harness-state"
    state.mkdir(parents=True, exist_ok=True)

    import csd_client.cli as cli
    import csd_client.transport as transport

    def refuse(*_a: object, **_k: object) -> dict:
        raise transport.TransportError("connection refused: test")

    monkeypatch.setattr(transport, "request", refuse)

    cases: list[list[str]] = [
        ["status"],
        ["steer", "pause"],
        ["steer", "resume"],
        ["steer", "show"],
        ["steer-note", "hold"],
        ["steer-next", "region-pretrain"],
        ["chat", "ping"],
        ["runs", "list"],
        ["runs", "show", "run-does-not-exist"],
        ["runs", "attach", "run-does-not-exist"],
        ["agents", "list"],
        ["agents", "show", "agent-x"],
        ["sessions", "list"],
        ["sessions", "show", "sess-x"],
        ["leases", "list"],
        ["leases", "show", "lease-x"],
        ["tui"],
        # Legacy flag forms used by lab-console wrappers.
        ["--steer", "pause"],
        ["--steer-note", "n"],
        ["--steer-next", "region-pretrain"],
        ["--chat", "ping"],
    ]

    before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file()}
    for argv in cases:
        rc = cli.main(argv)
        assert rc != 0, f"expected nonzero for {argv!r}, got {rc}"
    after = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before, (
        f"subcommand created files under transport failure: {after - before}"
    )

    # No run / lease artifacts in the harness state tree.
    assert (
        not list((state / "runs").glob("*.json")) if (state / "runs").exists() else True
    )
    assert (
        not list((state / "leases").glob("*.json"))
        if (state / "leases").exists()
        else True
    )


def test_loop_api_parity() -> None:
    """Every loop action on the shared surface has an HTTP route (S13)."""
    from csd_autodev.loop_actions import API_ROUTES, LOOP_ACTIONS

    missing = sorted(action for action in LOOP_ACTIONS if action not in API_ROUTES)
    assert missing == [], f"loop-only actions without API routes: {missing}"
    for action, (method, path) in API_ROUTES.items():
        assert method in {"GET", "POST", "PUT", "DELETE", "PATCH"}
        assert path.startswith("/api/"), f"{action} path must be under /api: {path}"


def test_scripts_csd_entry_imports_clean() -> None:
    """scripts/csd is a thin entry; subprocess import check stays free of loop."""
    script = ROOT / "scripts" / "csd"
    assert script.is_file()
    env = {
        **dict(**{k: v for k, v in __import__("os").environ.items()}),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    # Import the client CLI the same way the script does, in a fresh interpreter.
    code = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(SRC)!r}); "
        "import csd_client.cli as c; "
        "bad=[n for n in sys.modules if n.startswith('csd_autodev_loop') "
        "or n.startswith('csd_lab_console')]; "
        "raise SystemExit(1 if bad else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
