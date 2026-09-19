# ConnectX

A group and private chat app for computers on the same local network —
no internet, no external server, no account required. One machine runs
as the host; everyone else connects to it directly over TCP.

## About

ConnectX is a self-contained Python chat application built around a
simple client-server model: one computer hosts the chat, and any number
of other machines on the same network connect to it as clients. It
supports both group messaging (everyone sees everything) and private
one-to-one conversations, each kept in its own separate thread with
unread badges. Messages can include text and images, and the client
raises native macOS notifications for anything received while the app
isn't focused. There's no cloud service, no sign-up, and no data leaves
your local network — everything is relayed directly by the host machine.

## Features

- **Group chat** — one shared conversation everyone in it can see.
- **Private chat** — a separate one-to-one thread per person, kept
  fully isolated from the group and from other private threads.
- **Image sharing** — send photos (JPEG, PNG, GIF, BMP, WebP, and HEIC
  with an optional extra package); images are automatically resized
  and compressed before sending.
- **Unread badges** — conversations you're not currently viewing show
  a count of unread messages in the sidebar.
- **Native macOS notifications** — get notified with the sender's name,
  whether it's a group or private message, and the message content,
  whenever the app isn't the focused window.
- **Dark, WhatsApp-style interface** — sent messages on the right,
  received on the left, with per-conversation history.

## Requirements

These apply if you're running with Python (Option A below). If you're
using the prebuilt app (Option B), everything is already bundled in —
you don't need any of this.

- Python 3.9 or later (comes with `tkinter` on most installs)
- [Pillow](https://pypi.org/project/Pillow/), for image support:
  ```bash
  pip3 install pillow
  ```
- *(Optional)* [pillow-heif](https://pypi.org/project/pillow-heif/), if
  you want to send `.heic`/`.heif` photos (Apple's default photo format
  on iPhone and Mac):
  ```bash
  pip3 install pillow-heif
  ```

The **host** always needs Python installed regardless (see Usage
below) — `host.py` isn't packaged as an app. Notifications currently
only work on macOS, since they use the built-in `osascript` command.

## Installation

```bash
git clone https://github.com/<your-username>/connectx.git
cd connectx
pip3 install pillow
```

## Usage

**The host always runs via Python, no matter what.** `host.py` is a
plain script — it was intentionally kept out of the packaged app — so
whoever hosts needs Python 3 installed and runs it from Terminal. This
applies even if everyone joining uses the prebuilt app below.

1. **Pick one computer to be the host.** All machines must be on the
   same Wi-Fi/LAN network.

2. **On the host machine**, run:
   ```bash
   python3 host.py
   ```
   It will print its LAN IP address, e.g.:
   ```
   Host listening on 192.168.1.14:5050
   Give this IP address to the clients.
   ```
   Leave this running — it's the relay every client connects through.

3. **Everyone joining** (including the host machine's own user, who
   connects to `127.0.0.1`) picks one of two ways to run the client:

   **Option A — Run with Python**
   ```bash
   python3 client.py
   ```

   **Option B — Download the prebuilt app** *(Apple Silicon Mac only)*

   [**⬇ Download ConnectX.app**](https://github.com/<your-username>/connectx/releases/latest)

   No Python or dependencies needed — everything is bundled inside the
   app. It is **not code-signed** (no paid Apple Developer account), so
   the first launch will show macOS's "cannot be opened because the
   developer cannot be verified" warning. **Right-click the app → Open**
   once to allow it — after that it opens normally every time.

4. **Chat.** Enter the host's IP address and a username, then click
   **Connect**. Click a conversation in the left sidebar to switch
   between Group and any private conversations. Whichever conversation
   is selected is also who your next message is sent to. Use the 📷
   button to attach an image.

### Firewall note

The first time you run `host.py`, macOS/Windows may prompt to allow
incoming network connections — allow it, or clients won't be able to
connect.

## Project structure

```
connectx/
├── host.py     # the server — routes messages between connected clients
├── client.py   # the GUI — connect, chat, send images, get notified
├── setup.py    # py2app build script — packages client.py as ConnectX.app
└── README.md
```

| File        | Purpose                                                        |
|-------------|-----------------------------------------------------------------|
| `host.py`   | The server. Accepts client connections and routes messages by recipient (group broadcast or private, by username). Runs one thread per connected client. |
| `client.py` | The GUI. Connects to a host, sends/receives messages and images, and handles all display, threading, and notification logic. |
| `setup.py`  | Build script for packaging `client.py` as a standalone Mac app with `py2app`. Not needed to just run the project with Python — only to build the app. |

## How it works

- **Protocol**: every message is a single JSON object followed by a
  newline, sent over a plain TCP socket. This is the framing that works
  around TCP having no built-in message boundaries.
- **Routing**: the host doesn't inspect message content — it just
  reads the `"to"` field (`"group"` or a specific username) and
  forwards accordingly, stamping the authenticated sender onto every
  message so a client can't spoof someone else's username.
- **Threading (client side)**: tkinter's event loop and blocking socket
  reads can't share a thread, so a background thread does nothing but
  read incoming messages into a queue; the GUI polls that queue every
  100ms and is the only thing that ever touches widgets.
- **Local history**: each client saves its own conversations to a
  SQLite database in `~/Library/Application Support/ConnectX/`. Text
  is stored directly in the database; images are saved as files
  alongside it (only the filename is stored in the database) so it
  doesn't balloon in size. This is local to each machine — it isn't
  synced between devices or with the host.

## Known limitations

- **LAN only** — built for a host and clients on the same local
  network, not for chatting over the internet (no NAT traversal/relay
  server for that).
- **No encryption** — messages are sent as plain-text JSON. Fine for a
  trusted home/office network, not suitable for anything sensitive.
- **History is per-machine, not per-account** — messages are saved
  locally on whichever machine sent/received them (see
  [How it works](#how-it-works)). If different people share the same
  Mac at different times, they'll see the same local history.
- **Animated GIFs send as a single static frame**, not the full
  animation.
- **No chunking for large files** — an image is sent as one message;
  very large or unusual images may briefly pause the UI while
  encoding/decoding.
- **Notifications are macOS-only** for now.
- **The app isn't code-signed** — expect a one-time Gatekeeper warning
  on first launch per machine (see Usage above).
