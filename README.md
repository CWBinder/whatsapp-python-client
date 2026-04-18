# whatsapp-python-client

A Python client for WhatsApp, backed by a Go bridge using [whatsmeow](https://github.com/tulir/whatsmeow). Read and send messages, files, and audio from Python -- no cloud, no third-party API, just your linked WhatsApp account.

**Derived from [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp)**, but with the MCP server layer removed and the focus shifted to direct Python usage. If you were frustrated that the upstream MCP integration didn't load reliably in your client, this project treats the Python library as the primary interface.

## Why not MCP?

In practice, the MCP server layer from the upstream project failed to register its tools reliably across many client setups. Rather than fighting the loader, this project skips the MCP wrapper entirely and exposes the functionality as a plain Python library. You can still call it from any AI assistant -- you just call Python directly from the shell instead of relying on MCP tool registration.

## What's in the repo

```
whatsapp-python-client/
├── whatsapp-bridge/       Go bridge (whatsmeow) -- maintains the WhatsApp connection
│   └── main.go
└── whatsapp-client/       Python library
    ├── whatsapp.py        main API (list/search/send/read)
    └── audio.py           audio conversion helpers
```

## How it works

```
Your code  --Python--> whatsapp.py  --HTTP (localhost:8080)-->  Go bridge  <--WebSocket-->  WhatsApp
```

The Go bridge holds the WhatsApp connection and stores data in a local SQLite database (`whatsapp-bridge/store/`). The Python library reads message history straight from that database and sends outgoing messages via an HTTP API the bridge exposes on `localhost:8080`.

## Setup

### Prerequisites
- Go 1.21+
- Python 3.11+ and [uv](https://github.com/astral-sh/uv)
- A WhatsApp account on your phone (to scan the linking QR code)

### 1. Clone and build

```bash
git clone https://github.com/CWBinder/whatsapp-python-client.git
cd whatsapp-python-client

# Build the Go bridge
cd whatsapp-bridge && go build && cd ..

# Set up the Python environment
cd whatsapp-client && uv sync && cd ..
```

### 2. Link your WhatsApp account (one-time)

Run the bridge:

```bash
./start.sh
```

It will print a QR code. On your phone, open WhatsApp → Settings → Linked Devices → Link a Device, and scan. The session persists for ~2--3 weeks; after that, re-run `./start.sh` and scan again.

### 3. Use the Python library

With the bridge running, call functions from the Python side:

```bash
cd whatsapp-client
uv run python -c "from whatsapp import list_messages; print(list_messages(chat_jid='1234567890@s.whatsapp.net', limit=10))"
```

Or in a script:

```python
from whatsapp import list_messages, send_message, search_contacts

# Search for a contact
contacts = search_contacts("Alice")

# Read recent messages
messages = list_messages(chat_jid=contacts[0].jid, limit=20)

# Send a message
send_message(contacts[0].jid, "Hello from Python!")
```

See `whatsapp-client/whatsapp.py` for the full list of available functions.

## Using with Claude Code or other AI assistants

Since there is no MCP layer, you wire it in by letting the assistant call bash commands. A typical setup note in your assistant's instructions:

> WhatsApp client at `/path/to/whatsapp-python-client`. Start the Go bridge with `./start.sh` if not running. Call Python functions with `cd whatsapp-client && uv run python -c "from whatsapp import <function>; ..."`.

The assistant then runs those commands like any other shell commands. No MCP server registration required.

## Known gotcha: phone JIDs vs LIDs

WhatsApp has been migrating contacts to use opaque LIDs (e.g. `256903535947980@lid`) internally, while older code paths still use phone-based JIDs (e.g. `447707903896@s.whatsapp.net`). If a contact has been migrated and you send to the phone JID, **the message silently disappears** -- no error, no warning.

Workaround: before sending, check the `whatsmeow_lid_map` table in `whatsapp-bridge/store/whatsapp.db`:

```sql
SELECT lid FROM whatsmeow_lid_map WHERE pn = '447707903896';
```

If a LID exists, send to `<lid>@lid` instead of the phone JID. For reading message history, a contact's messages may be split across both identifiers -- query both and merge.

This is a real problem we hit in practice with several contacts; the upstream project does not handle it automatically.

## Credits

Original project: [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) by Luke Harries (MIT).

This repository removes the MCP server layer, ships dependency fixes for whatsmeow (adding `context.Background()` arguments required by recent versions), documents the LID gotcha, and repositions the project as a direct Python client.

## License

MIT. See [LICENSE](LICENSE) -- preserves both the original and modification copyrights.
