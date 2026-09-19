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

All machines — the host and every client — need Python and Pillow
installed. Notifications currently only work on macOS, since they use
the built-in `osascript` command.

## Installation

```bash
git clone https://github.com/<your-username>/connectx.git
cd connectx
pip3 install pillow
```

## Usage

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
   If the host machine's own user wants to chat too, they also run
   `client.py` (see below) and connect to `127.0.0.1`.

3. **On each client machine**, run:
   ```bash
   python3 client.py
   ```
   Enter the host's IP address (printed in step 2) and a username, then
   click **Connect**.

4. **Chat.** Click a conversation in the left sidebar to switch between
   Group and any private conversations. Whichever conversation is
   selected is also who your next message is sent to. Use the 📷 button
   to attach an image.

### Firewall note

The first time you run `host.py`, macOS/Windows may prompt to allow
incoming network connections — allow it, or clients won't be able to
connect.

## Project structure

```
connectx/
├── host.py     # the server — routes messages between connected clients
├── client.py   # the GUI — connect, chat, send images, get notified
└── README.md
```

| File        | Purpose                                                        |
|-------------|-----------------------------------------------------------------|
| `host.py`   | The server. Accepts client connections and routes messages by recipient (group broadcast or private, by username). Runs one thread per connected client. |
| `client.py` | The GUI. Connects to a host, sends/receives messages and images, and handles all display, threading, and notification logic. |

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

## Known limitations

- **LAN only** — built for a host and clients on the same local
  network, not for chatting over the internet (no NAT traversal/relay
  server for that).
- **No encryption** — messages are sent as plain-text JSON. Fine for a
  trusted home/office network, not suitable for anything sensitive.
- **No message history** — nothing is saved to disk; closing the app
  loses the conversation.
- **Animated GIFs send as a single static frame**, not the full
  animation.
- **No chunking for large files** — an image is sent as one message;
  very large or unusual images may briefly pause the UI while
  encoding/decoding.
- **Notifications are macOS-only** for now.
