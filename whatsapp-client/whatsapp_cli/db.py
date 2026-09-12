"""Read-only access to the two SQLite files the bridge keeps.

messages.db is the bridge's own archive (chats, messages).
whatsapp.db belongs to whatsmeow; we read two of its tables (contacts, lid map)
and never write to either file.
"""
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from . import paths

GROUP_SUFFIX = "@g.us"
LID_SUFFIX = "@lid"
PHONE_SUFFIX = "@s.whatsapp.net"


@dataclass
class Message:
    id: str
    chat_jid: str
    sender: str          # bare id, no suffix
    content: str
    timestamp: datetime
    is_from_me: bool
    media_type: str
    filename: str
    chat_name: str = ""


@dataclass
class Chat:
    jid: str
    name: str
    last_message_time: Optional[datetime]
    last_message: str = ""
    last_sender: str = ""
    last_is_from_me: bool = False

    @property
    def is_group(self) -> bool:
        return self.jid.endswith(GROUP_SUFFIX)


def connect() -> sqlite3.Connection:
    if not paths.MESSAGES_DB.is_file():
        raise SystemExit(f"no message archive at {paths.MESSAGES_DB}; has the bridge ever run?")
    conn = sqlite3.connect(f"file:{paths.MESSAGES_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if paths.SESSION_DB.is_file():
        conn.execute("attach database ? as w", (f"file:{paths.SESSION_DB}?mode=ro",))
        conn.execute("pragma query_only = 1")
    return conn


def has_session_tables(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("select 1 from w.whatsmeow_lid_map limit 1")
        return True
    except sqlite3.Error:
        return False


def parse_ts(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def archive_age_seconds() -> Optional[float]:
    """Seconds since the bridge last wrote the archive, or None if it does not exist."""
    if not paths.MESSAGES_DB.is_file():
        return None
    return time.time() - paths.MESSAGES_DB.stat().st_mtime


def bare(jid: str) -> str:
    return jid.split("@", 1)[0]


def _row_to_message(row) -> Message:
    return Message(
        id=row["id"], chat_jid=row["chat_jid"], sender=row["sender"] or "",
        content=row["content"] or "", timestamp=parse_ts(row["timestamp"]),
        is_from_me=bool(row["is_from_me"]), media_type=row["media_type"] or "",
        filename=row["filename"] or "", chat_name=row["chat_name"] if "chat_name" in row.keys() else "",
    )


MESSAGE_COLS = "m.id, m.chat_jid, m.sender, m.content, m.timestamp, m.is_from_me, m.media_type, m.filename, c.name as chat_name"


def messages(conn, chat_jids=None, senders=None, query=None, after=None, before=None,
             limit=20, offset=0, newest_first=True) -> list[Message]:
    where, args = [], []
    if chat_jids:
        where.append(f"m.chat_jid in ({','.join('?' * len(chat_jids))})")
        args += list(chat_jids)
    if senders:
        where.append(f"m.sender in ({','.join('?' * len(senders))})")
        args += list(senders)
    if query:
        where.append("m.content like ?")
        args.append(f"%{query}%")
    if after:
        where.append("m.timestamp >= ?")
        args.append(after)
    if before:
        where.append("m.timestamp <= ?")
        args.append(before)
    sql = f"select {MESSAGE_COLS} from messages m join chats c on c.jid = m.chat_jid"
    if where:
        sql += " where " + " and ".join(where)
    sql += f" order by m.timestamp {'desc' if newest_first else 'asc'} limit ? offset ?"
    args += [limit, offset]
    return [_row_to_message(r) for r in conn.execute(sql, args)]


def message_by_id(conn, message_id: str, chat_jid: Optional[str] = None) -> list[Message]:
    sql = f"select {MESSAGE_COLS} from messages m join chats c on c.jid = m.chat_jid where m.id = ?"
    args = [message_id]
    if chat_jid:
        sql += " and m.chat_jid = ?"
        args.append(chat_jid)
    return [_row_to_message(r) for r in conn.execute(sql, args)]


def context(conn, msg: Message, before=3, after=3) -> tuple[list[Message], list[Message]]:
    ts = msg.timestamp.isoformat(sep=" ") if msg.timestamp else ""
    earlier = messages(conn, chat_jids=[msg.chat_jid], before=ts, limit=before + 1)
    earlier = [m for m in earlier if m.id != msg.id][:before]
    later = messages(conn, chat_jids=[msg.chat_jid], after=ts, limit=after + 1, newest_first=False)
    later = [m for m in later if m.id != msg.id][:after]
    return list(reversed(earlier)), later


def chats(conn, query=None, limit=20, offset=0, groups: Optional[bool] = None) -> list[Chat]:
    where, args = [], []
    if query:
        where.append("(c.name like ? or c.jid like ?)")
        args += [f"%{query}%", f"%{query}%"]
    if groups is True:
        where.append("c.jid like '%@g.us'")
    elif groups is False:
        where.append("c.jid not like '%@g.us'")
    sql = """
        select c.jid, c.name, c.last_message_time, m.content, m.sender, m.is_from_me
        from chats c
        left join messages m on m.chat_jid = c.jid and m.timestamp = (
            select max(timestamp) from messages where chat_jid = c.jid)
    """
    if where:
        sql += " where " + " and ".join(where)
    sql += " group by c.jid order by c.last_message_time desc limit ? offset ?"
    args += [limit, offset]
    return [Chat(jid=r["jid"], name=r["name"] or "", last_message_time=parse_ts(r["last_message_time"]),
                 last_message=r["content"] or "", last_sender=r["sender"] or "",
                 last_is_from_me=bool(r["is_from_me"])) for r in conn.execute(sql, args)]


def chat_names(conn, jids) -> dict[str, str]:
    if not jids:
        return {}
    rows = conn.execute(f"select jid, name from chats where jid in ({','.join('?' * len(jids))})", list(jids))
    return {r["jid"]: r["name"] or "" for r in rows}


def group_senders(conn, group_jid: str) -> list[tuple[str, int, Optional[datetime]]]:
    rows = conn.execute(
        "select sender, count(*) as n, max(timestamp) as last from messages "
        "where chat_jid = ? and sender != '' and sender != ? group by sender order by n desc",
        (group_jid, bare(group_jid)))
    return [(r["sender"], r["n"], parse_ts(r["last"])) for r in rows]


def own_ids(conn) -> set[str]:
    rows = conn.execute("select distinct sender from messages where is_from_me = 1 and sender != ''")
    return {r["sender"] for r in rows}
