import contextlib
import datetime
import io
import json
import unittest
from argparse import Namespace
from unittest.mock import patch

from whatsapp_cli import cli, db


def message(mid="native:id", chat="123:4@s.whatsapp.net"):
    return db.Message(
        id=mid, chat_jid=chat, sender="123", content="Budget update",
        timestamp=datetime.datetime.now().astimezone(), is_from_me=False,
        media_type="", filename="", chat_name="Alice",
    )


class ResolverStub:
    def display(self, value):
        return "Alice"

    def identity(self, value):
        return type("Identity", (), {"phone": value, "lid": None})()


class ContractTests(unittest.TestCase):
    def test_message_id_is_self_contained_and_reversible(self):
        msg = message()
        identifier = cli._record(ResolverStub(), msg)["id"]
        chat, native = cli._split_message_id(identifier)
        self.assertEqual((chat, native), (msg.chat_jid, msg.id))

    def test_read_message_uses_embedded_chat(self):
        msg = message()
        identifier = cli._message_id(msg)
        connection = object()
        args = Namespace(message_id=identifier, thread_id=None, who=None, after=None,
                         before=None, max=20, page=0, json=True)
        with patch.object(cli.db, "connect", return_value=connection), \
             patch.object(cli, "Resolver", return_value=ResolverStub()), \
             patch.object(cli.db, "message_by_id", return_value=[msg]) as lookup, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            cli.cmd_read(args)
        self.assertEqual(json.loads(output.getvalue())[0]["id"], identifier)
        lookup.assert_called_once_with(connection, msg.id, msg.chat_jid)

    def test_thread_read_is_complete_by_default(self):
        msg = message()
        connection = object()
        args = Namespace(message_id=None, thread_id=msg.chat_jid, who=None, after=None,
                         before=None, max=None, page=0, json=True)
        with patch.object(cli.db, "connect", return_value=connection), \
             patch.object(cli, "Resolver", return_value=ResolverStub()), \
             patch.object(cli.db, "messages", return_value=[msg]) as lookup, \
             contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_read(args)
        self.assertIsNone(lookup.call_args.kwargs["limit"])

    def test_threads_returns_standard_record(self):
        chat = db.Chat(jid="group@g.us", name="Project", last_message_time=datetime.datetime.now().astimezone(),
                       last_message="Latest", last_sender="123", last_is_from_me=False)
        args = Namespace(query="Project", sender=None, since=None, max=20, page=0, json=True)
        with patch.object(cli.db, "connect", return_value=object()), \
             patch.object(cli, "Resolver", return_value=ResolverStub()), \
             patch.object(cli.db, "chats", return_value=[chat]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            cli.cmd_threads(args)
        row = json.loads(output.getvalue())[0]
        self.assertEqual(row["id"], "group@g.us")
        self.assertEqual(row["type"], "group")
        self.assertIn("message_count", row)

    def test_parser_exposes_new_contract(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["read", "--message", "x:y"]).message_id, "x:y")
        self.assertEqual(parser.parse_args(["read", "--thread", "chat@g.us"]).thread_id, "chat@g.us")
        self.assertEqual(parser.parse_args(["search", "x", "--thread", "chat@g.us"]).thread_id, "chat@g.us")
        self.assertEqual(parser.parse_args(["threads", "project"]).command, "threads")


if __name__ == "__main__":
    unittest.main()
