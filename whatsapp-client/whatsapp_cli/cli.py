"""whatsapp: read, search, and send WhatsApp messages through the Go bridge."""
import argparse
import json
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, bridge, config, db, paths
from .names import Resolver, resolve_target

STALE_AFTER = timedelta(hours=1)


# ---- output helpers ----------------------------------------------------------

def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "?"


def _warn_stale():
    age = db.archive_age_seconds()
    if age is None:
        return
    if age > STALE_AFTER.total_seconds():
        last = datetime.fromtimestamp(paths.MESSAGES_DB.stat().st_mtime)
        running = "running" if bridge.pids() else "not running"
        print(f"note: archive last written {_ts(last)}; bridge is {running}", file=sys.stderr)


def _emit_json(obj):
    def default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        return str(o)
    print(json.dumps(obj, default=default, ensure_ascii=False, indent=2))


def _line(res: Resolver, m: db.Message, show_chat: bool) -> str:
    who = res.display(m.sender) if m.sender else "?"
    body = m.content
    if m.media_type:
        tag = f"[{m.media_type}{' ' + m.filename if m.filename else ''}] "
        body = tag + body
    head = f"{_ts(m.timestamp)}"
    if show_chat:
        chat = m.chat_name if m.chat_name and m.chat_name != db.bare(m.chat_jid) else res.display(db.bare(m.chat_jid))
        head += f"  {chat}"
    return f"{head}  {who}: {body}    <{m.id}>"


def _address(res, bare_id: str) -> str:
    """Canonical address: the phone number when known, else the LID."""
    ident = res.identity(bare_id) if bare_id else None
    return (ident.phone or ident.lid or bare_id) if ident else bare_id


def _record(res, m: db.Message) -> dict:
    """The connector contract's message record (shared keys with every channel)."""
    is_group = m.chat_jid.endswith(db.GROUP_SUFFIX)
    chat_label = m.chat_name if (is_group and m.chat_name) else res.display(db.bare(m.chat_jid))
    return {
        "id": m.id,
        "account": paths.PROFILE,
        "when": m.timestamp.astimezone().isoformat(timespec="seconds") if m.timestamp else "",
        "from": "me" if m.is_from_me else _address(res, m.sender),
        "from_name": "me" if m.is_from_me else res.display(m.sender),
        "to": [m.chat_jid] if is_group else ([_address(res, db.bare(m.chat_jid))] if m.is_from_me else ["me"]),
        "subject": "",
        "text": m.content,
        "unread": None,                       # the archive does not track read state
        "thread": m.chat_jid,
        "thread_name": chat_label,
        "group": is_group,
        "attachments": [{"name": m.filename, "type": m.media_type, "size": None}] if m.media_type else [],
    }


def _print_messages(res, msgs, show_chat, as_json):
    if as_json:
        _emit_json([_record(res, m) for m in msgs])
        return
    if not msgs:
        print("no messages")
        return
    for m in msgs:
        print(_line(res, m, show_chat))


# ---- read commands ----------------------------------------------------------

def cmd_chats(a):
    conn = db.connect(); res = Resolver(conn)
    groups = True if a.groups else (False if a.people else None)
    rows = db.chats(conn, query=a.query, limit=a.max * 3 if groups is not True else a.max, groups=groups)
    # merge a person's lid and phone chats into one line
    seen, merged = {}, []
    for c in rows:
        key = c.jid if c.is_group else res.identity(db.bare(c.jid)).key
        if key in seen:
            continue
        seen[key] = c
        merged.append(c)
        if len(merged) >= a.max:
            break
    if a.json:
        _emit_json([{**asdict(c), "name": c.name if c.is_group else res.display(db.bare(c.jid))} for c in merged])
        return
    _warn_stale()
    for c in merged:
        name = c.name if c.is_group else res.display(db.bare(c.jid))
        kind = "group" if c.is_group else "chat"
        last = (("Me: " if c.last_is_from_me else "") + c.last_message.replace("\n", " "))[:70]
        print(f"{_ts(c.last_message_time)}  {kind:5} {name:30.30}  {last}    <{c.jid}>")


