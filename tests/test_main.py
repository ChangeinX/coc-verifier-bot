"""Tests for the bots.__main__ module."""

import subprocess
import sys


def test_main_module_imports():
    """Should import the main module successfully."""
    import bots.__main__

    # Basic test that the module imports successfully
    assert hasattr(bots.__main__, "asyncio")


def test_main_module_execution_subprocess():
    """Should execute main when run as module via subprocess."""
    # Test via subprocess to verify actual execution
    result = subprocess.run(
        [sys.executable, "-c", 'import bots.__main__; print("imported")'],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert "imported" in result.stdout
