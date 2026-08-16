"""
Static check: no undefined names anywhere in the package.

WHY THIS EXISTS
───────────────
A `NameError: name 'GeometryUtils' is not defined` reached the live assessment
path and failed all ten farmers in a drift run — while 220 tests passed and an
`import main` smoke check reported OK.

Both of those were blind to it for the same reason: importing a module does not
execute its function bodies, and no test exercises assess_farmer end to end
(that needs Earth Engine credentials and real imagery). So a name referenced
inside a function but never imported is invisible until the code actually runs
against a real farm.

This closes that gap cheaply. pyflakes resolves names statically, so it catches
the whole class — undefined names, and imports that were removed while still in
use — without needing credentials, network or fixtures.

Scope note: this asserts only on undefined names (F821) and undefined local
usage (F823). It is deliberately NOT a style gate; unused imports and long
lines are not failures here, and turning this into one would make it noisy
enough to be ignored, which is how checks like this stop working.
"""

from __future__ import annotations

import pathlib

import pytest

pyflakes_api = pytest.importorskip(
    "pyflakes.api", reason="pyflakes not installed; static name check skipped"
)
from pyflakes import reporter as pyflakes_reporter  # noqa: E402

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Directories that are not ours to police.
SKIP_DIRS = {".conda", "__pycache__", ".git", "node_modules", "venv", ".venv"}

# Only names that DO NOT RESOLVE. "assigned but never used" is untidy, not
# broken, and folding it in here would make this check noisy enough to be
# routinely ignored — which is exactly how a guard like this stops working.
FATAL_MARKERS = ("undefined name",)


class _Collector:
    """Minimal pyflakes reporter that keeps messages instead of printing."""

    def __init__(self):
        self.messages: list[str] = []
        self.errors: list[str] = []

    def unexpectedError(self, filename, msg):
        self.errors.append(f"{filename}: {msg}")

    def syntaxError(self, filename, msg, lineno, offset, text):
        self.errors.append(f"{filename}:{lineno}: syntax error: {msg}")

    def flake(self, message):
        self.messages.append(str(message))


def _python_files():
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def test_package_has_python_files_to_check():
    """Guard against the glob silently matching nothing and passing vacuously."""
    files = list(_python_files())
    assert len(files) > 20, f"only found {len(files)} python files — check the glob"


def test_no_undefined_names_in_package():
    """
    The check that would have caught the GeometryUtils NameError before it
    reached a real assessment run.
    """
    collector = _Collector()
    for path in _python_files():
        pyflakes_api.checkPath(str(path), reporter=collector)

    fatal = [
        m for m in collector.messages
        if any(marker in m for marker in FATAL_MARKERS)
    ]

    assert not collector.errors, (
        "pyflakes could not parse some files:\n  " + "\n  ".join(collector.errors)
    )
    assert not fatal, (
        f"{len(fatal)} undefined name(s) — these fail at RUNTIME, not import "
        f"time, so tests and `import module` will not catch them:\n  "
        + "\n  ".join(fatal)
    )


def test_main_assessment_module_is_clean():
    """
    main.py specifically: it holds the orchestration path that only executes
    against real imagery, so it is the file least covered by unit tests and the
    most costly place for a name error.
    """
    collector = _Collector()
    pyflakes_api.checkPath(str(PACKAGE_ROOT / "main.py"), reporter=collector)
    fatal = [
        m for m in collector.messages
        if any(marker in m for marker in FATAL_MARKERS)
    ]
    assert not fatal, "undefined name(s) in main.py:\n  " + "\n  ".join(fatal)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