def cmd_contacts(a):
    conn = db.connect(); res = Resolver(conn)
    people = res.find_people(a.query)
    if a.json:
        _emit_json([{"name": p.name, "phone": p.phone, "lid": p.lid, "jids": p.jids} for p in people])
        return
    if not people:
        print("no match")
    for p in people:
        print(f"{p.name:30.30}  phone={p.phone or '-':15}  lid={p.lid or '-'}")


def cmd_resolve(a):
    conn = db.connect(); res = Resolver(conn)
    t = resolve_target(res, a.who)
    if a.json:
        address = t.jids[0] if t.kind == "group" else ((t.identity.phone or t.identity.lid) if t.identity else db.bare(t.send_jid))
        _emit_json({"ok": True, "address": address, "name": t.label, "kind": t.kind, "jids": t.jids,
                    "send_jid": t.send_jid, "identity": asdict(t.identity) if t.identity else None,
                    "candidates": [{"address": address, "name": t.label}]})
        return
    print(f"{t.kind}: {t.label}")
    for j in t.jids:
        print(f"  chat  {j}")
    print(f"  send  {t.send_jid}")
    if t.identity:
        i = t.identity
        print(f"  names full={i.full_name or '-'} profile={i.push_name or '-'} chat={i.chat_label or '-'}")


def cmd_read(a):
    conn = db.connect(); res = Resolver(conn)
    t = resolve_target(res, a.who)
    msgs = db.messages(conn, chat_jids=t.jids, after=a.after, before=a.before, limit=a.max, offset=a.page * a.max)
    msgs.reverse()
    if not a.json:
        _warn_stale()
        print(f"# {t.label}  ({', '.join(t.jids)})")
    _print_messages(res, msgs, show_chat=False, as_json=a.json)


def _since(spec: str) -> str:
    """'24h', '3d', '90m', or an ISO date/time -> ISO lower bound."""
    spec = spec.strip()
    units = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    if spec[-1:] in units and spec[:-1].isdigit():
        return (datetime.now() - timedelta(**{units[spec[-1]]: int(spec[:-1])})).strftime("%Y-%m-%d %H:%M:%S")
    return spec


def cmd_recent(a):
    conn = db.connect(); res = Resolver(conn)
    since = _since(a.since)
    msgs = db.messages(conn, after=since, limit=a.max, newest_first=False)
    if a.incoming_only:
        msgs = [m for m in msgs if not m.is_from_me]
    if a.json:
        _emit_json([_record(res, m) for m in msgs])
        return
    _warn_stale()
    if not msgs:
        print(f"nothing since {since}")
        return
    by_chat: dict[str, list] = {}
    for m in msgs:
        by_chat.setdefault(m.chat_jid, []).append(m)
    ordered = sorted(by_chat.items(), key=lambda kv: kv[1][-1].timestamp, reverse=True)
    for jid, ms in ordered:
        label = ms[0].chat_name if jid.endswith(db.GROUP_SUFFIX) else res.display(db.bare(jid))
        kind = "group" if jid.endswith(db.GROUP_SUFFIX) else "chat"
        print(f"## {label}  ({kind}, {len(ms)} new)  <{jid}>")
        for m in ms:
            print("  " + _line(res, m, False))
    print(f"\n{len(msgs)} messages in {len(by_chat)} chats since {since}")


def cmd_search(a):
    conn = db.connect(); res = Resolver(conn)
    chat_jids = resolve_target(res, a.chat).jids if a.chat else None
    senders = resolve_target(res, a.sender).identity.bare_ids if a.sender else None
    if a.sender and not senders:
        raise SystemExit("--from must name a person, not a group")
    after = _since(a.since) if getattr(a, "since", None) else a.after
    msgs = db.messages(conn, chat_jids=chat_jids, senders=senders, query=a.text or None,
                       after=after, before=a.before, limit=a.max, offset=a.page * a.max)
    if not a.json:
        _warn_stale()
    _print_messages(res, msgs, show_chat=True, as_json=a.json)


