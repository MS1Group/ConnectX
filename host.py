"""
Chat host (server).

Run this on the machine acting as the host. Other laptops run client.py
and connect to this machine's LAN IP address.

Protocol: each message is one JSON object, followed by a newline ("\n").
Newline-delimited JSON avoids the TCP "message framing" problem — since TCP
is just a stream of bytes with no built-in message boundaries, we need our
own way to know where one message ends and the next begins. A newline is
the delimiter here, so messages must not contain raw newlines (we strip
those on the client side before sending).

Message types used in this protocol:
  {"type": "register", "username": "alice"}
      -> sent once by a client right after connecting.
  {"type": "message", "to": "group" | "<username>", "from": "<username>", "text": "..."}
      -> a chat message. "to" is either the literal string "group" (goes to
         everyone) or a specific username (private message to just them).
  {"type": "image", "to": "group" | "<username>", "from": "<username>",
   "data": "<base64-encoded JPEG bytes>", "filename": "photo.jpg"}
      -> an image message. Routed identically to "message" (by "to"); the
         host never decodes or inspects the image data, just relays it.
  {"type": "userlist", "users": ["alice", "bob"]}
      -> sent by the host to everyone whenever someone joins or leaves.
  {"type": "system", "text": "..."}
      -> informational message from the host (e.g. "bob joined").
  {"type": "error", "text": "..."}
      -> sent back to a client if something went wrong (e.g. name taken).
"""

import socket
import threading
import json

HOST = "0.0.0.0"  # listen on all network interfaces
PORT = 5050

# Maps username -> socket object, for every currently connected client.
# Shared across threads, so every read/write to it is wrapped in `lock`.
clients = {}
lock = threading.Lock()


def send_json(sock, obj):
    """Serialize obj to JSON and send it, newline-terminated."""
    data = (json.dumps(obj) + "\n").encode("utf-8")
    try:
        sock.sendall(data)
    except OSError:
        # The socket is already dead (client disconnected); ignore.
        pass


def broadcast(obj, exclude_username=None):
    """Send obj to every connected client except exclude_username (if given)."""
    with lock:
        for username, sock in clients.items():
            if username != exclude_username:
                send_json(sock, obj)


def broadcast_userlist():
    with lock:
        names = list(clients.keys())
    broadcast({"type": "userlist", "users": names})


def handle_client(conn, addr):
    """Runs in its own thread — one per connected client."""
    username = None
    # A file-like wrapper makes line-by-line reading trivial instead of
    # manually buffering partial reads from conn.recv().
    conn_file = conn.makefile("r", encoding="utf-8")

    try:
        # --- Step 1: the first message MUST be a registration ---
        first_line = conn_file.readline()
        if not first_line:
            return  # client disconnected before sending anything

        msg = json.loads(first_line)
        if msg.get("type") != "register" or not msg.get("username"):
            send_json(conn, {"type": "error", "text": "Expected registration first."})
            return

        requested_name = msg["username"].strip()

        with lock:
            if not requested_name:
                send_json(conn, {"type": "error", "text": "Username cannot be empty."})
                return
            if requested_name in clients:
                send_json(conn, {"type": "error", "text": "Username already taken."})
                return
            clients[requested_name] = conn
            username = requested_name

        print(f"[+] {username} connected from {addr}")
        broadcast({"type": "system", "text": f"{username} joined the chat."})
        broadcast_userlist()

        # --- Step 2: main receive loop ---
        for line in conn_file:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore malformed input rather than crashing

            # "message" = text chat, "image" = base64-encoded image data.
            # Both are routed identically — only the payload differs — so
            # we handle them the same way rather than duplicating the
            # to/group/private routing logic for each type.
            msg_type = msg.get("type")
            if msg_type not in ("message", "image"):
                continue

            to = msg.get("to", "group")
            out = dict(msg)  # keep whatever payload fields the client sent (text, or data+filename)
            out["from"] = username  # server is authoritative on identity — never trust a client-supplied "from"

            if to == "group":
                # Send to everyone except the sender — the sender already
                # displays their own message locally the moment they hit Send.
                broadcast(out, exclude_username=username)
            else:
                # Private message: route to exactly one recipient.
                with lock:
                    target_sock = clients.get(to)
                if target_sock:
                    send_json(target_sock, out)
                else:
                    send_json(conn, {"type": "error", "text": f"{to} is not online."})

    except (ConnectionResetError, ConnectionAbortedError):
        pass
    finally:
        if username:
            with lock:
                clients.pop(username, None)
            print(f"[-] {username} disconnected")
            broadcast({"type": "system", "text": f"{username} left the chat."})
            broadcast_userlist()
        conn.close()


def get_local_ip():
    """
    Find the LAN IP the OS would use to reach the outside world.

    socket.gethostbyname(socket.gethostname()) is unreliable on macOS —
    the hostname is often something like "MacBookAir.local", and that
    ".local" mDNS name doesn't always resolve via gethostbyname depending
    on network config, which raises socket.gaierror.

    Instead: open a UDP socket and "connect" it to an external address.
    UDP has no handshake, so this sends zero actual packets — it just
    asks the OS kernel which local network interface/IP it would route
    through to reach that address, which getsockname() then reports.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"  # fallback if there's no network at all
    finally:
        s.close()


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()

    # Print the LAN IP so you know what to type into the clients.
    local_ip = get_local_ip()
    print(f"Host listening on {local_ip}:{PORT}")
    print("Give this IP address to the clients.")

    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.close()


if __name__ == "__main__":
    main()
