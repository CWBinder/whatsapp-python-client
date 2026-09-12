"""Turn ids into people and people into ids.

A person can be addressed two ways, phone JID and LID. The lid map in the
session database ties them together; the contacts table holds names under
either form; the bridge's chats table holds a name that is often just the id.
"""
from dataclasses import dataclass, field
from typing import Optional

from . import db


@dataclass
class Identity:
    lid: Optional[str] = None
    phone: Optional[str] = None
    full_name: str = ""
    push_name: str = ""
    chat_label: str = ""
    raw: str = ""

    @property
    def key(self) -> str:
        return self.phone or self.lid or self.raw

    @property
    def name(self) -> str:
        return self.full_name or self.push_name or self.chat_label or self.key

    @property
    def jids(self) -> list[str]:
        out = []
        if self.lid:
            out.append(self.lid + db.LID_SUFFIX)
        if self.phone:
            out.append(self.phone + db.PHONE_SUFFIX)
        if not out and self.raw:
            out.append(self.raw)
        return out

    @property
    def bare_ids(self) -> list[str]:
        return [x for x in (self.lid, self.phone, self.raw) if x]

    @property
    def send_jid(self) -> str:
        """LID first: phone-form delivery silently fails for migrated contacts."""
        return (self.lid + db.LID_SUFFIX) if self.lid else (self.phone + db.PHONE_SUFFIX if self.phone else self.raw)


class Resolver:
    def __init__(self, conn):
        self.conn = conn
        self.session = db.has_session_tables(conn)
        self._cache: dict[str, Identity] = {}
        self._own = db.own_ids(conn)

    def is_me(self, bare_id: str) -> bool:
        return bare_id in self._own

    def identity(self, bare_id: str) -> Identity:
        bare_id = db.bare(bare_id)
        if bare_id in self._cache:
            return self._cache[bare_id]
        ident = Identity(raw=bare_id)
        if self.session:
            row = self.conn.execute(
                "select lid, pn from w.whatsmeow_lid_map where lid = ? or pn = ?", (bare_id, bare_id)).fetchone()
            if row:
                ident.lid, ident.phone = row["lid"], row["pn"]
        if not ident.lid and not ident.phone:
            # unknown to the map: keep the raw id, but still try contacts under both suffixes
            pass
        if self.session:
            candidates = [j for j in (
                ident.phone + db.PHONE_SUFFIX if ident.phone else None,
                ident.lid + db.LID_SUFFIX if ident.lid else None,
                bare_id + db.PHONE_SUFFIX, bare_id + db.LID_SUFFIX) if j]
            rows = self.conn.execute(
                f"select their_jid, first_name, full_name, push_name from w.whatsmeow_contacts "
                f"where their_jid in ({','.join('?' * len(candidates))})", candidates).fetchall()
            for r in rows:
                if r["full_name"] and not ident.full_name:
                    ident.full_name = r["full_name"]
                elif r["first_name"] and not ident.full_name:
                    ident.full_name = r["first_name"]
                if r["push_name"] and not ident.push_name:
                    ident.push_name = r["push_name"]
        labels = db.chat_names(self.conn, ident.jids or [bare_id + db.LID_SUFFIX, bare_id + db.PHONE_SUFFIX])
        for jid, label in labels.items():
            if label and label != db.bare(jid) and not ident.chat_label:
                ident.chat_label = label
        for k in ident.bare_ids:
            self._cache[k] = ident
        return ident

    def display(self, bare_id: str) -> str:
        if self.is_me(bare_id):
            return "Me"
        return self.identity(bare_id).name

    def find_people(self, query: str) -> list[Identity]:
        """Match a name, profile name, or number; return one Identity per person."""
        like = f"%{query}%"
        hits: list[str] = []
        if self.session:
            rows = self.conn.execute(
                "select their_jid from w.whatsmeow_contacts where full_name like ? or first_name like ? "
                "or push_name like ? or their_jid like ?", (like, like, like, like))
            hits += [db.bare(r["their_jid"]) for r in rows]
        rows = self.conn.execute(
            "select jid from chats where jid not like '%@g.us' and (name like ? or jid like ?)", (like, like))
        hits += [db.bare(r["jid"]) for r in rows]
        seen, out = set(), []
        for h in hits:
            ident = self.identity(h)
            if ident.key in seen:
                continue
            seen.add(ident.key)
            out.append(ident)
        return out

    def find_groups(self, query: str) -> list[db.Chat]:
        exact = [c for c in db.chats(self.conn, query=query, limit=50, groups=True) if c.name == query or c.jid == query]
        return exact or db.chats(self.conn, query=query, limit=50, groups=True)


@dataclass
class Target:
    kind: str                       # "group" or "person" or "jid"
    label: str
    jids: list[str] = field(default_factory=list)
    identity: Optional[Identity] = None

    @property
    def send_jid(self) -> str:
        if self.identity:
            return self.identity.send_jid
        return self.jids[0]


def resolve_target(res: Resolver, who: str) -> Target:
    """WHO is a JID, a bare number or LID, a group name, or a person's name."""
    who = who.strip()
    if "@" in who:
        if who.endswith(db.GROUP_SUFFIX):
            label = db.chat_names(res.conn, [who]).get(who) or who
            return Target("group", label, [who])
        ident = res.identity(who)
        existing = [j for j in ident.jids if j in db.chat_names(res.conn, ident.jids)] or [who]
        return Target("person", ident.name, existing, ident)
    if who.isdigit():
        ident = res.identity(who)
        existing = [j for j in ident.jids if j in db.chat_names(res.conn, ident.jids)] or ident.jids
        return Target("person", ident.name, existing, ident)
    groups = res.find_groups(who)
    if len(groups) == 1:
        return Target("group", groups[0].name, [groups[0].jid])
    people = res.find_people(who)
    if len(groups) > 1 and not people:
        names = "\n  ".join(f"{g.name}  {g.jid}" for g in groups[:10])
        raise SystemExit(f"'{who}' matches several groups; use the address:\n  {names}")
    if len(people) == 1 and not groups:
        ident = people[0]
        existing = [j for j in ident.jids if j in db.chat_names(res.conn, ident.jids)] or ident.jids
        return Target("person", ident.name, existing, ident)
    if not people and not groups:
        raise SystemExit(f"nothing matches '{who}'")
    lines = [f"{g.name}  {g.jid}  (group)" for g in groups[:10]]
    lines += [f"{p.name}  {p.key}" for p in people[:10]]
    raise SystemExit(f"'{who}' is ambiguous; be more specific or use an address:\n  " + "\n  ".join(lines))
