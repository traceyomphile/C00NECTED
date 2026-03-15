# C00NECTED

A networked real-time chat application developed for CSC3002F at the University of Cape Town. C00NECTED implements a client-server architecture with peer-to-peer (P2P) media transfer capabilities, supporting direct messaging, group chats, voice calls, file sharing, and offline message delivery.

---

## Features

- **User Authentication**: Register new accounts or log in with existing credentials. Passwords are salted and hashed with SHA-256 and enforced against strong-password rules.
- **Direct Messaging (DM)**: Send private timestamped messages to individual users in real time.
- **Group Messaging**: Create named groups, add/remove members, and broadcast messages to all members. System notifications are sent on membership changes.
- **P2P File Transfer**: Send images, audio, video, and PDF files directly to online users or groups over TCP. Files are transferred peer-to-peer without routing through the server.
- **Offline File Delivery**: Files sent to offline users are base64-encoded, stored in the server's SQLite database, and automatically delivered when the recipient comes back online.
- **Offline Message Queuing**: Text messages for offline users are queued in Redis and flushed to the client immediately upon login.
- **Voice Calls**: Real-time audio calls between users over UDP with NAT traversal via UDP hole-punching.
- **Voice Notes**: Record and send short voice notes from within the chat window.
- **Persistent Chat History**: Per-user chat history is cached locally as JSON with server-side sync, deduplication, and timestamp-ordered storage.
- **Presence & Last Seen**: Online/offline status tracked in Redis. Offline users display a human-readable "last seen X minutes ago" indicator.
- **Session Timeout**: Clients are automatically disconnected after 20 minutes of inactivity, with a warning sent before disconnection.
- **Thread-safe Server**: The server handles many concurrent clients using threading, WAL-mode SQLite, and fine-grained locking.

---

## Project Structure

```
C00NECTED/
├── ARCPServer.py       # Main TCP server — auth, routing, media upload/download
├── ChatServer.py       # Core server logic — presence, groups, messaging, persistence
├── ClientGUI.py        # Tkinter GUI client — splash, auth, chat, calls, file UI
├── network.py          # Client-side networking — TCP message loop, P2P file transfer, CallManager
├── history.py          # Local JSON chat history cache with deduplication and locking
├── infrastructure.py   # SQLite setup, Redis client, password hashing utilities
├── utils.py            # Shared constants, framing helpers, VoiceRecorder, colour/font tokens
└── chat_history/       # Auto-created at runtime — per-user JSON history files
    └── <username>.json
```

---

## Architecture

### Component Overview

```
┌──────────────┐     TCP (port 50000)      ┌────────────────────┐
│  ClientGUI   │◄─────────────────────────►│   ARCPServer.py    │
│  network.py  │                           │   ChatServer.py    │
│  history.py  │     UDP (dynamic port)    │   infrastructure   │
│              │◄──────────────────────────│                    │
│              │  P2P TCP (dynamic port)   │  SQLite + Redis    │
│              │◄──────────────────────────┤                    │
└──────────────┘                           └────────────────────┘
       │                                            │
       │         P2P TCP (peer ↔ peer)              │
       └────────────────────────────────────────────┘
              (file transfers bypass server)
```

### Communication Protocol (ARCP)

All TCP messages use a **9-byte length-prefixed frame**:

```
[ Type (1 ASCII char) ][ Length (8 decimal ASCII digits) ][ Payload ]
```

| Type | Purpose |
|------|---------|
| `A`  | Authentication messages (login, register, port registration) |
| `D`  | Data messages (chat content, file payloads) |
| `C`  | Control messages (peer info, call signalling, system status) |

### Transport Layer

| Channel | Protocol | Purpose |
|---------|----------|---------|
| Main control | TCP port 50000 | Auth, text chat, control commands |
| P2P file transfer | TCP (dynamic port) | Direct peer-to-peer file delivery |
| Voice calls | UDP (dynamic port) | Real-time audio streaming |
| Offline files | TCP via server | Base64-encoded, stored in SQLite |

### Key Data Stores

| Store | Technology | Contents |
|-------|-----------|----------|
| Users, groups, messages, media | SQLite (`arcp.db`) | Persistent storage across restarts |
| Presence & offline message queue | Redis (`fakeredis`) | Ephemeral session state |
| Local chat cache | JSON (`chat_history/<user>.json`) | Per-user client-side history |

---

## Module Reference

