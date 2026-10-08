"""Test-session setup: the same DLL registration the service does at import
(config._register_conda_dlls), before any test module imports NumPy-backed code."""
import config  # noqa: F401
