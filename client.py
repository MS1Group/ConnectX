"""
Chat client (tkinter GUI) — v4: image sending.

New in v4:
  - An image-attach button next to Send. Picks a file, resizes it down to a
    max of 1600px on its longest side (so a 12MP phone photo doesn't stall
    the transfer or bloat memory), base64-encodes it, and sends it as a
    {"type": "image", ...} message using the same to/from routing as text.
  - Images render as inline thumbnails in the chat bubble, same left/right
    alignment rules as text.
  - Requires Pillow (`pip3 install pillow`) — tkinter's built-in image
    support only handles PNG/GIF natively, not JPEG. If Pillow isn't
    installed, the app still runs: the image button is disabled with an
    explanation, and any image messages that arrive show a text
    placeholder instead of crashing.

Thread-safety fix in this version: `_send()` used to call `_add_message()`
directly on failure, which touches tkinter widgets — safe only because
`_send()` used to always run on the main thread. Sending a resized image
now happens on a background thread (so a slow upload doesn't freeze the
GUI), so `_send()` can no longer touch widgets directly from any context.
It now routes failures through the same incoming queue as everything else,
so `_poll_incoming` (which runs on the main thread) is the only thing that
ever touches a widget because of it.
"""

import socket
import threading
import json
import queue
import base64
import io
import os
import sys
import subprocess
import sqlite3
import uuid
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, filedialog

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# HEIC/HEIF is Apple's default photo format on iPhone and Mac — a photo
# picked straight from Photos.app or a recent screenshot is very likely
# this format. Pillow can't open it without this extra plugin. Optional:
# if it's missing, HEIC files just fail to send with a clear error instead
# of the whole app breaking.
if PIL_AVAILABLE:
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        HEIC_AVAILABLE = True
    except ImportError:
        HEIC_AVAILABLE = False
else:
    HEIC_AVAILABLE = False

PORT = 5050
MAX_SEND_DIMENSION = 1600   # shrink images to at most this on their longest side before sending
THUMBNAIL_DIMENSION = 260   # size images are shown at inside a chat bubble
NOTIFICATION_BODY_MAX_CHARS = 120  # truncate long messages in the notification banner

# Local, per-machine chat history. macOS's standard location for a user's
# own app data — this is NOT shared between your 3 Macs, each one keeps
# its own history of whatever conversations happened on it. Text lives
# directly in the database; images are saved as files here too (with only
# the filename stored in the database) so the database itself doesn't
# balloon in size the way embedding base64 image data in it would.
APP_SUPPORT_DIR = (os.path.expanduser("~/Library/Application Support/ConnectX")
                    if sys.platform == "darwin" else os.path.expanduser("~/.connectx"))
HISTORY_DB_PATH = os.path.join(APP_SUPPORT_DIR, "history.db")
HISTORY_IMAGES_DIR = os.path.join(APP_SUPPORT_DIR, "images")


