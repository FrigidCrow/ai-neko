"""Shared test fixtures."""

import subprocess
import sys

import pytest


@pytest.fixture(scope="session")
def sandbox_compatible(tmp_path_factory):
    """Skip tests needing real FS/process semantics under broken sandbox shims.

    Some IDE sandboxes broker filesystem calls and reject a second
    ``mkdir(exist_ok=True)`` on an existing path with EEXIST, and inject a
    sitecustomize into child Python processes that fails the same way. These
    tests carry the process-crash durability guarantees and still run in CI;
    locally we skip them explicitly instead of misreading environment noise as
    product regressions.
    """
    try:
        probe_dir = tmp_path_factory.mktemp("sandbox-probe") / "reinit"
        probe_dir.mkdir(parents=True)
        # A faithful sandbox accepts re-creating an existing directory.
        probe_dir.mkdir(exist_ok=True)
        probe = subprocess.run(
            [sys.executable, "-c", "print('fresh-python-ok')"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode != 0 or "fresh-python-ok" not in probe.stdout:
            raise RuntimeError((probe.stderr or probe.stdout).strip()[:200])
    except Exception as exc:
        pytest.skip(f"sandbox without faithful mkdir/subprocess semantics: {exc}")
    return sys.executable