def _one_message(conn, message_id, chat):
    hits = db.message_by_id(conn, message_id, chat)
    if not hits:
        raise SystemExit(f"no message with id {message_id}")
    if len(hits) > 1:
        raise SystemExit(f"id {message_id} exists in several chats; pass --chat:\n  " +
                         "\n  ".join(h.chat_jid for h in hits))
    return hits[0]


def cmd_context(a):
    conn = db.connect(); res = Resolver(conn)
    m = _one_message(conn, a.message_id, a.chat)
    before, after = db.context(conn, m, a.before, a.after)
    if a.json:
        _emit_json({"before": before, "message": m, "after": after})
        return
    label = m.chat_name if m.chat_jid.endswith(db.GROUP_SUFFIX) else res.display(db.bare(m.chat_jid))
    print(f"# {label}  ({m.chat_jid})")
    for x in before:
        print(_line(res, x, False))
    print(">>> " + _line(res, m, False))
    for x in after:
        print(_line(res, x, False))


def cmd_members(a):
    conn = db.connect(); res = Resolver(conn)
    t = resolve_target(res, a.group)
    if t.kind != "group":
        raise SystemExit(f"'{a.group}' is not a group")
    rows = db.group_senders(conn, t.jids[0])
    if a.json:
        _emit_json([{"id": s, "name": res.display(s), "messages": n, "last": last,
                     "identity": asdict(res.identity(s))} for s, n, last in rows])
        return
    print(f"# {t.label}  {t.jids[0]}  ({len(rows)} people who have written)")
    for s, n, last in rows:
        ident = res.identity(s)
        print(f"{res.display(s):30.30}  {n:5}  last {_ts(last)}  phone={ident.phone or '-'}")


# ---- bridge-backed commands --------------------------------------------------

def cmd_download(a):
    conn = db.connect()
    m = _one_message(conn, a.message_id, a.chat)
    if not m.media_type:
        raise SystemExit("that message has no media")
    ok, msg, path = bridge.download(m.id, m.chat_jid)
    if not ok:
        raise SystemExit(f"download failed: {msg}")
    if a.save:
        dest_dir = Path(a.save).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(path).name
        shutil.copy2(path, dest)
        path = str(dest)
    print(path if not a.json else json.dumps({"path": path, "media_type": m.media_type}))


def _guard_send(t, what):
    if not config.send_allowed():
        print(f"DRY RUN, sending is disabled. Would send to {t.label} <{t.send_jid}>:\n{what}", file=sys.stderr)
        print("enable with `[send] enabled = true` in config.toml or WHATSAPP_ALLOW_SEND=1", file=sys.stderr)
        sys.exit(2)


def cmd_send(a):
    conn = db.connect(); res = Resolver(conn)
    t = resolve_target(res, a.who)
    body = a.body if a.body is not None else sys.stdin.read().rstrip("\n")
    if not body:
        raise SystemExit("empty message")
    _guard_send(t, body)
    ok, msg = bridge.send(t.send_jid if not a.as_phone else t.jids[-1], message=body)
    if getattr(a, "json", False):
        _emit_json({"ok": ok, "id": None, "thread": t.send_jid, "to": [t.label], "account": paths.PROFILE,
                    **({} if ok else {"reason": msg})})
        sys.exit(0 if ok else 1)
    print(("sent to " if ok else "FAILED: ") + f"{t.label} <{t.send_jid}>: {msg}")
    sys.exit(0 if ok else 1)


def cmd_send_file(a):
    conn = db.connect(); res = Resolver(conn)
    t = resolve_target(res, a.who)
    path = Path(a.path).expanduser()
    if not path.is_file():
        raise SystemExit(f"no such file: {path}")
    if a.voice and path.suffix.lower() != ".ogg":
        from . import audio
        path = Path(audio.convert_to_opus_ogg_temp(str(path)))
    _guard_send(t, f"file {path}")
    ok, msg = bridge.send(t.send_jid, media_path=str(path))
    print(("sent to " if ok else "FAILED: ") + f"{t.label}: {msg}")
    sys.exit(0 if ok else 1)


