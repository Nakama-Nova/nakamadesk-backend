"""
Regression tests for issue #23: SECRET_KEY must not silently default to a
hardcoded value outside dev/test. These run `app.core.security` in a fresh
subprocess (rather than importing it in-process) because the guard is a
module-level, import-time check — reusing the already-imported module in
this test session wouldn't exercise it again.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_security_in_subprocess(env_overrides: dict, unset: tuple = ()):
    env = os.environ.copy()
    for key in unset:
        env.pop(key, None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app.core.security"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_default_secret_key_rejected_outside_dev_test():
    result = _import_security_in_subprocess(
        {"ENV": "production"}, unset=("SECRET_KEY",)
    )
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_empty_secret_key_rejected_outside_dev_test():
    result = _import_security_in_subprocess({"ENV": "production", "SECRET_KEY": ""})
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_real_secret_key_allowed_outside_dev_test():
    result = _import_security_in_subprocess(
        {"ENV": "production", "SECRET_KEY": "a-real-production-secret"}
    )
    assert result.returncode == 0, result.stderr


def test_default_secret_key_allowed_in_dev():
    result = _import_security_in_subprocess({"ENV": "dev"}, unset=("SECRET_KEY",))
    assert result.returncode == 0, result.stderr


def test_default_secret_key_allowed_in_test_env():
    result = _import_security_in_subprocess({"ENV": "test"}, unset=("SECRET_KEY",))
    assert result.returncode == 0, result.stderr
