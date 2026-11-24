"""Composition root and startup checks."""

from opsagent.bootstrap.auth import AuthKit, build_auth
from opsagent.bootstrap.container import Container, build_container
from opsagent.bootstrap.doctor import Check, has_failure, run_doctor
from opsagent.bootstrap.tui import StartupError, run_tui

__all__ = [
    "AuthKit",
    "Check",
    "Container",
    "StartupError",
    "build_auth",
    "build_container",
    "has_failure",
    "run_doctor",
    "run_tui",
]