### `ARCPServer.py`
The entry point for the server. Accepts TCP connections, spawns a thread per client, and handles the full message lifecycle: authentication, framing, command parsing, media upload/download, group management, call signalling, and offline queue flushing.

### `ChatServer.py`
Stateless helper module used by the server. Manages the in-memory `clients` registry, Redis presence keys, group membership queries (via SQLite), DM and group message routing, and message persistence. Also provides `get_last_seen` and `format_last_seen` for offline status display.

### `ClientGUI.py`
The full Tkinter GUI. Contains `SplashScreen` (animated logo on startup), `AuthWindow` (login/register card), `ChatWindow` (main chat interface with sidebar, message bubbles, file and voice UI), and supporting dialogs. Processes all GUI events from the `gui_queue` populated by `NetworkClient`.

### `network.py`
Client-side networking layer. `NetworkClient` manages the main TCP socket, a media listener TCP socket, and a `CallManager` for UDP voice calls. Handles sending/receiving framed messages, P2P file transfers (both online and offline paths), and routes server events to the GUI via a thread-safe `queue.Queue`.

`CallManager` handles UDP socket creation, NAT hole-punching, per-call stop events, and audio send/receive threads.

### `history.py`
Thread-safe local chat history manager. Persists conversations per user as JSON with versioning. Supports append-with-deduplication, server-merge, last-fetched timestamp tracking, and known-group bookkeeping.

### `infrastructure.py`
Shared infrastructure layer. Provides `get_connection()` (SQLite with WAL mode and 15-second busy timeout), the `fakeredis` client, `hash_password()` (SHA-256 with a random 32-byte salt), `verify_password()` (timing-safe comparison), and `initialise_database()` (creates all tables and indexes on first run).

### `utils.py`
Shared constants and utilities used by both client modules. Contains colour tokens, font definitions, file-extension sets, `send_framed_msg` / `receive_framed_msg`, `parse_incoming_message`, and the `VoiceRecorder` class for microphone capture and WAV playback.

---

## Installation

### Prerequisites

- **Python 3.13** is required. PyAudio must be installed inside a virtual environment running Python 3.13 — it will not build correctly outside one on most systems.
- Git (optional, for cloning).

### Step 1 — Clone or download the project

```bash
git clone <repo-url>
cd C00NECTED
```

### Step 2 — Create a virtual environment with Python 3.13

```bash
python3.13 -m venv venv
```

> On Windows, if `python3.13` is not recognised, try:
> ```bash
> py -3.13 -m venv venv
> ```

### Step 3 — Activate the virtual environment

**macOS / Linux (bash/zsh):**
```bash
source venv/bin/activate
```

**Windows — Command Prompt:**
```cmd
venv\Scripts\activate.bat
```

**Windows — PowerShell:**

