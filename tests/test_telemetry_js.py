"""Runs the network-telemetry frontend specs under node, from pytest.

Same arrangement as the other `test_frontend_*.py` wrappers: no build step
and no JS test runner, so each spec is a plain node script. Skips rather than
fails when node is unavailable.

The collector spec carries the property the whole feature rests on: the
window.fetch wrapper is transparent (same promise, same response, errors
propagate, body untouched, uninstall restores native fetch).
See .devnotes/frontend-telemetry/.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPECS = [
    "telemetry_classify_spec.mjs",
    "telemetry_buffer_spec.mjs",
    "telemetry_collector_spec.mjs",
    "telemetry_flush_spec.mjs",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", SPECS)
def test_telemetry_spec(name):
    spec = Path(__file__).parent / "js" / name
    assert spec.exists(), f"missing spec: {spec}"
    result = subprocess.run(
        [shutil.which("node"), str(spec)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"{name} failed:\n{result.stdout}\n{result.stderr}"
    assert " 0 failed" in result.stdout