# ---- connector contract --------------------------------------------------------

def cmd_accounts(a):
    """Every profile (linked account) this client knows, with its own number."""
    import sqlite3
    rows = []
    current = paths.PROFILE
    for name in paths.profiles():
        paths.configure(name)
        address, ok = None, False
        if paths.SESSION_DB.is_file():
            try:
                conn = sqlite3.connect(f"file:{paths.SESSION_DB}?mode=ro", uri=True)
                row = conn.execute("select jid from whatsmeow_device limit 1").fetchone(); conn.close()
                if row:
                    address, ok = row[0].split(":")[0].split("@")[0], True
            except Exception:
                pass
        rows.append({"name": name, "address": address, "ok": ok, "default": name == paths.DEFAULT_PROFILE,
                     "running": bool(bridge.pids()), "port": paths.PORT})
    paths.configure(current)
    if a.json:
        _emit_json(rows); return
    for r in rows:
        state = "linked" if r["ok"] else "NOT LINKED"
        print(f"{'ok' if r['ok'] else 'failed'}: {r['name']:9} {r['address'] or '-':16} {state}, bridge {'running' if r['running'] else 'not running'} on {r['port']}")


def cmd_capabilities(a):
    _emit_json({
        "connector": "whatsapp", "version": __version__, "account_flag": "--profile", "account_position": "before",
        "verbs": ["accounts", "search", "read", "send", "resolve", "capabilities"],
        "optional": ["recent", "chats", "context", "members", "contacts", "download", "send-file", "bridge"],
        "features": {"threads": True, "subject": False, "attach": True, "drafts": False, "groups": True, "unread": False},
        "address": "phone digits with country code, a LID, a group JID, or a contact name",
    })


# ---- bridge management -------------------------------------------------------

def cmd_bridge(a):
    if a.bridge_command == "start":
        bridge.start(foreground=True if a.foreground else (False if a.background else None))
    elif a.bridge_command == "stop":
        bridge.stop()
    elif a.bridge_command == "build":
        bridge.build(); print("built")
    elif a.bridge_command == "log":
        print(bridge.log_tail(a.lines))
    elif a.bridge_command == "install":
        bridge.install_launchd()
    elif a.bridge_command == "uninstall":
        bridge.uninstall_launchd()
    elif a.bridge_command == "status":
        s = bridge.status()
        if a.json:
            _emit_json(s); return
        age = s["archive_age_seconds"]
        age_txt = "never" if age is None else (f"{age // 3600}h {age % 3600 // 60}m ago" if age > 3600 else f"{age // 60}m ago")
        print(f"profile    {s['profile']}  (store {s['store']}, port {s['port']})")
        print(f"binary     {'present' if s['binary'] else 'MISSING (run: whatsapp bridge build)'}")
        print(f"process    {'running pid ' + ','.join(map(str, s['pids'])) if s['running'] else 'not running'}")
        print(f"api        {'answering' if s['port_open'] else 'not answering'} at {paths.API}")
        print(f"linked     {'yes' if s['linked'] else 'NO, run `whatsapp bridge start` in a terminal and scan the QR' if s['linked'] is False else 'unknown'}")
        print(f"archive    last written {age_txt}  ({s['archive']})")
        print(f"launchd    {'installed' if s['launchd_installed'] else 'not installed (whatsapp bridge install)'}")