PowerShell blocks script execution by default. Before activating, run this **once per terminal session**:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```
Then activate:
```powershell
venv\Scripts\Activate.ps1
```

> The `-Scope Process` flag means this change only applies to the current PowerShell window and does not permanently alter your system policy.

Once activated, your terminal prompt will be prefixed with `(venv)`.

### Step 4 — Install dependencies

```bash
pip install fakeredis pyaudio Pillow
```

| Package | Purpose |
|---------|---------|
| `fakeredis` | In-process Redis emulation for the server's offline queue and presence tracking |
| `pyaudio` | Microphone input and audio playback for voice calls and voice notes |
| `Pillow` | Image rendering in the chat GUI |

> **PyAudio on macOS**: PortAudio must be installed first:
> ```bash
> brew install portaudio
> pip install pyaudio
> ```

> **PyAudio on Windows (if pip install fails)**: Use a pre-built wheel:
> ```bash
> pip install pipwin
> pipwin install pyaudio
> ```

### Step 5 — Verify the installation

```bash
python -c "import fakeredis, pyaudio, PIL; print('All dependencies OK')"
```

You should see `All dependencies OK` with no errors.

No further configuration is needed. The SQLite database (`arcp.db`), Redis instance, and `chat_history/` directory are all created automatically on first run.

---

## Usage

### Starting the Server

```bash
python ARCPServer.py
```

The server binds to the machine's local IP on port 50000 and prints a confirmation line:

```
Server listening on 192.168.x.x:50000
```

With Conda:
```bash
conda activate <your_env>
python ARCPServer.py
# or
conda run -n <your_env> python ARCPServer.py
```

### Running the Client

```bash
python ClientGUI.py
# Windows shortcut
py ClientGUI.py
```

With Conda:
```bash
conda activate <your_env>
python ClientGUI.py
```

On launch, the animated **C00NECTED** splash screen is displayed for 3 seconds while the client connects to the server in the background. If the connection fails, a retry screen is shown.

### Authentication

- **Login**: Enter your username and password on the login card.
- **Register**: Switch to the register tab, choose a username, and set a strong password. Passwords must be at least 8 characters and contain uppercase, lowercase, digits, and a special character.

### Sending Messages

- Select a contact or group from the sidebar to open a conversation.
- Type your message in the input bar and press **Enter** or click **Send**.
- Messages to offline users are queued and delivered automatically on their next login.

### File Transfer

- Click the **paperclip (📎)** button in the chat input bar.
- Select any file (image, audio, video, PDF, or other).
- Online recipients receive the file directly over a P2P TCP connection.
- Offline recipients receive it automatically when they next log in.

### Voice Notes

- Click and hold the **microphone (🎤)** button to record.
- Release to send. The recipient sees a playable voice note bubble.

### Voice Calls

- Click the **phone (📞)** button on a contact's chat header.
- The callee receives an incoming call dialog and can accept or reject.
- Audio streams in real time over UDP. Click **End Call** to hang up.

### Groups

| Action | How |
|--------|-----|
| Create group | Click **+** in the groups section of the sidebar |
| Add member | Open the group, click the settings icon, enter a username |
| Leave group | Open the group settings and click **Leave** |
| Send to group | Type in the group conversation normally |

---

## Security

- Passwords are hashed with **SHA-256** using a unique random 16-byte salt per password. The stored format is `salt_hex:hash_hex`.
- Password comparison uses `hmac.compare_digest` to prevent timing attacks.
- A list of 28 commonly weak passwords is explicitly rejected at registration.
- Passwords must meet: minimum length 8, uppercase, lowercase, digit, and special character requirements.
- Session inactivity timeout of **20 minutes** is enforced server-side.

---

## Known Issues & Fixes Applied

### Bug 1 — P2P received files were corrupted / could not be opened
**File**: `network.py` → `_handle_file_conn`

**Cause**: After reading the file header with `conn.makefile('rb').readline()`, the code switched back to `conn.recv()` for the body. The buffered `makefile` reader had already consumed bytes from the file body into its internal buffer, so `recv()` started mid-stream — producing a truncated, broken file.

**Fix**: Use the same `rfile` (the buffered reader) consistently for both the header and body reads.

---

### Bug 2 — Chat history was wiped on every app restart
**File**: `history.py` → `_save_nolock`

**Cause**: `data_to_save` was constructed with `_version` included, but `self._data` (without `_version`) was what actually got written to disk. On the next load the version check always failed, discarding the cache.

**Fix**: Pass `data_to_save` (not `self._data`) to `json.dump`.

---

### Bug 3 — Deadlock when syncing history from server
**File**: `history.py` → `merge_from_server`

**Cause**: `merge_from_server` held `self._lock` and then called `self.append()` and `self.set_last_fetched()`, both of which also tried to acquire the same non-reentrant lock — guaranteed deadlock.

**Fix**: Remove the outer lock from `merge_from_server` and rely on the individual locks already inside `append()` and `set_last_fetched()`.

---

### Bug 4 — `delete_chat` never actually removed a group from `known_groups`
**File**: `history.py` → `delete_chat`

**Cause**: The `known_groups` property returns `set(self._data.get('known_groups', []))` — a brand-new throwaway set on every access. Calling `.discard()` on it modified the throwaway set, not the underlying list in `_data`.

**Fix**: Operate directly on `self._data['known_groups']` (the real list) instead.

---

### Bug 5 — Splash screen never displayed on startup
**File**: `ClientGUI.py` → `App.__init__`

**Cause**: `self.net.connect()` (a blocking socket call with a 10-second timeout) was being called before `root.mainloop()` had started. Tkinter cannot render anything while the main thread is blocked, so the splash was either never drawn or shown too briefly to be seen.

**Fix**: Show the `SplashScreen` immediately (so the event loop can render it), and move `connect()` into a background daemon thread. The result is handed back to the main thread via `root.after(0, ...)`.

---

## Authors

- Karabo Nkambule, Tracey Letlape, Amahle Mbambo
- Developed as part of CSC3002F coursework at the University of Cape Town.
- Initial date: 2026-03-05

## License

This project is for educational purposes only.
