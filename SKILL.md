---
name: whatsapp
description: Read, search, and (when enabled) send WhatsApp messages from the shell through the local whatsmeow bridge. Use when asked to check, find, summarise, or reply to WhatsApp messages, or to find out who is in a group.
---

# whatsapp

The `whatsapp` command is on PATH. It reads a local SQLite archive kept by a
Go bridge process and talks to that bridge over localhost for sending and
media download. Nothing leaves the machine except through the bridge's own
WhatsApp connection.

## Before anything else

```bash
whatsapp bridge status
```

Read the `archive` and `linked` lines. Every read command prints a note
when the archive is more than an hour old; treat old data as old. If the
bridge is not running, `whatsapp bridge start`. If `linked` says NO, the
session has expired: the account owner must run `whatsapp bridge start`
in a terminal and scan the QR code from WhatsApp > Linked Devices. Report
that; do not try to work around it.

## Reading (no bridge needed)

```bash
whatsapp recent [--since 24h] [--incoming-only]      what came in, grouped by chat
whatsapp chats [-n 20] [--groups|--people] [QUERY]   conversations, newest first
whatsapp threads [QUERY] [--from WHO] [--since TIME]
whatsapp read --message MESSAGE_ID
whatsapp read --thread THREAD_ID [-n 20]
whatsapp read WHO [-n 20]                             legacy name lookup
whatsapp search TEXT [--thread ID] [--from WHO] [-n 20]
whatsapp context MESSAGE_ID [--before 3] [--after 3]
whatsapp members GROUP                               who has written in a group
whatsapp contacts QUERY                              find people by name or number
whatsapp resolve WHO                                 every address a name/number maps to
```

`WHO` is a person's name, a phone number, a LID, a group name, or a JID.
A person's two addresses (phone form and LID form) are merged automatically.
Add `--json` to any read command for structured output. Message ids appear
in angle brackets at the end of each line and are self-contained; thread ids
identify native chats. Pass either back unchanged.

## Media (bridge must be running)

```bash
whatsapp download MESSAGE_ID [--save DIR]
```

Prints the local path. With `--save` the file is copied into DIR, which is
the hand-off point for filing it elsewhere, for example `ws add document`.

## Sending (external effect, disabled by default)

```bash
whatsapp send WHO --body "text"        or pipe the body on stdin
whatsapp send-file WHO PATH [--voice]
```

Both refuse to send and print a dry run unless sending is enabled in
`config.toml` (`[send] enabled = true`) or `WHATSAPP_ALLOW_SEND=1` is set.
Never enable sending on your own initiative. Always show the exact
recipient and text and get explicit confirmation before a real send.
Recipients are addressed by LID when one is known, because phone-form
delivery silently fails for migrated contacts.

## Bridge

```bash
whatsapp bridge status | start | stop | log | build | install | uninstall
```

`install` writes a launchd agent so the bridge runs at login and restarts if
it exits; that is what keeps the archive current and the link alive.

## Profiles (a second linked account)

Every command accepts `--profile NAME` before the subcommand, or
`WHATSAPP_PROFILE=NAME`. A profile is one linked WhatsApp account with its own
bridge instance, store and port, declared under `[profiles.NAME]` in
`config.toml`. Without it, the default profile is used. `whatsapp bridge
status` prints which profile it is looking at. A profile whose bridge is not
linked needs the same QR scan as the first one, from the phone that owns that
number.
