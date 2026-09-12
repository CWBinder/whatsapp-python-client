"""The Go bridge: process control and its two HTTP endpoints."""
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import db, paths


# ---- HTTP -------------------------------------------------------------------

def _post(endpoint: str, payload: dict, timeout=60) -> dict:
    req = urllib.request.Request(f"{paths.API}/{endpoint}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"success": False, "message": f"HTTP {e.code}: {body.strip()}"}
    except urllib.error.URLError as e:
        raise SystemExit(f"bridge not reachable at {paths.API} ({e.reason}); run `whatsapp bridge start`")


def send(recipient: str, message: str = "", media_path: str = "") -> tuple[bool, str]:
    payload = {"recipient": recipient}
    if message:
        payload["message"] = message
    if media_path:
        payload["media_path"] = str(Path(media_path).resolve())
    r = _post("send", payload)
    return bool(r.get("success")), r.get("message", "")


def download(message_id: str, chat_jid: str) -> tuple[bool, str, Optional[str]]:
    r = _post("download", {"message_id": message_id, "chat_jid": chat_jid}, timeout=300)
    return bool(r.get("success")), r.get("message", ""), r.get("path")


def port_open(timeout=1.5) -> bool:
    try:
        urllib.request.urlopen(f"{paths.API}/send", timeout=timeout)
    except urllib.error.HTTPError:
        return True            # 405 Method Not Allowed means the server answered
    except urllib.error.URLError:
        return False
    return True


# ---- process ----------------------------------------------------------------

def _args() -> list[str]:
    """Command-line arguments that pin the binary to this profile's store and port."""
    return ["-store", paths.STORE_NAME, "-port", str(paths.PORT)]


def pids() -> list[int]:
    """Bridge processes belonging to this profile. An instance started without
    arguments (the pre-profile way) counts as the default profile."""
    pattern = rf"(^|/){paths.BRIDGE_BIN.name}( |$)"      # the binary by name, however it was invoked
    candidates = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()
    candidates = [p for p in candidates if p.isdigit() and int(p) != os.getpid()]
    if not candidates:
        return []
    # ps, not pgrep -a: on macOS -a means "include ancestors" and prints no arguments
    out = subprocess.run(["ps", "-o", "pid=,command=", "-p", ",".join(candidates)], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if not pid.isdigit():
            continue
        argv = cmd.split()
        store = argv[argv.index("-store") + 1] if "-store" in argv and argv.index("-store") + 1 < len(argv) else "store"
        if store == paths.STORE_NAME:
            found.append(int(pid))
    return found


def linked() -> Optional[bool]:
    """True if the session store holds a device identity, None if no store."""
    if not paths.SESSION_DB.is_file():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{paths.SESSION_DB}?mode=ro", uri=True)
        n = conn.execute("select count(*) from whatsmeow_device").fetchone()[0]
        conn.close()
        return n > 0
    except Exception:
        return None


def build() -> None:
    if not shutil.which("go"):
        raise SystemExit("go is not installed; install it (brew install go) and retry")
    subprocess.run(["go", "build", "-o", str(paths.BRIDGE_BIN)], cwd=paths.BRIDGE_DIR, check=True)


def start(foreground: Optional[bool] = None) -> None:
    """Foreground when a QR scan is needed (no saved session), background otherwise.
    Pass True/False to override."""
    if not paths.BRIDGE_BIN.is_file():
        print("bridge binary missing; building it first")
        build()
    if pids():
        print("bridge already running")
        return
    if foreground is None:
        foreground = not linked()
        if foreground:
            print("no saved session: starting in the foreground so you can scan the QR code.")
            print("scan it from WhatsApp > Settings > Linked Devices > Link a Device, wait a minute, then Ctrl-C.")
            print("after that, `whatsapp bridge start` runs in the background, or `whatsapp bridge install` keeps it running.")
            print()
    if foreground:
        # QR codes render properly only on a terminal; ctrl-c stops it
        os.chdir(paths.BRIDGE_DIR)
        os.execv(str(paths.BRIDGE_BIN), [str(paths.BRIDGE_BIN), *_args()])
    log = open(paths.BRIDGE_LOG, "ab")
    subprocess.Popen([str(paths.BRIDGE_BIN), *_args()], cwd=paths.BRIDGE_DIR, stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(90):                      # connecting can take well over ten seconds
        time.sleep(0.5)
        if port_open() or not pids():
            break
    tail = log_tail(40)
    if "QR" in tail or "scan" in tail.lower():
        print("bridge started but is NOT linked and needs a QR scan. stop it and run `whatsapp bridge start` in a terminal.")
    elif port_open():
        print(f"bridge running (pid {pids()[0] if pids() else '?'}), log at {paths.BRIDGE_LOG}")
    elif pids():
        print(f"bridge process is up (pid {pids()[0]}) but still connecting; check `whatsapp bridge status` in a moment")
    else:
        print(f"bridge exited; last log lines:\n{tail}")


def stop() -> None:
    ps = pids()
    if not ps:
        print("bridge not running")
        return
    for p in ps:
        os.kill(p, signal.SIGTERM)
    time.sleep(1)
    print(f"stopped pid {', '.join(map(str, ps))}")


def log_tail(n=40) -> str:
    if not paths.BRIDGE_LOG.is_file():
        return ""
    lines = paths.BRIDGE_LOG.read_text(errors="replace").splitlines()
    return "\n".join(lines[-n:])


def status() -> dict:
    age = db.archive_age_seconds()
    return {
        "profile": paths.PROFILE,
        "store": str(paths.STORE),
        "port": paths.PORT,
        "binary": paths.BRIDGE_BIN.is_file(),
        "running": bool(pids()),
        "pids": pids(),
        "port_open": port_open(),
        "linked": linked(),
        "archive": str(paths.MESSAGES_DB),
        "archive_age_seconds": None if age is None else int(age),
        "launchd_installed": paths.LAUNCHD_PLIST.is_file(),
    }


# ---- launchd ----------------------------------------------------------------

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>WorkingDirectory</key><string>{cwd}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def install_launchd() -> None:
    if not paths.BRIDGE_BIN.is_file():
        build()
    paths.LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(paths.BRIDGE_BIN), *_args()]
    args = "".join(f"<string>{a}</string>" for a in argv)
    paths.LAUNCHD_PLIST.write_text(PLIST.format(label=paths.LAUNCHD_LABEL, args=args,
                                                cwd=paths.BRIDGE_DIR, log=paths.BRIDGE_LOG))
    subprocess.run(["launchctl", "unload", str(paths.LAUNCHD_PLIST)], capture_output=True)
    subprocess.run(["launchctl", "load", str(paths.LAUNCHD_PLIST)], check=True)
    print(f"installed and loaded {paths.LAUNCHD_PLIST}; the '{paths.PROFILE}' bridge now starts at login and restarts if it exits")


def uninstall_launchd() -> None:
    if paths.LAUNCHD_PLIST.is_file():
        subprocess.run(["launchctl", "unload", str(paths.LAUNCHD_PLIST)], capture_output=True)
        paths.LAUNCHD_PLIST.unlink()
        print("launchd agent removed")
    else:
        print("no launchd agent installed")
