"""Optional config.toml at the repo root. Only one setting so far."""
import os
import tomllib
from . import paths


def load() -> dict:
    if not paths.CONFIG.is_file():
        return {}
    with paths.CONFIG.open("rb") as fh:
        return tomllib.load(fh)


def send_allowed() -> bool:
    """Sending is off unless config.toml says [send] enabled = true, or the env var is set."""
    if os.environ.get("WHATSAPP_ALLOW_SEND") == "1":
        return True
    return bool(load().get("send", {}).get("enabled", False))