# ---- parser -------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="whatsapp", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--profile", metavar="NAME", default=None,
                   help="which linked account to use (declared under [profiles.NAME] in config.toml; "
                        "also WHATSAPP_PROFILE); default: the original store on port 8080")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, paging=True):
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        if paging:
            sp.add_argument("-n", "--max", type=int, default=20, help="rows to show (default 20)")
            sp.add_argument("--page", type=int, default=0, help="page offset")

    def timerange(sp):
        sp.add_argument("--after", help="ISO date/time lower bound, e.g. 2026-06-01")
        sp.add_argument("--before", help="ISO date/time upper bound")

    s = sub.add_parser("chats", help="list conversations, newest first"); common(s)
    s.add_argument("query", nargs="?", help="filter by name")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--groups", action="store_true"); g.add_argument("--people", action="store_true")
    s.set_defaults(func=cmd_chats)

    s = sub.add_parser("contacts", help="find people by name or number"); common(s, paging=False)
    s.add_argument("query"); s.set_defaults(func=cmd_contacts)

    s = sub.add_parser("resolve", help="show every address a name/number/LID maps to"); common(s, paging=False)
    s.add_argument("who"); s.set_defaults(func=cmd_resolve)

    s = sub.add_parser("read", help="read one conversation (person or group)"); common(s); timerange(s)
    s.add_argument("who", help="name, number, LID, group name, or JID"); s.set_defaults(func=cmd_read)

    s = sub.add_parser("recent", help="what came in lately, grouped by chat"); common(s)
    s.set_defaults(max=200)
    s.add_argument("--since", default="24h", help="window: 24h, 3d, 90m, or an ISO date (default 24h)")
    s.add_argument("--incoming-only", action="store_true", help="hide your own messages")
    s.set_defaults(func=cmd_recent)

    s = sub.add_parser("search", help="find messages by text"); common(s); timerange(s)
    s.add_argument("--since", help="24h, 7d, or an ISO date (alias of --after with a relative form)")
    s.add_argument("text", nargs="?", help="substring to look for (omit to list latest)")
    s.add_argument("--chat", help="restrict to one person or group")
    s.add_argument("--from", dest="sender", help="restrict to messages from one person")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("context", help="messages around one message id"); common(s, paging=False)
    s.add_argument("message_id"); s.add_argument("--chat", help="chat JID if the id is not unique")
    s.add_argument("--before", type=int, default=3); s.add_argument("--after", type=int, default=3)
    s.set_defaults(func=cmd_context)

    s = sub.add_parser("members", help="who has written in a group"); common(s, paging=False)
    s.add_argument("group"); s.set_defaults(func=cmd_members)

    s = sub.add_parser("download", help="fetch a message's media via the bridge"); common(s, paging=False)
    s.add_argument("message_id"); s.add_argument("--chat", help="chat JID if the id is not unique")
    s.add_argument("--save", help="copy the file into this folder"); s.set_defaults(func=cmd_download)

    s = sub.add_parser("accounts", help="every linked account (profile), its number, and bridge state")
    s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_accounts)
    s = sub.add_parser("capabilities", help="what this connector implements (JSON)"); s.set_defaults(func=cmd_capabilities)

    s = sub.add_parser("send", help="send a text message (external effect; disabled by default)")
    s.add_argument("who"); s.add_argument("--body", help="text; omitted means read stdin")
    s.add_argument("--as-phone", action="store_true", help="force the phone-form address instead of LID")
    s.add_argument("--json", action="store_true", help="machine-readable result (connector contract)")
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("send-file", help="send a file or voice note (external effect; disabled by default)")
    s.add_argument("who"); s.add_argument("path")
    s.add_argument("--voice", action="store_true", help="convert audio to a WhatsApp voice note first")
    s.set_defaults(func=cmd_send_file)

    s = sub.add_parser("bridge", help="control the Go bridge process")
    bs = s.add_subparsers(dest="bridge_command", required=True)
    b = bs.add_parser("status", help="process, port, link, archive age"); b.add_argument("--json", action="store_true")
    b = bs.add_parser("start", help="start the bridge: foreground if a QR scan is needed, else background")
    g = b.add_mutually_exclusive_group()
    g.add_argument("--foreground", action="store_true", help="force foreground")
    g.add_argument("--background", action="store_true", help="force background")
    bs.add_parser("stop"); bs.add_parser("build", help="go build the bridge")
    b = bs.add_parser("log", help="tail the bridge log"); b.add_argument("-n", "--lines", type=int, default=40)
    bs.add_parser("install", help="install a launchd agent so the bridge runs at login and restarts")
    bs.add_parser("uninstall", help="remove the launchd agent")
    s.set_defaults(func=cmd_bridge)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    paths.configure(args.profile)
    try:
        args.func(args)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
