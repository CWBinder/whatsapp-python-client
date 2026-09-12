"""Where things live. Everything hangs off the repo root, overridable by env.

One bridge is one linked WhatsApp account. A *profile* names one such
account: its store directory (session keys, archive, media) and the port its
bridge listens on. The default profile is the original layout (store/, 8080).
Further profiles are declared in config.toml:

    [profiles.claude]
    store = "store-claude"
    port  = 8081

Select one with `whatsapp --profile claude ...` or WHATSAPP_PROFILE=claude.
"""
import os
import tomllib
from pathlib import Path

ROOT = Path(os.environ.get("WHATSAPP_ROOT") or Path(__file__).resolve().parents[2])
BRIDGE_DIR = ROOT / "whatsapp-bridge"
BRIDGE_BIN = BRIDGE_DIR / "whatsapp-bridge"
CONFIG = ROOT / "config.toml"
DEFAULT_PROFILE = "default"
_LABEL_BASE = "local.whatsapp-bridge"

# Set by configure(); module-level so every other module reads paths.X at call time.
PROFILE = DEFAULT_PROFILE
STORE_NAME = "store"
PORT = 8080
STORE = BRIDGE_DIR / STORE_NAME
MESSAGES_DB = STORE / "messages.db"
SESSION_DB = STORE / "whatsapp.db"
BRIDGE_LOG = BRIDGE_DIR / "bridge.log"
API = os.environ.get("WHATSAPP_API", f"http://localhost:{PORT}/api")
LAUNCHD_LABEL = _LABEL_BASE
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _read_config() -> dict:
    if not CONFIG.is_file():
        return {}
    with CONFIG.open("rb") as fh:
        return tomllib.load(fh)


def profiles() -> dict[str, dict]:
    """Every known profile, the default included, as {name: {store, port}}."""
    out = {DEFAULT_PROFILE: {"store": "store", "port": 8080}}
    for name, spec in (_read_config().get("profiles") or {}).items():
        out[name] = {"store": str(spec.get("store", f"store-{name}")), "port": int(spec.get("port", 8080))}
    return out


def configure(profile: str | None = None) -> None:
    """Point every path and the API at one profile. Called once by the CLI."""
    global PROFILE, STORE_NAME, PORT, STORE, MESSAGES_DB, SESSION_DB, BRIDGE_LOG, API, LAUNCHD_LABEL, LAUNCHD_PLIST
    name = profile or os.environ.get("WHATSAPP_PROFILE") or DEFAULT_PROFILE
    known = profiles()
    if name not in known:
        raise SystemExit(f"unknown profile '{name}'; known: {', '.join(known)} (declare it under [profiles.{name}] in {CONFIG})")
    spec = known[name]
    PROFILE = name
    STORE_NAME = spec["store"]
    PORT = spec["port"]
    STORE = BRIDGE_DIR / STORE_NAME
    MESSAGES_DB = STORE / "messages.db"
    SESSION_DB = STORE / "whatsapp.db"
    suffix = "" if name == DEFAULT_PROFILE else f"-{name}"
    BRIDGE_LOG = BRIDGE_DIR / f"bridge{suffix}.log"
    API = os.environ.get("WHATSAPP_API", f"http://localhost:{PORT}/api")
    LAUNCHD_LABEL = _LABEL_BASE + ("" if name == DEFAULT_PROFILE else f".{name}")
    LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


configure()
