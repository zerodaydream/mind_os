"""Composition root and startup checks."""

from mindos.bootstrap.auth import AuthKit, build_auth
from mindos.bootstrap.container import Container, build_container
from mindos.bootstrap.doctor import Check, has_failure, run_doctor
from mindos.bootstrap.tui import StartupError, run_tui

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
