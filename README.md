# whatsapp-python-client

A command-line client for WhatsApp, backed by a Go bridge using [whatsmeow](https://github.com/tulir/whatsmeow). Read, search, and send messages, files, and voice notes from the shell -- no cloud, no third-party API, just your linked WhatsApp account.

**Derived from [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp)**, with the MCP server layer removed. Any assistant that can run shell commands can drive it; a `SKILL.md` at the repo root tells one how.

## What's in the repo

```
whatsapp-python-client/
├── SKILL.md                agent-facing instructions for the CLI
├── config.example.toml     copy to config.toml to enable sending
├── whatsapp-bridge/        Go bridge (whatsmeow): holds the WhatsApp connection,
│   └── main.go             writes messages to store/messages.db, serves send/download over HTTP
└── whatsapp-client/        Python package providing the `whatsapp` command
    └── whatsapp_cli/
```

## How it works

```
whatsapp CLI  --reads-->  store/messages.db + store/whatsapp.db   (SQLite, written by the bridge)
whatsapp CLI  --HTTP (localhost:8080)-->  Go bridge  <--websocket-->  WhatsApp
```

The bridge is a linked device. While it runs, every message on the account is pushed to it and written to a local SQLite archive. Stop it and the archive stops. The CLI reads the archive directly, so reading works even when the bridge is down (it warns when the data is stale), and uses the bridge's HTTP endpoints only for sending and media download.

## Setup

Prerequisites: Go 1.21+, Python 3.11+, [uv](https://github.com/astral-sh/uv), ffmpeg (only for voice notes).

```bash
git clone https://github.com/CWBinder/whatsapp-python-client.git
cd whatsapp-python-client
uv tool install --editable whatsapp-client      # puts `whatsapp` on PATH
whatsapp bridge build                            # go build
whatsapp bridge start               # prints a QR code; scan it from WhatsApp > Linked Devices
```

Once linked, run the bridge in the background and keep it there:

```bash
whatsapp bridge install     # launchd agent: starts at login, restarts on exit
whatsapp bridge status
```

WhatsApp drops linked devices that stay offline for roughly two weeks. Keeping the bridge running avoids re-scanning.

## Commands

```
whatsapp recent [--since 24h] [--incoming-only]        what came in lately, grouped by chat
whatsapp chats [-n N] [--groups|--people] [QUERY]     conversations, newest first
whatsapp threads [QUERY] [--from WHO] [--since TIME]  contract thread discovery
whatsapp read --message MESSAGE_ID
whatsapp read --thread THREAD_ID [-n N]
whatsapp read WHO [-n N]                              legacy name lookup
whatsapp search TEXT [--thread ID] [--from WHO]
whatsapp context MESSAGE_ID [--before N] [--after N]
whatsapp members GROUP                                 who has written in a group
whatsapp contacts QUERY
whatsapp resolve WHO                                   every address a name/number/LID maps to
whatsapp download MESSAGE_ID [--save DIR]
whatsapp send WHO --body TEXT                          (disabled until enabled in config.toml)
whatsapp send-file WHO PATH [--voice]
whatsapp bridge status|start|stop|log|build|install|uninstall
```

`WHO` accepts a person's name, phone number, LID, group name, or full JID. Add
`--json` to read commands for structured output. Message IDs emitted by search
are self-contained composites of the native chat and message identifiers.
Thread IDs are native chat JIDs; both can be passed back unchanged.
`read --thread` returns the complete archived thread unless `-n` explicitly
limits it to the newest messages.

## Phone JIDs, LIDs, and names

WhatsApp addresses an account two ways: a phone JID (`<number>@s.whatsapp.net`) and a LID (`<opaque id>@lid`). Contacts are migrating to LIDs, and sending to the phone form of a migrated contact silently fails. The bridge files a conversation under whichever form the server used, so one person can appear as two chats, and the bridge's own chat name for a LID chat is often just the id.

The CLI fixes this by reading both databases in the store: the `whatsmeow_lid_map` table pairs LIDs with phone numbers, and `whatsmeow_contacts` holds names under either form. Every name shown, every `WHO` argument, and every send recipient goes through that lookup. A person's two chats are merged, and sends go to the LID when one is known.

## Sending is off by default

`send` and `send-file` print a dry run and exit 2 unless `config.toml` contains `[send] enabled = true` or `WHATSAPP_ALLOW_SEND=1` is set. This is the guard that lets an assistant use the read side freely without ever sending by accident.

## Profiles: a second account

One bridge is one linked device, so a second WhatsApp account (for example an assistant identity that messages you from its own number) is a second bridge instance with its own store and port. Declare it in `config.toml`:

```toml
[profiles.claude]
store = "store-claude"   # under whatsapp-bridge/, gitignored
port  = 8081
```

Then every command takes `--profile claude` (or `WHATSAPP_PROFILE=claude`): `whatsapp --profile claude bridge start` links it by QR, `whatsapp --profile claude bridge install` keeps it running, `whatsapp --profile claude send WHO --body TEXT` sends from it. The default profile is the original layout, `store/` on port 8080, and needs no declaration. The bridge binary itself takes `-store DIR` and `-port N` (or `WHATSAPP_STORE`, `WHATSAPP_PORT`).

## Privacy

`whatsapp-bridge/store/` holds your session keys and every message in plain SQLite. It is gitignored. Treat it like a mailbox on disk.

## Credits

Original project: [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) by Luke Harries (MIT).

## License

MIT. See [LICENSE](LICENSE) -- preserves both the original and modification copyrights.