def init_history_db():
    """Create the local database (and images folder) if they don't exist
    yet, and return an open connection. Only 'chat' and 'image' messages
    are ever stored — join/leave notices and errors are transient status,
    not conversation history, so they're never written here."""
    os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
    os.makedirs(HISTORY_IMAGES_DIR, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_key TEXT NOT NULL,
            sender TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT,
            image_filename TEXT,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def save_image_to_disk(base64_data):
    """Decode a base64 image payload and save it under HISTORY_IMAGES_DIR
    with a unique filename. Returns just the filename (not the full path)
    to store in the database, or None if the image couldn't be decoded."""
    try:
        raw = base64.b64decode(base64_data)
        if PIL_AVAILABLE:
            fmt = (Image.open(io.BytesIO(raw)).format or "JPEG").lower()
        else:
            fmt = "jpg"
        ext = "jpg" if fmt == "jpeg" else fmt
        filename = f"{uuid.uuid4().hex}.{ext}"
        with open(os.path.join(HISTORY_IMAGES_DIR, filename), "wb") as f:
            f.write(raw)
        return filename
    except Exception:
        return None


def save_message_to_history(conn, conversation_key, sender, kind, text=None, image_filename=None):
    conn.execute(
        "INSERT INTO messages (conversation_key, sender, kind, text, image_filename, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (conversation_key, sender, kind, text, image_filename, datetime.now().isoformat()),
    )
    conn.commit()


def load_history(conn, current_username):
    """Returns {conversation_key: [message dicts]}, oldest first, in the
    exact shape ChatClient uses at runtime. 'own' is recomputed against
    the CURRENT username rather than trusting a stored flag, since the
    same machine could be used to join under a different username later —
    a message should only show as "own" if it matches who you are now."""
    conversations = {}
    rows = conn.execute(
        "SELECT conversation_key, sender, kind, text, image_filename FROM messages ORDER BY id ASC"
    ).fetchall()

    for conversation_key, sender, kind, text, image_filename in rows:
        own = (sender == current_username)
        if kind == "chat":
            msg = {"kind": "chat", "from": sender, "text": text or "", "own": own}
        elif kind == "image":
            msg = _reload_image_message(sender, image_filename, own)
        else:
            continue
        conversations.setdefault(conversation_key, []).append(msg)

    return conversations


def _reload_image_message(sender, image_filename, own):
    """Read a previously-saved image back off disk and re-encode it to the
    same base64-in-memory shape used for a live image message, so the
    existing rendering code needs no special case for "loaded from
    history" vs. "just received". Falls back to a text placeholder if the
    file is missing (e.g. the images folder was moved or cleaned up)."""
    if not image_filename:
        return {"kind": "chat", "from": sender, "text": "[image unavailable]", "own": own}
    try:
        with open(os.path.join(HISTORY_IMAGES_DIR, image_filename), "rb") as f:
            raw = f.read()
        return {"kind": "image", "from": sender, "data": base64.b64encode(raw).decode("ascii"),
                "filename": image_filename, "own": own}
    except OSError:
        return {"kind": "chat", "from": sender, "text": "[image unavailable]", "own": own}

# --- Dark, WhatsApp-inspired palette. Every widget uses ONLY these
# constants for bg/fg — never an unset default. ---
BG_APP = "#0B141A"
BG_SIDEBAR = "#111B21"
BG_SIDEBAR_SELECTED = "#2A3942"
BG_HEADER = "#202C33"
BG_ENTRY = "#2A3942"
BUBBLE_OWN = "#005C4B"
BUBBLE_OTHER = "#202C33"
TEXT_PRIMARY = "#E9EDEF"
TEXT_MUTED = "#8696A0"
ACCENT = "#00A884"
FONT_MAIN = ("Helvetica", 11)
FONT_SMALL = ("Helvetica", 9)


class Conversation:
    """Holds everything the UI needs to know about one conversation thread."""

    def __init__(self, key, is_group):
        self.key = key
        self.is_group = is_group
        self.messages = []   # list of dicts: {kind: "chat"|"image"|"system", ...}
        self.unread = 0


class ClickableLabel(tk.Label):
    """A Label styled and behaving like a button. Used instead of tk.Button
    because macOS's native theme ignores custom background colors on real
    Button widgets, making colored buttons look broken there."""

    def __init__(self, parent, text, command, bg=ACCENT, fg="white", disabled=False, **kwargs):
        super().__init__(parent, text=text, bg=bg, fg=fg,
                          cursor="hand2" if not disabled else "arrow",
                          font=FONT_MAIN, padx=14, pady=6, **kwargs)
        self._bg = bg
        self._hover_bg = self._darken(bg)
        self.disabled = disabled
        if not disabled:
            self.bind("<Button-1>", lambda e: command())
            self.bind("<Enter>", lambda e: self.configure(bg=self._hover_bg))
            self.bind("<Leave>", lambda e: self.configure(bg=self._bg))

    @staticmethod
    def _darken(hex_color, factor=0.85):
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
        r, g, b = (max(0, int(c * factor)) for c in (r, g, b))
        return f"#{r:02x}{g:02x}{b:02x}"


class ChatClient:
    def __init__(self, root):
        self.root = root
        self.root.title("ConnectX")
        self.root.configure(bg=BG_APP)
        self.sock = None
        self.username = None
        self.incoming = queue.Queue()

        self.conversations = {"Group": Conversation("Group", is_group=True)}
        self.current_key = "Group"
        self.sidebar_rows = {}
        # Keeps strong references to every currently-displayed PhotoImage.
        # tkinter does NOT keep an image alive on its own — if nothing in
        # Python holds a reference, it gets garbage-collected and the
        # widget silently goes blank even though it looks "attached".
        self._image_refs = []
        # Tracks whether THIS app's window currently has OS focus, so we
        # know when to fire a notification (only when the user has clicked
        # away to another app/window, matching normal chat-app behavior).
        self.app_focused = True

        # History is best-effort: if the local database can't be opened
        # for any reason (permissions, disk full), the app should still
        # work — it just won't remember anything between sessions.
        try:
            self.history_conn = init_history_db()
        except Exception:
            self.history_conn = None

        self._build_connect_screen()

    # ---------- Connect screen ----------

    def _build_connect_screen(self):
        self.connect_frame = tk.Frame(self.root, padx=24, pady=24, bg=BG_APP)
        self.connect_frame.pack(fill="both", expand=True)

        tk.Label(self.connect_frame, text="ConnectX", font=("Helvetica", 18, "bold"),
                 bg=BG_APP, fg=TEXT_PRIMARY).grid(row=0, column=0, columnspan=2, pady=(0, 16))

        tk.Label(self.connect_frame, text="Host IP address:", bg=BG_APP, fg=TEXT_PRIMARY,
                 font=FONT_MAIN).grid(row=1, column=0, sticky="w", pady=5)
        self.ip_entry = tk.Entry(self.connect_frame, width=25, font=FONT_MAIN,
                                  bg=BG_ENTRY, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                                  relief="flat")
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.grid(row=1, column=1, pady=5, ipady=4)

        tk.Label(self.connect_frame, text="Your username:", bg=BG_APP, fg=TEXT_PRIMARY,
                 font=FONT_MAIN).grid(row=2, column=0, sticky="w", pady=5)
        self.name_entry = tk.Entry(self.connect_frame, width=25, font=FONT_MAIN,
                                    bg=BG_ENTRY, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                                    relief="flat")
        self.name_entry.grid(row=2, column=1, pady=5, ipady=4)

        ClickableLabel(self.connect_frame, "Connect", self._connect).grid(
            row=3, column=0, columnspan=2, pady=16)

        if not PIL_AVAILABLE:
            tk.Label(self.connect_frame,
                     text="Note: Pillow isn't installed — image sending/viewing will be disabled.\n"
                          "Install it with: pip3 install pillow",
                     font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_APP, justify="center").grid(
                row=4, column=0, columnspan=2, pady=(0, 4))

        self.ip_entry.focus()
        self.name_entry.bind("<Return>", lambda e: self._connect())

    def _connect(self):
        host_ip = self.ip_entry.get().strip()
        username = self.name_entry.get().strip()
        if not host_ip or not username:
            messagebox.showerror("Missing info", "Enter both an IP address and a username.")
            return

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((host_ip, PORT))
            sock.settimeout(None)
        except OSError as e:
            messagebox.showerror("Connection failed", str(e))
            return

        self.sock = sock
        self.username = username
        self._send({"type": "register", "username": username})

        self.sock_file = self.sock.makefile("r", encoding="utf-8")
        first_line = self.sock_file.readline()
        if first_line:
            msg = json.loads(first_line)
            if msg.get("type") == "error":
                messagebox.showerror("Could not join", msg.get("text", "Unknown error"))
                self.sock.close()
                return
            else:
                self.incoming.put(msg)

        self.connect_frame.destroy()
        self._build_chat_screen()
        self._load_local_history()

        threading.Thread(target=self._receive_loop, daemon=True).start()
        self.root.after(100, self._poll_incoming)

    def _load_local_history(self):
        """Populate self.conversations with anything previously saved on
        THIS machine. Runs after _build_chat_screen() (not before) because
        it needs the sidebar widgets to already exist to redraw them
        afterward — going through _get_or_create_conversation() before the
        UI exists would crash trying to rebuild a sidebar that isn't built
        yet."""
        if not self.history_conn:
            return
        try:
            loaded = load_history(self.history_conn, self.username)
        except Exception:
            return

        for conv_key, messages in loaded.items():
            conv = self.conversations.setdefault(conv_key, Conversation(conv_key, is_group=(conv_key == "Group")))
            conv.messages = messages + conv.messages

        if loaded:
            self._rebuild_sidebar()
            self._render_conversation(self.current_key)

    # ---------- Chat screen layout ----------

    def _build_chat_screen(self):
        self.root.title(f"ConnectX — {self.username}")
        self.root.geometry("780x540")
        self.root.configure(bg=BG_APP)

        # Only react to focus events on the window itself (not on child
        # widgets shifting focus between each other as you tab/click
        # around inside the app) — checking event.widget filters that out.
        self.root.bind("<FocusIn>", self._on_focus_in)
        self.root.bind("<FocusOut>", self._on_focus_out)

        main = tk.Frame(self.root, bg=BG_APP)
        main.pack(fill="both", expand=True)

        # --- Sidebar ---
        sidebar = tk.Frame(main, bg=BG_SIDEBAR, width=200)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="Conversations", font=("Helvetica", 12, "bold"),
                 bg=BG_SIDEBAR, fg=TEXT_PRIMARY, anchor="w", padx=12, pady=10).pack(fill="x")

        self.sidebar_list_frame = tk.Frame(sidebar, bg=BG_SIDEBAR)
        self.sidebar_list_frame.pack(fill="both", expand=True)

        # --- Right side: header + scrollable message area + input row ---
        right = tk.Frame(main, bg=BG_APP)
        right.pack(side="left", fill="both", expand=True)

        self.header_label = tk.Label(right, text="Group", font=("Helvetica", 13, "bold"),
                                      bg=BG_HEADER, fg=TEXT_PRIMARY, anchor="w", padx=14, pady=10)
        self.header_label.pack(fill="x")

        canvas_container = tk.Frame(right, bg=BG_APP)
        canvas_container.pack(fill="both", expand=True)

        self.msg_canvas = tk.Canvas(canvas_container, bg=BG_APP, highlightthickness=0)
        scrollbar = tk.Scrollbar(canvas_container, orient="vertical", command=self.msg_canvas.yview)
        self.msg_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.msg_canvas.pack(side="left", fill="both", expand=True)

        self.msg_frame = tk.Frame(self.msg_canvas, bg=BG_APP)
        self.msg_frame_window = self.msg_canvas.create_window((0, 0), window=self.msg_frame, anchor="nw")

        self.msg_frame.bind("<Configure>", self._on_msg_frame_configure)
        self.msg_canvas.bind("<Configure>", self._on_canvas_configure)

        # Input row
        entry_row = tk.Frame(right, bg=BG_APP, pady=8, padx=8)
        entry_row.pack(fill="x")

        self.msg_entry = tk.Entry(entry_row, font=FONT_MAIN, bg=BG_ENTRY, fg=TEXT_PRIMARY,
                                   insertbackground=TEXT_PRIMARY, relief="flat")
        self.msg_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.msg_entry.bind("<Return>", lambda e: self._send_message())
        self.msg_entry.focus()

        image_btn_bg = ACCENT if PIL_AVAILABLE else "#3A4650"
        ClickableLabel(entry_row, "📷", self._pick_image, bg=image_btn_bg,
                       disabled=not PIL_AVAILABLE).pack(side="left", padx=(8, 0))
        ClickableLabel(entry_row, "Send", self._send_message).pack(side="left", padx=(8, 0))

        self._rebuild_sidebar()
        self._render_conversation("Group")

    def _on_msg_frame_configure(self, event):
        self.msg_canvas.configure(scrollregion=self.msg_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.msg_canvas.itemconfig(self.msg_frame_window, width=event.width)

    # ---------- Sidebar ----------

    def _rebuild_sidebar(self):
        for widget in self.sidebar_list_frame.winfo_children():
            widget.destroy()
        self.sidebar_rows = {}

        keys = ["Group"] + sorted(k for k in self.conversations if k != "Group")
        for key in keys:
            self._add_sidebar_row(key)

    def _add_sidebar_row(self, key):
        conv = self.conversations[key]
        selected = key == self.current_key
        bg = BG_SIDEBAR_SELECTED if selected else BG_SIDEBAR

        row = tk.Frame(self.sidebar_list_frame, bg=bg, padx=12, pady=8, cursor="hand2")
        row.pack(fill="x")

        name_label = tk.Label(row, text=key, font=FONT_MAIN, bg=bg, fg=TEXT_PRIMARY, anchor="w")
        name_label.pack(side="left", fill="x", expand=True)

        badge_label = tk.Label(row, text=str(conv.unread) if conv.unread else "",
                                font=("Helvetica", 9, "bold"), bg=ACCENT, fg="white",
                                padx=6, borderwidth=0)
        if conv.unread:
            badge_label.pack(side="right")

        for widget in (row, name_label, badge_label):
            widget.bind("<Button-1>", lambda e, k=key: self._select_conversation(k))

        self.sidebar_rows[key] = row

    def _select_conversation(self, key):
        self.current_key = key
        self.conversations[key].unread = 0
        self._rebuild_sidebar()
        self._render_conversation(key)

    # ---------- Message rendering ----------

    def _render_conversation(self, key):
        conv = self.conversations[key]
        self.header_label.configure(text=key if key == "Group" else f"{key} (private)")

        for widget in self.msg_frame.winfo_children():
            widget.destroy()
        # Every widget in this conversation is about to be rebuilt from
        # scratch, so any PhotoImage references from the previous view are
        # safe to drop now — new ones get added as we re-render below.
        self._image_refs = []

        for msg in conv.messages:
            self._render_one_message(msg, conv.is_group)

        self._scroll_to_bottom()

    def _render_one_message(self, msg, is_group):
        if msg["kind"] == "system":
            row = tk.Frame(self.msg_frame, bg=BG_APP, pady=4)
            row.pack(fill="x")
            tk.Label(row, text=msg["text"], font=FONT_SMALL, fg=TEXT_MUTED,
                     bg=BG_APP).pack(anchor="center")
            return

        row = tk.Frame(self.msg_frame, bg=BG_APP, pady=3, padx=10)
        row.pack(fill="x")

        side = "e" if msg["own"] else "w"
        bubble_container = tk.Frame(row, bg=BG_APP)
        bubble_container.pack(anchor=side)

        # In group chat, show who sent it above the bubble — except your own,
        # which is obviously you. Private chat never needs this (only 2 people).
        if is_group and not msg["own"]:
            tk.Label(bubble_container, text=msg["from"], font=("Helvetica", 8, "bold"),
                     fg=TEXT_MUTED, bg=BG_APP).pack(anchor="w", padx=4)

        bubble_bg = BUBBLE_OWN if msg["own"] else BUBBLE_OTHER

        if msg["kind"] == "image":
            self._render_image_bubble(bubble_container, msg, bubble_bg, side)
        else:
            tk.Label(bubble_container, text=msg["text"], font=FONT_MAIN,
                     bg=bubble_bg, fg=TEXT_PRIMARY,
                     wraplength=320, justify="left", padx=10, pady=6).pack(anchor=side)

    def _render_image_bubble(self, parent, msg, bubble_bg, side):
        if not PIL_AVAILABLE:
            tk.Label(parent, text=f"[Image: {msg['filename']}]\n(install Pillow to view: pip3 install pillow)",
                     font=FONT_SMALL, fg=TEXT_PRIMARY, bg=bubble_bg,
                     wraplength=280, justify="left", padx=10, pady=6).pack(anchor=side)
            return

        try:
            raw = base64.b64decode(msg["data"])
            img = Image.open(io.BytesIO(raw))
            img.thumbnail((THUMBNAIL_DIMENSION, THUMBNAIL_DIMENSION))
            photo = ImageTk.PhotoImage(img)
            self._image_refs.append(photo)  # prevent garbage collection — see __init__ comment
            tk.Label(parent, image=photo, bg=BG_APP, borderwidth=0).pack(anchor=side)
        except Exception:
            tk.Label(parent, text=f"[Could not display image: {msg.get('filename', '?')}]",
                     font=FONT_SMALL, fg=TEXT_PRIMARY, bg=bubble_bg,
                     wraplength=280, padx=10, pady=6).pack(anchor=side)

    def _scroll_to_bottom(self):
        self.msg_frame.update_idletasks()
        self.msg_canvas.configure(scrollregion=self.msg_canvas.bbox("all"))
        self.msg_canvas.yview_moveto(1.0)

    # ---------- Conversation bookkeeping ----------

    def _get_or_create_conversation(self, key, is_group=False):
        if key not in self.conversations:
            self.conversations[key] = Conversation(key, is_group=is_group)
            self._rebuild_sidebar()
        return self.conversations[key]

    def _add_message(self, key, msg):
        """Store msg in conversation `key`. Render it live if that conversation
        is currently open; otherwise bump its unread badge."""
        conv = self._get_or_create_conversation(key, is_group=(key == "Group"))
        conv.messages.append(msg)

        # Every text/image message — sent or received — passes through
        # here exactly once, so this is the one place that needs to know
        # about persistence. System messages (join/leave, errors) are
        # deliberately never saved — they're transient status, not
        # conversation content.
        if msg["kind"] in ("chat", "image"):
            self._persist_message(key, msg)

        if key == self.current_key:
            self._render_one_message(msg, conv.is_group)
            self._scroll_to_bottom()
        else:
            conv.unread += 1
            self._rebuild_sidebar()

    def _persist_message(self, key, msg):
        """Best-effort: a history save failing should never break the chat
        itself, so every failure here is swallowed rather than surfaced."""
        if not self.history_conn:
            return
        try:
            if msg["kind"] == "chat":
                save_message_to_history(self.history_conn, key, msg["from"], "chat", text=msg["text"])
            elif msg["kind"] == "image":
                image_filename = save_image_to_disk(msg["data"])
                save_message_to_history(self.history_conn, key, msg["from"], "image",
                                         image_filename=image_filename)
        except Exception:
            pass

    def _on_focus_in(self, event):
        if event.widget == self.root:
            self.app_focused = True

    def _on_focus_out(self, event):
        if event.widget == self.root:
            self.app_focused = False

    def _notify(self, sender, is_group, body):
        """Fire a native macOS Notification Center banner. Best-effort only:
        any failure here (wrong OS, osascript missing, permission not yet
        granted) is swallowed rather than crashing the chat over a banner."""
        subtitle = "Group" if is_group else "Private message"
        if len(body) > NOTIFICATION_BODY_MAX_CHARS:
            body = body[:NOTIFICATION_BODY_MAX_CHARS - 1] + "…"

        def escape(s):
            # The message/sender text comes from other people on the
            # network — it must be escaped before being spliced into an
            # AppleScript string literal, or a message containing a
            # double-quote could break out of the string and be
            # interpreted as AppleScript source instead of plain text.
            return s.replace("\\", "\\\\").replace('"', '\\"')

        script = (
            f'display notification "{escape(body)}" '
            f'with title "{escape(sender)}" '
            f'subtitle "{escape(subtitle)}"'
        )
        try:
            # Popen, not run() — fire-and-forget so this never blocks
            # _poll_incoming (which runs on the main thread and would
            # otherwise freeze the whole GUI while osascript starts up).
            subprocess.Popen(["osascript", "-e", script],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass  # not on macOS, or osascript unavailable — nothing to do

    # ---------- Networking ----------

    def _send(self, obj):
        """Safe to call from ANY thread — never touches a tkinter widget
        directly. On failure it queues a system message instead of calling
        _add_message() directly, since only the main thread (via
        _poll_incoming) is allowed to touch widgets."""
        data = (json.dumps(obj) + "\n").encode("utf-8")
        try:
            self.sock.sendall(data)
        except OSError:
            self.incoming.put({"type": "system", "text": "[Connection lost]"})

    def _send_message(self):
        text = self.msg_entry.get().strip()
        if not text:
            return

        key = self.current_key
        to = "group" if key == "Group" else key

        self._send({"type": "message", "to": to, "from": self.username, "text": text})
        self._add_message(key, {"kind": "chat", "from": self.username, "text": text, "own": True})
        self.msg_entry.delete(0, "end")

    def _pick_image(self):
        if not PIL_AVAILABLE:
            messagebox.showerror(
                "Pillow required",
                "Sending images requires the Pillow library.\n\nInstall it with:\n  pip3 install pillow"
            )
            return

        path = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp *.heic *.heif"),
                       ("All files", "*.*")],
        )
        if not path:
            return

        # Capture the target conversation now, in case the user switches
        # tabs before the background thread below finishes resizing.
        key = self.current_key
        threading.Thread(target=self._send_image, args=(path, key), daemon=True).start()

    def _send_image(self, path, key):
        """Runs on a background thread. Resizing and base64-encoding a large
        photo can take a moment — doing this on the main thread would
        freeze the whole GUI for that moment, so it happens here instead."""
        try:
            img = Image.open(path)
            img.load()  # force-read now, on this background thread, so a
                        # corrupt/unsupported file fails HERE with a clear
                        # error instead of surfacing a confusing exception
                        # later during rendering on the main thread.
            img.thumbnail((MAX_SEND_DIMENSION, MAX_SEND_DIMENSION))
            fmt = (img.format or "PNG")
            if fmt in ("HEIC", "HEIF"):
                fmt = "JPEG"  # HEIC isn't something the *receiving* client
                              # can necessarily display either — always
                              # convert to JPEG before sending, regardless
                              # of what format it arrived in.
            if fmt == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")  # JPEG doesn't support transparency/palette modes
            buffer = io.BytesIO()
            img.save(buffer, format=fmt)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as e:
            filename = os.path.basename(path)
            is_heic = filename.lower().endswith((".heic", ".heif"))
            if is_heic and not HEIC_AVAILABLE:
                detail = ("This is a HEIC photo (Apple's default format) and HEIC "
                           "support isn't installed.\n\nInstall it with:\n"
                           "  pip3 install pillow-heif\n\nthen restart the app.")
            else:
                detail = f"Could not read {filename}:\n{e}"
            # Background thread: can't call messagebox directly (Tk dialogs
            # must run on the main thread) — queue it so _handle_message
            # shows the popup from the main thread via _poll_incoming.
            self.incoming.put({"type": "image_error", "text": detail})
            return

        to = "group" if key == "Group" else key
        filename = os.path.basename(path)

        self._send({"type": "image", "to": to, "from": self.username,
                    "data": encoded, "filename": filename})

        # Queue the local echo instead of calling _add_message() directly —
        # this is a background thread, so only _poll_incoming (main thread)
        # is allowed to touch widgets.
        self.incoming.put({"type": "image", "from": self.username, "to": to,
                            "data": encoded, "filename": filename,
                            "_own": True, "_own_key": key})

    def _receive_loop(self):
        """Background thread only. Never touches tkinter widgets directly."""
        try:
            for line in self.sock_file:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.incoming.put(msg)
        except OSError:
            pass
        finally:
            self.incoming.put({"type": "system", "text": "Disconnected from host."})

    def _poll_incoming(self):
        """Main thread only, via root.after(). Safe to touch widgets here."""
        try:
            while True:
                msg = self.incoming.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_incoming)

    def _handle_message(self, msg):
        mtype = msg.get("type")

        if mtype == "message":
            sender = msg.get("from", "?")
            to = msg.get("to", "group")
            text = msg.get("text", "")
            key = "Group" if to == "group" else sender
            self._add_message(key, {"kind": "chat", "from": sender, "text": text, "own": False})
            # This branch only ever runs for messages from OTHER people —
            # your own sent text is echoed locally by _send_message()
            # directly, never routed back through here.
            if not self.app_focused:
                self._notify(sender, is_group=(key == "Group"), body=text)

        elif mtype == "image":
            own = msg.get("_own", False)
            if own:
                key = msg.get("_own_key", "Group")
                sender = self.username
            else:
                sender = msg.get("from", "?")
                to = msg.get("to", "group")
                key = "Group" if to == "group" else sender
            self._add_message(key, {
                "kind": "image", "from": sender, "data": msg.get("data"),
                "filename": msg.get("filename", "image"), "own": own,
            })
            # own=True is your own image being echoed back to yourself
            # locally — never notify about your own message.
            if not own and not self.app_focused:
                self._notify(sender, is_group=(key == "Group"), body="sent an image")

        elif mtype == "image_error":
            self._add_message("Group", {"kind": "system", "text": f"[Image not sent] {msg.get('text', '')}"})
            messagebox.showerror("Couldn't send image", msg.get("text", "Unknown error"))

        elif mtype == "system":
            self._add_message("Group", {"kind": "system", "text": msg.get("text", "")})

        elif mtype == "error":
            self._add_message(self.current_key, {"kind": "system", "text": f"[Error] {msg.get('text', '')}"})

        elif mtype == "userlist":
            users = [u for u in msg.get("users", []) if u != self.username]
            for user in users:
                self._get_or_create_conversation(user, is_group=False)
            self._rebuild_sidebar()


def main():
    root = tk.Tk()
    ChatClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
