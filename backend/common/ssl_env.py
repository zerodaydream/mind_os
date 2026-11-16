"""Fix broken SSL_CERT_* from the shell before httpx/ssl opens CA paths."""

from __future__ import annotations

import os
from pathlib import Path


def fix_ssl_cert_env() -> None:
    """Fix broken SSL_CERT_* from the shell before httpx/ssl opens CA paths."""
    cert_file = os.environ.get("SSL_CERT_FILE")
    if cert_file and not Path(cert_file).is_file():
        try:
            import certifi

            os.environ["SSL_CERT_FILE"] = certifi.where()
        except ImportError:
            os.environ.pop("SSL_CERT_FILE", None)
    cert_dir = os.environ.get("SSL_CERT_DIR")
    if cert_dir and not Path(cert_dir).is_dir():
        os.environ.pop("SSL_CERT_DIR", None)
