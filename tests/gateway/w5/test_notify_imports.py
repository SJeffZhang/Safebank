"""Fresh-process imports catch cycles hidden by pytest import order."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["main", "state", "notify", "utterance.service"])
def test_fresh_gateway_import(module):
    result = subprocess.run(
        [sys.executable, "-c", "import services.gateway.app." + module],
        env={**os.environ, "PYTHONPATH": "packages/contracts/src:packages/bankcore/src"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
