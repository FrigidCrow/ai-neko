"""Shared test fixtures."""

import errno
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
    probe_dir = tmp_path_factory.mktemp("sandbox-probe") / "reinit"
    probe_dir.mkdir(parents=True)
    try:
        # A faithful sandbox accepts re-creating an existing directory.
        probe_dir.mkdir(exist_ok=True)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pytest.skip(f"sandbox rejects mkdir(exist_ok=True): {exc}")
    probe = subprocess.run(
        [sys.executable, "-c", "print('fresh-python-ok')"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0 or "fresh-python-ok" not in probe.stdout:
        detail = (probe.stderr or probe.stdout).strip()
        if "sitecustomize" in detail and ("EEXIST" in detail or "FileExistsError" in detail):
            pytest.skip(f"sandbox child initialization rejects existing paths: {detail[:200]}")
        pytest.fail(f"fresh Python process failed unexpectedly: {detail[:200]}")
    return sys.executable
