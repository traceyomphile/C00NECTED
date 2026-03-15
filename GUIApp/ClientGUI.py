#!/usr/bin/env python3
"""
ClientGUI.py - WhatsApp-inspired GUI client for C00NECTED chat system.
Integrates all networking from Client.py with a polished Tkinter UI.
"""

import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog
import threading
import queue
import time
import socket
import os
import re
import base64
import struct
import pickle
import pyaudio
import cv2
import json
import tempfile
from datetime import datetime
import wave as _wave
import random as _rnd, hashlib as _hs

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

SERVER_IP   = '196.47.192.177'
TCP_PORT    = 50000
MAX_VIDEO_SECONDS = 45

PKT_AUDIO   = b'\x01'
PKT_VIDEO   = b'\x02'
PKT_END     = b'\xFF'

AUDIO_FORMAT   = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_RATE     = 44100
AUDIO_CHUNK    = 1024

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.aac'}
VIDEO_EXTS = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
PDF_EXTS   = {'.pdf'}

# ─────────────────────────────────────────────────────────────────────────────
# COLORS & FONTS  — deep navy blue theme
# ─────────────────────────────────────────────────────────────────────────────

C_BG        = "#060D1A"   # near-black navy
C_SIDEBAR   = "#08142A"   # sidebar panel
C_PANEL     = "#0B1A2F"   # chat panel
C_HEADER    = "#0D2040"   # header bars
C_SENT      = "#0F2F6E"   # outgoing message bubble
C_RECV      = "#0A1A30"   # incoming message bubble
C_INPUT_BG  = "#091525"   # input field bg
C_INPUT_BAR = "#0F2440"   # Distinct input bar at bottom
C_ACCENT    = "#2563EB"   # primary blue accent
C_ACCENT_LT = "#3B82F6"   # lighter blue (hover/active)
C_GREEN     = "#2563EB"   # kept as alias so existing refs work
C_GREEN_LT  = "#3B82F6"
C_TEXT      = "#F0F4FF"   # primary text
C_SECONDARY = "#7B8FA6"   # muted/placeholder text
C_HOVER     = "#12284A"   # hover backgrounds
C_BORDER    = "#1E3A5F"   # dividers / borders
C_RED       = "#EF4444"   # error / danger
C_AMBER     = "#F59E0B"   # warnings
C_ONLINE    = "#22C55E"   # online presence dot
C_TICK_GREY = "#7B8FA6"   # delivered (grey) ticks
C_TICK_BLUE = "#60A5FA"   # read (blue) ticks

FONT_APP    = ("Segoe UI", 10)
FONT_BOLD   = ("Segoe UI", 10, "bold")
FONT_SMALL  = ("Segoe UI", 9)
FONT_MICRO  = ("Segoe UI", 8)
FONT_LARGE  = ("Segoe UI", 14, "bold")
FONT_LOGO   = ("Consolas", 36, "bold")
FONT_SUB    = ("Segoe UI", 11)

# Consistent window dimensions
WINDOW_WIDTH = 1100
WINDOW_HEIGHT = 720

# ─────────────────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def get_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXTS: return 'image'
    if ext in PDF_EXTS:   return 'pdf'
    if ext in AUDIO_EXTS: return 'audio'
    if ext in VIDEO_EXTS: return 'video'
    return 'unknown'

def send_framed_msg(sock: socket.socket, message: str, msg_type: str = 'D') -> None:
    data   = message.encode('ascii')
    header = f"{msg_type}{len(data):08d}".encode('ascii')
    sock.sendall(header + data)

def receive_framed_msg(sock: socket.socket):
    header = b''
    while len(header) < 9:
        chunk = sock.recv(9 - len(header))
        if not chunk:
            return None, None
        header += chunk
    msg_type = header[0:1].decode('ascii')
    msg_len  = int(header[1:9].decode('ascii'))
    data = b''
    while len(data) < msg_len:
        packet = sock.recv(msg_len - len(data))
        if not packet: break
        data += packet
    return msg_type, data.decode('ascii', errors='replace')

def parse_incoming_message(msg: str, my_username: str):
    """
    Parse a timestamped server message into structured data.
    Returns dict with keys: type, sender, content, group, timestamp, raw
    """
    # Group message: [ts] [group_id] sender: content
    gm = re.match(r'^\[(.+?)\] \[(.+?)\] (.+?): (.+)$', msg)
    if gm:
        ts, group, sender, content = gm.groups()
        return {'type': 'group', 'timestamp': ts, 'group': group,
                'sender': sender, 'content': content, 'raw': msg}

    # DM: [ts] [sender (DM)]: content
    dm = re.match(r'^\[(.+?)\] \[(.+?) \(DM\)\]: (.+)$', msg)
    if dm:
        ts, sender, content = dm.groups()
        return {'type': 'dm', 'timestamp': ts, 'sender': sender,
                'content': content, 'raw': msg}

    return {'type': 'system', 'content': msg, 'raw': msg}


# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE HISTORY  — persists per-user across sessions
# ─────────────────────────────────────────────────────────────────────────────

class HistoryStore:
    """
    Saves and loads chat history as a JSON file per user.
    File: chat_history/<username>.json
    Format: { chat_id: [msg_dict, ...], ... }
    Also persists which chat_ids are known groups.
    """

    HISTORY_DIR = "chat_history"

    def __init__(self, username: str):
        self.username = username
        os.makedirs(self.HISTORY_DIR, exist_ok=True)
        self._path = os.path.join(self.HISTORY_DIR, f"{username}.json")
        self._lock = threading.Lock()
        self._data = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def conversations(self) -> dict:
        return self._data.get('conversations', {})

    @property
    def known_groups(self) -> set:
        return set(self._data.get('known_groups', []))

    def append(self, chat_id: str, msg: dict):
        """Append one message dict and persist immediately."""
        with self._lock:
            convs = self._data.setdefault('conversations', {})
            convs.setdefault(chat_id, []).append(msg)
            self._save_nolock()

    def mark_group(self, chat_id: str):
        """Record that chat_id is a group."""
        with self._lock:
            groups = self._data.setdefault('known_groups', [])
            if chat_id not in groups:
                groups.append(chat_id)
                self._save_nolock()

    def ensure_chat(self, chat_id: str):
        """Create an empty conversation slot if it doesn't exist."""
        with self._lock:
            self._data.setdefault('conversations', {}).setdefault(chat_id, [])
            self._save_nolock()

    def delete_chat(self, chat_id: str):
        with self._lock:
            self._data.get('conversations', {}).pop(chat_id, None)
            try:
                self._data.get('known_groups', []).remove(chat_id)
            except ValueError:
                pass
            self._save_nolock()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_nolock(self):
        try:
            with open(self._path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# VOICE RECORDER  — record mic → WAV file, playback WAV
# ─────────────────────────────────────────────────────────────────────────────

class VoiceRecorder:
    """
    Records microphone input to a temporary WAV file.
    Usage:
        vr = VoiceRecorder()
        vr.start()
        ...
        path, duration = vr.stop()   # returns (filepath, seconds)
        vr.play(path)                # non-blocking playback in thread
    """

    RATE     = 44100
    CHANNELS = 1
    FORMAT   = pyaudio.paInt16
    CHUNK    = 1024

    def __init__(self):
        self._pa       = pyaudio.PyAudio()
        self._stream   = None
        self._frames   = []
        self._recording = False
        self._start_ts  = 0.0

    def start(self):
        if self._recording:
            return
        self._frames    = []
        self._recording = True
        self._start_ts  = time.time()
        self._stream = self._pa.open(
            format=self.FORMAT, channels=self.CHANNELS,
            rate=self.RATE, input=True,
            frames_per_buffer=self.CHUNK,
            stream_callback=self._callback
        )
        self._stream.start_stream()

    def _callback(self, in_data, frame_count, time_info, status):
        if self._recording:
            self._frames.append(in_data)
        return (None, pyaudio.paContinue)

    def stop(self) -> tuple:
        """Stop recording, write WAV, return (path, duration_seconds)."""
        if not self._recording:
            return None, 0
        self._recording = False
        duration = time.time() - self._start_ts

        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return None, 0

        fd, path = tempfile.mkstemp(suffix='.wav', prefix='c00n_voice_')
        os.close(fd)
        with _wave.open(path, 'wb') as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self._pa.get_sample_size(self.FORMAT))
            wf.setframerate(self.RATE)
            wf.writeframes(b''.join(self._frames))

        return path, round(duration, 1)

    @staticmethod
    def play(path: str):
        """Play a WAV file in a daemon thread (non-blocking)."""
        def _play():
            pa = pyaudio.PyAudio()
            try:
                with _wave.open(path, 'rb') as wf:
                    stream = pa.open(
                        format=pa.get_format_from_width(wf.getsampwidth()),
                        channels=wf.getnchannels(),
                        rate=wf.getframerate(),
                        output=True
                    )
                    data = wf.readframes(1024)
                    while data:
                        stream.write(data)
                        data = wf.readframes(1024)
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            finally:
                pa.terminate()
        threading.Thread(target=_play, daemon=True).start()

    def __del__(self):
        try:
            self._pa.terminate()
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# CALL MANAGER  — safe Queue-based UDP dispatcher
# ─────────────────────────────────────────────────────────────────────────────

class CallManager:
    """
    Manages real-time UDP audio/video calls using the Dispatcher Pattern.
    Only ONE thread ever reads from the UDP socket to prevent packet stealing.
    Packets are safely sorted into Queues for playback threads.
    """

    def __init__(self, gui_queue: queue.Queue):
        self.gui_queue = gui_queue
        self.udp_sock: socket.socket | None = None
        self.peer_addr: tuple | None = None
        self.call_ended = True
        self.call_type = 'audio'
        self.incoming_caller_addr: tuple | None = None
        
        # Thread-safe queues for media
        self.video_frame_queue = queue.Queue(maxsize=3)
        self._audio_queue = queue.Queue(maxsize=50)
        self._video_queue = queue.Queue(maxsize=50)
        
        self._call_done_event = threading.Event()
        self._hole_punched = threading.Event()

        # Add session variable
        self.session_id: bytes | None = None

    # ── Socket setup ──────────────────────────────────────────────────────────

    def create_udp_socket(self) -> int:
        if self.udp_sock:
            try: 
                self.udp_sock.close()
            except: 
                pass
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.bind(('0.0.0.0', 0))
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        return self.udp_sock.getsockname()[1]

    # ── Safe UDP Dispatcher Thread ────────────────────────────────────────────

    def start_dispatcher(self):
        """Starts the single central reader thread for all UDP packets."""
        threading.Thread(target=self._udp_dispatch_loop, daemon=True).start()

    def _udp_dispatch_loop(self):
        """Central UDP packet mailroom. It reads ALL packets and sorts them."""
        self.udp_sock.settimeout(1.0)
        while True:
            try:
                datagram, addr = self.udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                break

            # Ignore packets we accidentally sent to ourselves
            if addr == self.udp_sock.getsockname():
                continue

            if not datagram:
                continue

            if datagram in (b'PUNCH', b'PUNCH_ACK'):
                self.handle_punch_response(addr)
                continue

            pkt_type = datagram[0:1]
            if len(datagram) < 9:
                continue

            packet_session = datagram[1:9]

            if self.call_ended:
                # If we are not in a call, watch for incoming call packets
                if pkt_type in (PKT_AUDIO, PKT_VIDEO):
                    if self.incoming_caller_addr != addr:
                        self.incoming_caller_addr = addr
                        # Let the GUI know an incoming UDP call arrived
                        self.gui_queue.put(("INCOMING_CALL_UDP",))
            else:
                # We are in a call: Sort packets into their specific playback queues.
                # IMPORTANT: Only process packets from the current call peer to prevent
                # stale PKT_END packets from a previous call ending the new call.
                if addr != self.peer_addr:
                    continue
                if pkt_type == PKT_AUDIO:
                    if packet_session != self.session_id:
                        continue

                    if not self._audio_queue.full():
                        self._audio_queue.put_nowait(datagram[1:])
                elif pkt_type == PKT_END:
                    self.call_ended = True
                    self.gui_queue.put(("CALL_ENDED_REMOTE",))

    # ── Outgoing call (caller side) ───────────────────────────────────────────

    def start_outgoing_call(self, peer_ip: str, peer_udp_port: int, call_type: str = 'audio'):
        self._begin_call(call_type)
        self.peer_addr = (peer_ip, int(peer_udp_port))

        self._hole_punched.clear()
        threading.Thread(target=self._hole_punch_worker, daemon=True).start()

        if not self._hole_punched.wait(timeout=10.0):
            self.gui_queue.put(('STATUS', 'Call failed: NAT traversal timeout'))
            self.end_call()
            return
        
        self._start_media_threads(call_type)

    def _hole_punch_worker(self):
        punch_packet = b'PUNCH'
        while not self.call_ended and not self._hole_punched.is_set():
            if self.peer_addr:
                self.udp_sock.sendto(punch_packet, self.peer_addr)
                time.sleep(0.25)

    def handle_punch_response(self, addr):
        if not self._hole_punched.is_set():
            self._hole_punched.set()
            self.udp_sock.sendto(b'PUNCH_ACK', addr)

    def _start_media_threads(self, call_type: str):
        threading.Thread(target=self._stream_audio, daemon=True, name='audio-send').start()
        threading.Thread(target=self._recv_audio,   daemon=True, name='audio-recv').start()

    # ── Accepting an incoming call (callee side) ──────────────────────────────

    def accept_incoming_call(self, call_type: str = 'audio'):
        if not self.incoming_caller_addr:
            self.gui_queue.put(('STATUS', 'Call accept failed: no caller address'))
            return False

        self._begin_call(call_type)
        self.peer_addr = self.incoming_caller_addr

        # Start UDP dispatcher first
        #self._start_udp_dispatcher()

        # Send punch ACK to open our NAT to them
        self.udp_sock.sendto(b'PUNCH_ACK', self.peer_addr)

        self._start_media_threads(call_type)
        return True

    def _begin_call(self, call_type: str):
        self.call_ended = False
        self.call_type  = call_type
        self._hole_punched.clear()  # Reset for each new call (fixes callee re-call hole-punch)
        self.session_id = os.urandom(8)

    # ── End call ─────────────────────────────────────────────────────────────

    def end_call(self):
        self.call_ended = True
        self.session_id = None

        if self.udp_sock and self.peer_addr:
            try: 
                self.udp_sock.sendto(PKT_END, self.peer_addr)
            except: 
                pass
        self.peer_addr = None
        self.incoming_caller_addr = None

        # Flush stale packets out of the queues
        while not self._audio_queue.empty():
            try: self._audio_queue.get_nowait()
            except: break
        while not self._video_queue.empty():
            try: self._video_queue.get_nowait()
            except: break
        while not self.video_frame_queue.empty():
            try: self.video_frame_queue.get_nowait()
            except: break
        
        self._call_done_event.set()     # let the listener resume

    # ── Background listener ───────────────────────────────────────────────────

    def listen_for_incoming(self):
        """
        Runs forever as a daemon thread.
        - While no call is active: reads packets and detects new calls.
        - While a call is active: blocks on _call_done_event (doesn't touch
          the socket so call threads have exclusive access).
        """
        self.udp_sock.settimeout(1.0)
        while True:
            # Block here while a call is active — dispatcher owns the socket
            self._call_done_event.wait()

            try:
                datagram, addr = self.udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                break

            if not datagram:
                continue

            if datagram == b'PUNCH':
                self.udp_sock.sendto(b'PUNCH_ACK', addr)

            pkt = datagram[0:1]
            if pkt not in (PKT_AUDIO, PKT_VIDEO):
                continue

            # First meaningful packet from a caller — notify the GUI
            self.incoming_caller_addr = addr
            self._call_done_event.clear()   # pause listener until call ends
            self.gui_queue.put(('INCOMING_CALL_UDP',))
            # Listener is now paused; call threads will take over the socket

    # ── Audio threads ─────────────────────────────────────────────────────────

    def _stream_audio(self):
        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
                rate=AUDIO_RATE, input=True, frames_per_buffer=AUDIO_CHUNK
            )
            while not self.call_ended:
                pcm = stream.read(AUDIO_CHUNK, exception_on_overflow=False)
                if self.peer_addr:
                    packet = PKT_AUDIO + self.session_id + pcm
                    self.udp_sock.sendto(packet, self.peer_addr)
        except Exception as e:
            if not self.call_ended:
                self.gui_queue.put(('STATUS', f'[Call] Audio send error: {e}'))
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            pa.terminate()

    def _recv_audio(self):
        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
                rate=AUDIO_RATE, output=True, frames_per_buffer=AUDIO_CHUNK
            )
            while not self.call_ended:
                try:
                    # Safely pull ONLY audio packets from the mailroom queue
                    payload = self._audio_queue.get(timeout=0.5)
                    stream.write(payload)
                except queue.Empty:
                    continue
        except Exception as e:
            if not self.call_ended:
                self.gui_queue.put(('STATUS', f'[Call] Audio recv error: {e}'))
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            pa.terminate()

# ─────────────────────────────────────────────────────────────────────────────
# NETWORK CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class NetworkClient:
    """
    Handles all TCP communication with the ARCP server.
    Posts structured events to gui_queue for the main thread to consume.
    """

    def __init__(self, gui_queue: queue.Queue):
        self.gui_queue         = gui_queue
        self.tcp_sock          = None
        self.media_sock        = None
        self.call_manager      = CallManager(gui_queue)
        self.pending_transfers = {}
        self.username          = None
        self.shutting_down     = False
        self.current_call_type = 'audio'

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_sock.settimeout(10)
            self.tcp_sock.connect((SERVER_IP, TCP_PORT))
            self.tcp_sock.settimeout(None)
            return True
        except Exception as e:
            self.gui_queue.put(('CONNECT_ERROR', str(e)))
            return False

    # ── Auth ──────────────────────────────────────────────────────────────────

    def check_user(self, username: str) -> str:
        send_framed_msg(self.tcp_sock, f"CHECK:{username}", 'A')
        _, resp = receive_framed_msg(self.tcp_sock)
        return resp or "ERROR"

    def login(self, username: str, password: str) -> str:
        send_framed_msg(self.tcp_sock, f"LOGIN:{username}:{password}", 'A')
        _, resp = receive_framed_msg(self.tcp_sock)
        return resp or "ERROR"

    def register(self, username: str, password: str) -> str:
        send_framed_msg(self.tcp_sock, f"REG:{username}:{password}", 'A')
        _, resp = receive_framed_msg(self.tcp_sock)
        return resp or "ERROR"

    def post_auth_setup(self, username: str):
        """Register ports with server, start all background threads."""
        self.username = username

        self.media_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.media_sock.bind(('0.0.0.0', 0))
        self.media_sock.listen()
        media_port = self.media_sock.getsockname()[1]

        udp_port = self.call_manager.create_udp_socket()

        send_framed_msg(self.tcp_sock, f"PORT:{media_port}", 'A')
        send_framed_msg(self.tcp_sock, f"CALL_PORT:{udp_port}", 'A')

        threading.Thread(target=self._recv_tcp_media, daemon=True).start()
        threading.Thread(target=self._recv_tcp_messages, daemon=True).start()
        
        # Start the UDP Dispatcher mailroom
        self.call_manager.start_dispatcher()

        # Deliver any queued offline messages immediately
        send_framed_msg(self.tcp_sock, "FLUSH_OFFLINE:", 'C')

    # ── Messaging ─────────────────────────────────────────────────────────────

    def send_dm(self, recipient: str, text: str):
        send_framed_msg(self.tcp_sock, f"SEND:{recipient}:{text}", 'D')

    def send_group_msg(self, group_id: str, text: str):
        send_framed_msg(self.tcp_sock, f"SEND_GROUP:{group_id}:{text}", 'D')

    # ── File transfer ─────────────────────────────────────────────────────────

    def send_file(self, recipient: str, filepath: str):
        filename = os.path.basename(filepath)
        self.pending_transfers[recipient] = filepath
        send_framed_msg(self.tcp_sock, f"GET_PEER:{recipient}:{filename}", 'C')

    # ── Groups ────────────────────────────────────────────────────────────────

    def create_group(self, group_name: str):
        send_framed_msg(self.tcp_sock, f"CREATE_GROUP:{group_name}:", 'C')

    def add_to_group(self, group_name: str, target_user: str):
        send_framed_msg(self.tcp_sock, f"ADD_TO_GROUP:{group_name}:{target_user}", 'C')

    def leave_group(self, group_name: str):
        send_framed_msg(self.tcp_sock, f"LEAVE_GROUP:{group_name}:", 'C')

    def verify_group(self, group_name: str):
        """Probe the server to check whether this client is a member of group_name.
        The server responds with ADD_STATUS:... (member/exists) or
        GROUP NOT FOUND OR NOT MEMBER (doesn't exist / not a member).
        """
        send_framed_msg(self.tcp_sock, f"ADD_TO_GROUP:{group_name}:{self.username}", 'C')

    # ── Calls ─────────────────────────────────────────────────────────────────

    def request_call(self, recipient: str, call_type: str):
        self.current_call_type = call_type
        send_framed_msg(self.tcp_sock, f"{call_type.upper()}_CALL:{recipient}", 'C')

    def accept_call(self, caller: str):
        send_framed_msg(self.tcp_sock, f"CALL_ACCEPT:{caller}", 'C')

    def reject_call(self, caller: str):
        send_framed_msg(self.tcp_sock, f"CALL_REJECT:{caller}", 'C')

    def end_call(self):
        self.call_manager.end_call()

    # ── Disconnect ────────────────────────────────────────────────────────────

    def disconnect(self):
        self.shutting_down = True
        try: 
            send_framed_msg(self.tcp_sock, "EXIT:", 'C')
        except: 
            pass

        for s in [self.tcp_sock, self.media_sock]:
            try: 
                s.shutdown(socket.SHUT_RDWR)
                s.close()
            except: 
                pass

        try: 
            self.call_manager.udp_sock.close()
        except: 
            pass

    # ── Background receive loop ───────────────────────────────────────────────

    def _recv_tcp_messages(self):
        while True:
            try:
                msg_type, msg = receive_framed_msg(self.tcp_sock)
                if not msg:
                    if not self.shutting_down:
                        self.gui_queue.put(('DISCONNECTED', 'Connection lost'))
                    break

                # ── P2P file: single user ──
                if msg_type == 'C' and msg.startswith("PEER_INFO:"):
                    _, target_user, t_ip, t_port = msg.split(':', 3)
                    if target_user in self.pending_transfers:
                        fp = self.pending_transfers.pop(target_user)
                        threading.Thread(
                            target=self._tcp_send_file,
                            args=(fp, t_ip, int(t_port), target_user),
                            daemon=True
                        ).start()
                    continue

                # ── P2P file: group ──
                if msg_type == 'C' and msg.startswith("GROUP_PEER_INFO:"):
                    _, group_name, peers_str = msg.split(':', 2)
                    if group_name in self.pending_transfers:
                        fp = self.pending_transfers.pop(group_name)
                        for peer_str in peers_str.split('|'):
                            ip, port = peer_str.split(',')
                            threading.Thread(
                                target=self._tcp_send_file,
                                args=(fp, ip, int(port), group_name),
                                daemon=True
                            ).start()
                    continue

                # ── Store for offline user ──
                if msg_type == 'C' and msg.startswith("STORE_OFFLINE:"):
                    target = msg.split(':', 2)[1]
                    fp = self.pending_transfers.get(target)
                    if fp:
                        threading.Thread(
                            target=self._upload_offline,
                            args=(target, fp),
                            daemon=True
                        ).start()
                    continue

                # ── Call peer info (caller gets callee's UDP addr) ──
                if msg_type == 'C' and msg.startswith("CALL_PEER_INFO:"):
                    _, peer_user, peer_ip, peer_udp_port = msg.split(":")
                    self.call_manager.start_outgoing_call(peer_ip, peer_udp_port, self.current_call_type)
                    self.gui_queue.put(('CALL_RINGING', peer_user))
                    continue

                if msg_type == 'C' and msg.startswith("CALL_ACCEPTED:"):
                    callee = msg.split(":")[1]
                    self.gui_queue.put(('CALL_ACCEPTED', callee))
                    continue

                if msg_type == 'C' and msg.startswith("CALL_REJECTED:"):
                    callee = msg.split(":")[1]
                    self.call_manager.end_call()
                    self.gui_queue.put(('CALL_REJECTED', callee))
                    continue

                # ── Incoming call notification ──
                if msg.startswith("AUDIO_CALL:"):
                    parts     = msg.split(":")
                    caller    = parts[1]
                    call_type = "audio"
                    
                    # Server now includes caller's IP and UDP port in the notification.
                    if len(parts) >= 4:
                        caller_ip       = parts[2]
                        caller_udp_port = int(parts[3])
                        self.call_manager.incoming_caller_addr = (caller_ip, caller_udp_port)
                    self.gui_queue.put(('INCOMING_CALL', caller, call_type))
                    continue

                if msg in {'GROUP CREATED', 'GROUP EXISTS', 'LEFT GROUP'}:
                    continue
                if msg == 'GROUP NOT FOUND OR NOT MEMBER':
                    self.gui_queue.put(('GROUP_NOT_FOUND',))
                    continue

                if msg.startswith(("ADD_STATUS:", "CALLING:", "MEDIA_ID:",
                                   "ERROR:", "TIMEOUT")):
                    self.gui_queue.put(('STATUS', msg))
                    if msg.startswith("TIMEOUT"):
                        self.gui_queue.put(('TIMEOUT', msg))
                    continue

                if msg_type == 'C' and msg.startswith("CALLING:"):
                    self.gui_queue.put(('CALL_OFFLINE', msg.split(":", 1)[1]))
                    continue

                # ── Session timeout ──
                if msg_type == 'C' and msg.startswith("TIMEOUT"):
                    self.gui_queue.put(('TIMEOUT', msg))
                    break

                # ── Offline media notification ──
                if msg.startswith("MEDIA_WAITING:"):
                    parts = msg.split(":", 3)
                    media_id, sender, filename = parts[1], parts[2], parts[3]
                    self.gui_queue.put(('STATUS', f'📥 Downloading offline file "{filename}" from {sender}...'))
                    send_framed_msg(self.tcp_sock, f"DOWNLOAD_MEDIA:{media_id}:", 'C')
                    continue

                # ── Base64 file download ──
                if msg_type == 'D' and msg.startswith("FILE:"):
                    parts = msg.split(":", 4)
                    if len(parts) >= 4:
                        filename  = parts[1]
                        ftype  = parts[2]
                        b64    = parts[3]
                        sender = parts[4] if len(parts) == 5 else None
                        self._save_b64_file(filename, ftype, b64, sender)
                    continue

                # ── All other messages (DMs, group msgs, system msgs) ──
                self.gui_queue.put(('MESSAGE', msg, msg_type))

            except Exception as e:
                if not self.shutting_down:
                    self.gui_queue.put(('DISCONNECTED', str(e)))
                break

    def _tcp_send_file(self, filepath: str, target_ip: str, target_port: int, recipient: str):
        try:
            filename    = os.path.basename(filepath)
            filesize = os.path.getsize(filepath)

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((target_ip, target_port))

            sock.sendall(f"FILE:{filename}:{filesize}:{self.username}\n".encode('utf-8'))
            with open(filepath, 'rb') as f:
                while chunk := f.read(4096):
                    sock.sendall(chunk)

            sock.close()
            self.gui_queue.put(('FILE_SENT', filename, recipient))

        except Exception as e:
            self.gui_queue.put(('STATUS', f'❌ File transfer failed: {e}'))
        finally:
            self.pending_transfers.pop(recipient, None)

    def _upload_offline(self, recipient: str, filepath: str):
        try:
            filename  = os.path.basename(filepath)
            ftype  = get_file_type(filepath)

            with open(filepath, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
            send_framed_msg(self.tcp_sock, f"UPLOAD_MEDIA:{recipient}:{filename}|{ftype}|{b64}", 'D')
            self.gui_queue.put(('STATUS', f'📤 "{filename}" queued for {recipient} (offline)'))

        except Exception as e:
            self.gui_queue.put(('STATUS', f'❌ Offline upload failed: {e}'))
        finally:
            self.pending_transfers.pop(recipient, None)

    def _recv_tcp_media(self):
        os.makedirs("received", exist_ok=True)
        while True:
            try:
                conn, addr = self.media_sock.accept()
                threading.Thread(
                    target=self._handle_file_conn,
                    args=(conn, addr), daemon=True
                ).start()
            except Exception:
                break

    def _handle_file_conn(self, conn: socket.socket, addr):
        try:
            rfile  = conn.makefile('rb')
            raw    = rfile.readline(1024)
            if not raw:
                return

            header = raw.rstrip(b'\n').decode('utf-8', errors='replace')
            parts  = header.split(":")

            if len(parts) < 3 or parts[0] != 'FILE':
                return

            filename = parts[1]
            filesize = int(parts[2])
            sender   = parts[3] if len(parts) >= 4 else None

            save_path = self._unique_path(os.path.join("received", filename))
            received  = 0

            with open(save_path, 'wb') as f:
                while received < filesize:
                    chunk = conn.recv(min(4096, filesize - received))
                    if not chunk: 
                        break

                    f.write(chunk); received += len(chunk)
            self.gui_queue.put(('FILE_RECEIVED', filename, get_file_type(filename), save_path, sender))
        
        except Exception:
            pass
        finally:
            conn.close()

    def _save_b64_file(self, filename: str, ftype: str, b64: str, sender: str = None):
        try:
            os.makedirs("received", exist_ok=True)
            save_path = self._unique_path(os.path.join("received", filename))
            with open(save_path, 'wb') as f:
                f.write(base64.b64decode(b64))
            self.gui_queue.put(('FILE_RECEIVED', filename, ftype, save_path, sender))
        except Exception:
            pass

    @staticmethod
    def _unique_path(path: str) -> str:
        base, ext = os.path.splitext(path)
        n = 1
        while os.path.exists(path):
            path = f"{base}_{n}{ext}"
            n += 1
        return path


# ─────────────────────────────────────────────────────────────────────────────
# SPLASH SCREEN
# ─────────────────────────────────────────────────────────────────────────────

class SplashScreen:
    """
    Full-window splash with the C00NECTED logo.
    The two '0' characters are replaced by hand-drawn router icons.
    Shows for 3 seconds then calls on_done().
    """

    def __init__(self, root: tk.Tk, on_done):
        self.root    = root
        self.on_done = on_done
        self._build()
        root.after(3000, self._finish)

    def _build(self):
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.frame = tk.Frame(self.root, bg=C_BG)
        self.frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.canvas = tk.Canvas(self.frame, bg=C_BG, highlightthickness=0)
        self.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.frame.bind("<Configure>", lambda _e: self._redraw())
        self.root.after(50, self._redraw)   

    def _redraw(self):
        self.canvas.delete("all")
        W = self.canvas.winfo_width()
        H = self.canvas.winfo_height()
        if W < 10 or H < 10:
            return

        cy = int(H * 0.42)
        font_size = max(40, min(96, int(W * 0.085)))
        font      = ("Consolas", font_size, "bold")

        chars = [
            ("C", C_TEXT),
            ("0", C_GREEN),
            ("0", C_GREEN),
            ("N", C_TEXT),
            ("E", C_TEXT),
            ("C", C_TEXT),
            ("T", C_TEXT),
            ("E", C_TEXT),
            ("D", C_TEXT),
        ]
        spacing = int(font_size * 0.72)   
        total_w = spacing * (len(chars) - 1)
        x_start = (W - total_w) // 2

        for i, (ch, col) in enumerate(chars):
            self.canvas.create_text(
                x_start + i * spacing, cy,
                text=ch, font=font, fill=col, anchor='center'
            )

        line_y = cy + int(font_size * 0.60)
        margin = int(W * 0.06)
        self.canvas.create_line(
            margin, line_y, W - margin, line_y,
            fill=C_GREEN, width=2, dash=(10, 7)
        )

        self.canvas.create_text(
            W // 2, line_y + int(H * 0.07),
            text="connect  •  chat  •  call",
            font=("Segoe UI", max(10, int(font_size * 0.18))),
            fill=C_SECONDARY, anchor='center'
        )

        dot_char = "●" * (getattr(self, '_dot_n', 0) % 4)
        self._dot_id = self.canvas.create_text(
            W // 2, line_y + int(H * 0.15),
            text=dot_char,
            font=("Segoe UI", max(10, int(font_size * 0.18))),
            fill=C_GREEN, anchor='center'
        )
        self._animate_dots(getattr(self, '_dot_n', 0))

    def _animate_dots(self, n: int):
        self._dot_n = n + 1
        dot_char = "●" * (n % 4)
        try:
            if hasattr(self, '_dot_id'):
                self.canvas.itemconfig(self._dot_id, text=dot_char)
        except tk.TclError:
            return
        self.root.after(500, self._animate_dots, n + 1)

    def _finish(self):
        self.frame.destroy()
        self.on_done()


# ─────────────────────────────────────────────────────────────────────────────
# AUTH WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class AuthWindow:
    """Login / register — styled to match the deep-navy blue design."""

    CARD_W = 400

    def __init__(self, root: tk.Tk, net: NetworkClient, on_success):
        self.root       = root
        self.net        = net
        self.on_success = on_success
        self._entries   = {}   
        self._build_login()

    def _clear(self):
        for w in self.root.winfo_children():
            w.destroy()
        self._entries = {}

    def _bg_frame(self) -> tk.Frame:
        f = tk.Frame(self.root, bg=C_BG)
        f.place(relx=0, rely=0, relwidth=1, relheight=1)
        return f

    def _card(self, bg_frame) -> tk.Frame:
        card = tk.Frame(bg_frame, bg=C_SIDEBAR, padx=32, pady=36)
        card.place(relx=0.5, rely=0.5, anchor='center', width=self.CARD_W)
        return card

    def _rounded_field(self, parent, placeholder: str, show: str = '') -> tk.StringVar:
        var   = tk.StringVar()
        outer = tk.Frame(parent, bg=C_SIDEBAR)
        outer.pack(fill='x', pady=(0, 12))

        cv = tk.Canvas(outer, bg=C_SIDEBAR, highlightthickness=0,
                       height=48, bd=0)
        cv.pack(fill='x')

        def _draw_bg(cv=cv):
            cv.delete('bg')
            w, h = cv.winfo_width() or self.CARD_W - 64, cv.winfo_height()
            r = 10
            cv.create_arc( 0,  0, 2*r, 2*r, start= 90, extent= 90, fill=C_HEADER, outline='', tags='bg')
            cv.create_arc(w-2*r, 0, w, 2*r, start=  0, extent= 90, fill=C_HEADER, outline='', tags='bg')
            cv.create_arc( 0, h-2*r, 2*r, h, start=180, extent= 90, fill=C_HEADER, outline='', tags='bg')
            cv.create_arc(w-2*r, h-2*r, w, h, start=270, extent= 90, fill=C_HEADER, outline='', tags='bg')
            cv.create_rectangle(r, 0, w-r, h, fill=C_HEADER, outline='', tags='bg')
            cv.create_rectangle(0, r, w, h-r, fill=C_HEADER, outline='', tags='bg')
            cv.create_arc( 0,  0, 2*r, 2*r, start= 90, extent= 90, outline=C_BORDER, tags='bg')
            cv.create_arc(w-2*r, 0, w, 2*r, start=  0, extent= 90, outline=C_BORDER, tags='bg')
            cv.create_arc( 0, h-2*r, 2*r, h, start=180, extent= 90, outline=C_BORDER, tags='bg')
            cv.create_arc(w-2*r, h-2*r, w, h, start=270, extent= 90, outline=C_BORDER, tags='bg')
            cv.create_line(r, 0, w-r, 0, fill=C_BORDER, tags='bg')
            cv.create_line(r, h, w-r, h, fill=C_BORDER, tags='bg')
            cv.create_line(0, r, 0, h-r, fill=C_BORDER, tags='bg')
            cv.create_line(w, r, w, h-r, fill=C_BORDER, tags='bg')

        cv.bind('<Configure>', lambda _e: _draw_bg())
        cv.after(10, _draw_bg)

        entry = tk.Entry(
            cv, textvariable=var, show=show,
            bg=C_HEADER, fg=C_TEXT, insertbackground=C_TEXT,
            relief='flat', font=("Segoe UI", 11), bd=0,
            disabledbackground=C_HEADER
        )
        entry.insert(0, placeholder)
        entry.config(fg=C_SECONDARY)

        def _on_focus_in(_e, e=entry, p=placeholder, v=var):
            if e.get() == p:
                e.delete(0, 'end')
                e.config(fg=C_TEXT, show=show)
        
        def _on_focus_out(_e, e=entry, p=placeholder):
            if not e.get():
                e.insert(0, p)
                e.config(fg=C_SECONDARY, show='')

        entry.bind('<FocusIn>',  _on_focus_in)
        entry.bind('<FocusOut>', _on_focus_out)

        cv.create_window(16, 24, anchor='w', window=entry, width=self.CARD_W - 96)

        self._entries[placeholder] = (var, entry)
        return var

    def _get_field(self, placeholder: str) -> str:
        var, _ = self._entries.get(placeholder, (None, None))
        if var is None: 
            return ''
        val = var.get().strip()
        return '' if val == placeholder else val

    def _blue_btn(self, parent, text: str, cmd, outline=False) -> tk.Button:
        bg  = C_SIDEBAR if outline else C_ACCENT
        fg  = C_ACCENT  if outline else C_TEXT
        btn = tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg,
            activebackground=C_ACCENT_LT, activeforeground=C_TEXT,
            relief='flat', font=("Segoe UI", 11, "bold"),
            cursor='hand2', bd=2 if outline else 0,
            highlightbackground=C_ACCENT if outline else bg,
            highlightthickness=2 if outline else 0,
        )
        btn.pack(fill='x', ipady=11, pady=(0, 10))
        return btn

    def _err_label(self, parent) -> tk.Label:
        lbl = tk.Label(parent, text='', font=("Segoe UI", 9),
                       fg=C_RED, bg=C_SIDEBAR,
                       wraplength=self.CARD_W - 64, justify='center')
        lbl.pack(pady=(0, 6))
        return lbl

    def _build_login(self):
        self._clear()
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        bg  = self._bg_frame()
        card = self._card(bg)

        tk.Label(card, text="Log in",
                 font=("Segoe UI", 26, "bold"), fg=C_TEXT, bg=C_SIDEBAR
                 ).pack(anchor='w', pady=(0, 22))

        self._rounded_field(card, "Username")
        self._rounded_field(card, "Password", show='●')

        self._login_err = self._err_label(card)
        self._blue_btn(card, "Log in", self._do_login)

        div = tk.Frame(card, bg=C_SIDEBAR)
        div.pack(fill='x', pady=(4, 12))
        tk.Frame(div, bg=C_BORDER, height=1).pack(side='left',  fill='x', expand=True, pady=8)
        tk.Label(div, text=" Or ", font=("Segoe UI", 9),
                 fg=C_SECONDARY, bg=C_SIDEBAR).pack(side='left')
        tk.Frame(div, bg=C_BORDER, height=1).pack(side='left',  fill='x', expand=True, pady=8)

        self._blue_btn(card, "Sign up", self._build_register, outline=True)

        self.root.bind("<Return>", lambda _e: self._do_login())

    def _do_login(self):
        username = self._get_field("Username")
        password = self._get_field("Password")

        if not username or not password:
            self._login_err.config(text="Username and password are required.")
            return

        check = self.net.check_user(username)
        if check == "NOT_FOUND":
            self._login_err.config(text="Username not found.")
            return
        if check not in ("EXISTS", "NOT_FOUND"):
            self._login_err.config(text=f"Connection error: {check}")
            return

        resp = self.net.login(username, password)
        if resp == "SUCCESS":
            self.root.unbind("<Return>")
            self.net.post_auth_setup(username)
            self.on_success(username)
        elif resp == "ALREADY ONLINE":
            self._login_err.config(text="Already logged in elsewhere.")
        else:
            self._login_err.config(text="Incorrect password.")

    def _build_register(self):
        self._clear()
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        bg   = self._bg_frame()
        card = self._card(bg)

        tk.Label(card, text="Sign up",
                 font=("Segoe UI", 26, "bold"), fg=C_TEXT, bg=C_SIDEBAR
                 ).pack(anchor='w', pady=(0, 22))

        self._rounded_field(card, "Username")
        self._rounded_field(card, "Password", show='●')
        self._rounded_field(card, "Confirm password", show='●')

        tk.Label(card,
                 text="Min 8 chars · uppercase · lowercase · digit · special",
                 font=("Segoe UI", 8), fg=C_SECONDARY, bg=C_SIDEBAR
                 ).pack(anchor='w', pady=(0, 8))

        self._reg_err = self._err_label(card)
        self._blue_btn(card, "Create account", self._do_register)

        div = tk.Frame(card, bg=C_SIDEBAR)
        div.pack(fill='x', pady=(4, 12))
        tk.Frame(div, bg=C_BORDER, height=1).pack(side='left', fill='x', expand=True, pady=8)
        tk.Label(div, text=" Or ", font=("Segoe UI", 9),
                 fg=C_SECONDARY, bg=C_SIDEBAR).pack(side='left')
        tk.Frame(div, bg=C_BORDER, height=1).pack(side='left', fill='x', expand=True, pady=8)

        self._blue_btn(card, "Back to Log in", self._build_login, outline=True)

        self.root.bind("<Return>", lambda _e: self._do_register())

    def _do_register(self):
        username = self._get_field("Username")
        password = self._get_field("Password")
        confirm  = self._get_field("Confirm password")

        if not username or not password or not confirm:
            self._reg_err.config(text="All fields are required.")
            return
        if password != confirm:
            self._reg_err.config(text="Passwords do not match.")
            return

        check = self.net.check_user(username)
        if check == "EXISTS":
            self._reg_err.config(text="Username already taken.")
            return

        resp = self.net.register(username, password)
        if resp == "SUCCESS":
            self.root.unbind("<Return>")
            self.net.post_auth_setup(username)
            self.on_success(username)
        elif resp == "USER_EXISTS":
            self._reg_err.config(text="Username taken — try another.")
        elif resp and resp.startswith("WEAK_PASSWORD:"):
            self._reg_err.config(text=resp.split(":", 1)[1])
        else:
            self._reg_err.config(text=f"Registration failed: {resp}")


# ─────────────────────────────────────────────────────────────────────────────
# CALL WINDOW  — shown during an active call
# ─────────────────────────────────────────────────────────────────────────────

class CallWindow(tk.Toplevel):
    """
    Shows during an active call.
    - Audio call: avatar + timer + mute/end buttons.
    - Video call: remote video feed + local PiP + controls.
    """

    def __init__(self, root, net: NetworkClient, peer: str, call_type: str, on_end):
        super().__init__(root)
        self.net       = net
        self.peer      = peer
        self.call_type = call_type
        self.on_end    = on_end
        self._start_ts = time.time()
        self._running  = True
        self._muted    = False
        self._local_cap = None

        self.title(f"{'🎥' if call_type == 'video' else '🎙️'} Call with {peer}")
        self.configure(bg=C_HEADER)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._end_call)

        if call_type == 'video':
            self.geometry("700x560")
            self._build_video_ui()
        else:
            self.geometry("360x420")
            self._build_audio_ui()

        self._tick_timer()

    def _build_audio_ui(self):
        tk.Label(self, text="🎙️  Audio Call", font=("Segoe UI", 12, "bold"),
                 fg=C_SECONDARY, bg=C_HEADER).pack(pady=(24, 4))

        c = tk.Canvas(self, width=120, height=120, bg=C_HEADER, highlightthickness=0)
        c.pack(pady=20)
        c.create_oval(5, 5, 115, 115, fill=C_GREEN, outline='')
        initial = self.peer[0].upper() if self.peer else '?'
        c.create_text(60, 60, text=initial, font=("Segoe UI", 46, "bold"), fill='#000')

        tk.Label(self, text=self.peer, font=("Segoe UI", 18, "bold"),
                 fg=C_TEXT, bg=C_HEADER).pack()
        self.status_lbl = tk.Label(self, text="Connecting…",
                                   font=FONT_SMALL, fg=C_GREEN, bg=C_HEADER)
        self.status_lbl.pack(pady=4)
        self.timer_lbl  = tk.Label(self, text="00:00",
                                   font=("Consolas", 22), fg=C_TEXT, bg=C_HEADER)
        self.timer_lbl.pack(pady=12)

        self._build_controls(self)

    def update_status(self, text: str):
        if self._running:
            self.status_lbl.config(text=text)

    def _build_video_ui(self):
        top = tk.Frame(self, bg=C_HEADER, height=30)
        top.pack(fill='x')
        tk.Label(top, text=f"🎥  Video Call  ·  {self.peer}",
                 font=FONT_BOLD, fg=C_SECONDARY, bg=C_HEADER).pack(side='left', padx=14, pady=6)
        self.timer_lbl = tk.Label(top, text="00:00", font=("Consolas", 11),
                                  fg=C_TEXT, bg=C_HEADER)
        self.timer_lbl.pack(side='right', padx=14)

        self.remote_canvas = tk.Canvas(self, width=700, height=440,
                                       bg='#000', highlightthickness=0)
        self.remote_canvas.pack()
        self._no_vid_id = self.remote_canvas.create_text(
            350, 220, text="Waiting for video…", font=FONT_SUB, fill=C_SECONDARY
        )

        self.local_canvas = tk.Canvas(self, width=140, height=105,
                                      bg='#111', highlightthickness=0)
        self.local_canvas.place(x=545, y=320)

        self._build_controls(self)
        self._poll_remote_video()
        if PIL_AVAILABLE:
            self._capture_local_video()

    def _build_controls(self, parent):
        row = tk.Frame(parent, bg=C_HEADER)
        row.pack(pady=20)

        self.mute_btn = tk.Button(
            row, text="🎤", font=("Segoe UI", 18),
            bg=C_INPUT_BG, fg=C_TEXT, relief='flat', width=4,
            cursor='hand2', command=self._toggle_mute
        )
        self.mute_btn.pack(side='left', padx=10)

        tk.Button(
            row, text="📵", font=("Segoe UI", 18),
            bg=C_RED, fg='white', relief='flat', width=4,
            cursor='hand2', command=self._end_call
        ).pack(side='left', padx=10)

    def _toggle_mute(self):
        self._muted = not self._muted
        self.mute_btn.config(text="🔇" if self._muted else "🎤",
                             bg=C_AMBER if self._muted else C_INPUT_BG)

    def _end_call(self):
        self._running = False
        self.net.end_call()

        if self._local_cap:
            try: 
                self._local_cap.release()
            except: 
                pass
        self.on_end()
        self.destroy()

    def _tick_timer(self):
        if not self._running: 
            return
        elapsed = int(time.time() - self._start_ts)
        m, s = divmod(elapsed, 60)
        self.timer_lbl.config(text=f"{m:02d}:{s:02d}")
        self.after(1000, self._tick_timer)

    def _poll_remote_video(self):
        if not self._running: return
        try:
            frame = self.net.call_manager.video_frame_queue.get_nowait()
            if PIL_AVAILABLE:
                img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                img = img.resize((700, 440), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.remote_canvas.delete(self._no_vid_id)
                self.remote_canvas.create_image(0, 0, anchor='nw', image=photo)
                self.remote_canvas._photo = photo  
        except queue.Empty:
            pass
        self.after(30, self._poll_remote_video)

    def _capture_local_video(self):
        self._local_cap = cv2.VideoCapture(0)
        threading.Thread(target=self._local_video_thread, daemon=True).start()

    def _local_video_thread(self):
        self._local_frames: queue.Queue = queue.Queue(maxsize=2)
        while self._running:
            ret, frame = self._local_cap.read()
            if not ret: 
                break
            
            if not self._local_frames.full():
                self._local_frames.put_nowait(frame)
            time.sleep(0.033)
        self._local_cap.release()

    def poll_local_pip(self):
        if not self._running or not hasattr(self, '_local_frames'): return
        try:
            frame = self._local_frames.get_nowait()
            img   = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            img   = img.resize((140, 105), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.local_canvas.create_image(0, 0, anchor='nw', image=photo)
            self.local_canvas._photo = photo
        except (queue.Empty, AttributeError):
            pass
        self.after(33, self.poll_local_pip)


# ─────────────────────────────────────────────────────────────────────────────
# INCOMING CALL DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class IncomingCallDialog(tk.Toplevel):
    def __init__(self, root, caller: str, call_type: str, on_accept, on_reject):
        super().__init__(root)
        self.on_accept = on_accept
        self.on_reject = on_reject

        icon = "🎥" if call_type == "video" else "🎙️"
        self.title(f"Incoming {call_type} call")
        self.configure(bg=C_HEADER)
        self.geometry("320x200")
        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._reject)

        tk.Label(self, text=f"{icon}  Incoming {call_type} call",
                 font=FONT_BOLD, fg=C_SECONDARY, bg=C_HEADER).pack(pady=(20, 8))
        tk.Label(self, text=caller, font=("Segoe UI", 20, "bold"),
                 fg=C_TEXT, bg=C_HEADER).pack()
        tk.Label(self, text="is calling you…",
                 font=FONT_SMALL, fg=C_SECONDARY, bg=C_HEADER).pack(pady=(4, 20))

        row = tk.Frame(self, bg=C_HEADER)
        row.pack()
        tk.Button(row, text="✅  Accept", bg=C_GREEN, fg='#000',
                  font=FONT_BOLD, relief='flat', padx=18, pady=8,
                  cursor='hand2', command=self._accept).pack(side='left', padx=12)
        tk.Button(row, text="❌  Decline", bg=C_RED, fg='white',
                  font=FONT_BOLD, relief='flat', padx=18, pady=8,
                  cursor='hand2', command=self._reject).pack(side='left', padx=12)

        self._ring_index = 0
        self._ring()

    def _ring(self):
        icons = ["🔔", "🔕"]
        try:
            self.title(icons[self._ring_index % 2] + " Incoming call…")
        except tk.TclError:
            return
        self._ring_index += 1
        self.after(600, self._ring)

    def _accept(self):
        self.destroy()
        self.on_accept()

    def _reject(self):
        self.destroy()
        self.on_reject()


# ─────────────────────────────────────────────────────────────────────────────
# CHAT WINDOW  — main interface
# ─────────────────────────────────────────────────────────────────────────────

class ChatWindow:
    def __init__(self, root: tk.Tk, net: NetworkClient, username: str,
                 gui_queue: queue.Queue):
        self.root       = root
        self.net        = net
        self.username   = username
        self.gui_queue  = gui_queue

        self.history    = HistoryStore(username)

        self.conversations: dict[str, list] = {
            k: list(v) for k, v in self.history.conversations.items()
        }
        self.known_groups: set[str]         = self.history.known_groups
        self.current_chat: str | None       = None
        self.active_call_window: CallWindow | None = None
        self._pending_call_info: tuple | None   = None
        self._groups_to_verify:   list          = []    
        self._verifying_group:    str | None    = None  

        self.unread_counts: dict[str, int]  = {}
        self._tick_labels: dict[str, tk.Label] = {}
        self._voice_rec   = VoiceRecorder()
        self._is_recording = False

        if 'SYSTEM' in self.conversations:
            del self.conversations['SYSTEM']
            self.history.delete_chat('SYSTEM')

        self._clear_root()
        self._build_layout()
        self._update_chat_list()   
        self._start_event_loop()
        
        self.root.after(200, self._start_group_purge)

    def _clear_root(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=C_BG)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w  = max(1200, int(sw * 0.80))
        h  = max(720,  int(sh * 0.82))
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.title(f"C00NECTED  —  {self.username}")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        try:
            self.net.disconnect()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _build_layout(self):
        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.sidebar = tk.Frame(self.root, bg=C_SIDEBAR, width=420)
        self.sidebar.grid(row=0, column=0, sticky='nsew')
        self.sidebar.grid_propagate(False)
        self._build_sidebar()

        self.right = tk.Frame(self.root, bg=C_PANEL)
        self.right.grid(row=0, column=1, sticky='nsew')
        self._build_empty_right()

    def _build_sidebar(self):
        hdr = tk.Frame(self.sidebar, bg=C_HEADER, height=64)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)

        tk.Label(hdr, text="C00NECTED", font=("Consolas", 14, "bold"),
                 fg=C_GREEN, bg=C_HEADER).pack(side='left', padx=16, pady=14)

        for txt, cmd in [("＋", self._new_chat_menu), ("⋮", self._overflow_menu)]:
            b = tk.Button(hdr, text=txt, font=("Segoe UI", 14),
                          bg=C_HEADER, fg=C_SECONDARY, relief='flat',
                          cursor='hand2', command=cmd)
            b.pack(side='right', padx=6)

        sf = tk.Frame(self.sidebar, bg=C_SIDEBAR, pady=8, padx=10)
        sf.pack(fill='x')
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', self._filter_chats)
        s_entry = tk.Entry(sf, textvariable=self.search_var,
                           bg=C_INPUT_BG, fg=C_TEXT, insertbackground=C_TEXT,
                           relief='flat', font=FONT_APP, bd=0)
        s_entry.pack(fill='x', ipady=7, padx=6)
        s_entry.insert(0, "🔍  Search or start new chat")
        s_entry.bind("<FocusIn>", lambda e: s_entry.delete(0, 'end')
                     if s_entry.get().startswith("🔍") else None)

        list_frame = tk.Frame(self.sidebar, bg=C_SIDEBAR)
        list_frame.pack(fill='both', expand=True)

        scrollbar = tk.Scrollbar(list_frame, troughcolor=C_SIDEBAR, bg=C_SIDEBAR,
                                 bd=0, relief='flat')
        scrollbar.pack(side='right', fill='y')

        self._cl_canvas = tk.Canvas(
            list_frame, bg=C_SIDEBAR, highlightthickness=0,
            yscrollcommand=scrollbar.set
        )
        self._cl_canvas.pack(fill='both', expand=True)
        scrollbar.config(command=self._cl_canvas.yview)

        self._cl_inner = tk.Frame(self._cl_canvas, bg=C_SIDEBAR)
        self._cl_win   = self._cl_canvas.create_window(
            (0, 0), window=self._cl_inner, anchor='nw'
        )
        self._cl_inner.bind(
            '<Configure>',
            lambda e: self._cl_canvas.configure(
                scrollregion=self._cl_canvas.bbox('all')
            )
        )
        self._cl_canvas.bind(
            '<Configure>',
            lambda e: self._cl_canvas.itemconfig(self._cl_win, width=e.width)
        )
        self._cl_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._cl_canvas.yview_scroll(
                int(-1 * (e.delta / 120)), "units"
            )
        )

        foot = tk.Frame(self.sidebar, bg=C_HEADER, height=52)
        foot.pack(fill='x', side='bottom')
        foot.pack_propagate(False)
        tk.Canvas(foot, width=36, height=36, bg=C_GREEN, highlightthickness=0
                  ).place(x=10, y=8)
        tk.Label(foot, text=self.username, font=FONT_BOLD,
                 fg=C_TEXT, bg=C_HEADER).place(x=54, y=8)
        tk.Label(foot, text="● Online", font=FONT_MICRO,
                 fg=C_ONLINE, bg=C_HEADER).place(x=54, y=28)

    def _build_empty_right(self):
        for w in self.right.winfo_children():
            w.destroy()
        tk.Label(
            self.right,
            text="Select a conversation\nor start a new chat with ＋",
            font=("Segoe UI", 14), fg=C_SECONDARY, bg=C_PANEL,
            justify='center'
        ).place(relx=0.5, rely=0.5, anchor='center')

    def _build_chat_right(self, chat_id: str):
        for w in self.right.winfo_children():
            w.destroy()

        self.right.rowconfigure(0, weight=0)
        self.right.rowconfigure(1, weight=1)
        self.right.rowconfigure(2, weight=0)
        self.right.columnconfigure(0, weight=1)

        hdr = tk.Frame(self.right, bg=C_HEADER, height=64)
        hdr.grid(row=0, column=0, sticky='ew')
        hdr.grid_propagate(False)

        cv = tk.Canvas(hdr, width=40, height=40, bg=C_GREEN,
                       highlightthickness=0)
        cv.place(x=14, y=12)
        initial = chat_id[0].upper() if chat_id else '?'
        cv.create_text(20, 20, text=initial, font=("Segoe UI", 16, "bold"), fill='#000')

        tk.Label(hdr, text=chat_id, font=FONT_BOLD,
                 fg=C_TEXT, bg=C_HEADER).place(x=68, y=10)
        self.chat_status_lbl = tk.Label(hdr, text="",
                                        font=FONT_SMALL, fg=C_SECONDARY, bg=C_HEADER)
        self.chat_status_lbl.place(x=68, y=30)

        btn_frame = tk.Frame(hdr, bg=C_HEADER)
        btn_frame.place(relx=1.0, rely=0.5, anchor='e', x=-12)

        for icon, ctype in [("📞", "audio"), ("🎥", "video")]:
            tk.Button(
                btn_frame, text=icon, font=("Segoe UI", 16),
                bg=C_HEADER, fg=C_TEXT, relief='flat',
                cursor='hand2',
                command=lambda ct=ctype: self._start_call(ct)
            ).pack(side='left', padx=4)

        tk.Button(
            btn_frame, text="⋮", font=("Segoe UI", 16),
            bg=C_HEADER, fg=C_SECONDARY, relief='flat',
            cursor='hand2', command=self._group_options_menu
        ).pack(side='left', padx=4)

        msg_frame = tk.Frame(self.right, bg=C_PANEL)
        msg_frame.grid(row=1, column=0, sticky='nsew')

        self.msg_scrollbar = tk.Scrollbar(msg_frame, troughcolor=C_PANEL,
                                          bg=C_PANEL, bd=0, relief='flat')
        self.msg_scrollbar.pack(side='right', fill='y')

        self.msg_canvas = tk.Canvas(
            msg_frame, bg=C_PANEL, highlightthickness=0,
            yscrollcommand=self.msg_scrollbar.set
        )
        self.msg_canvas.pack(fill='both', expand=True)
        self.msg_scrollbar.config(command=self.msg_canvas.yview)

        self.msg_inner = tk.Frame(self.msg_canvas, bg=C_PANEL)
        self._canvas_win = self.msg_canvas.create_window(
            (0, 0), window=self.msg_inner, anchor='nw'
        )
        self.msg_inner.bind('<Configure>', self._on_msg_frame_configure)
        self.msg_canvas.bind('<Configure>', self._on_canvas_configure)

        self.msg_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        input_bar = tk.Frame(self.right, bg=C_INPUT_BG, height=64)
        input_bar.grid(row=2, column=0, sticky='ew')
        input_bar.grid_propagate(False)
        input_bar.columnconfigure(1, weight=1)

        tk.Button(
            input_bar, text="📎", font=("Segoe UI", 16),
            bg=C_INPUT_BG, fg=C_SECONDARY, relief='flat',
            cursor='hand2', command=self._send_file
        ).grid(row=0, column=0, padx=(10, 4), pady=12)

        self.input_var = tk.StringVar()
        self.input_entry = tk.Entry(
            input_bar, textvariable=self.input_var,
            bg=C_INPUT_BG, fg=C_TEXT, insertbackground=C_TEXT,
            relief='flat', font=FONT_APP, bd=0
        )
        self.input_entry.grid(row=0, column=1, sticky='ew', ipady=10, padx=4)
        self.input_entry.bind("<Return>", lambda _: self._send_message())
        self.input_entry.focus()

        self._mic_btn = tk.Button(
            input_bar, text="🎤", font=("Segoe UI", 16),
            bg=C_INPUT_BG, fg=C_SECONDARY, relief='flat', cursor='hand2'
        )
        self._mic_btn.grid(row=0, column=2, padx=(4, 4), pady=12)
        self._mic_btn.bind("<ButtonPress-1>",   lambda _e: self._start_voice_recording())
        self._mic_btn.bind("<ButtonRelease-1>", lambda _e: self._stop_voice_recording())

        tk.Button(
            input_bar, text="➤", font=("Segoe UI", 16),
            bg=C_INPUT_BG, fg=C_ACCENT, relief='flat',
            cursor='hand2', command=self._send_message
        ).grid(row=0, column=3, padx=(0, 10), pady=12)

        self._render_all_messages()

    def _on_msg_frame_configure(self, _event=None):
        self.msg_canvas.configure(scrollregion=self.msg_canvas.bbox('all'))

    def _on_canvas_configure(self, event):
        self.msg_canvas.itemconfig(self._canvas_win, width=event.width)

    def _on_mousewheel(self, event):
        if hasattr(self, 'msg_canvas'):
            self.msg_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _render_all_messages(self):
        for w in self.msg_inner.winfo_children():
            w.destroy()
        msgs = self.conversations.get(self.current_chat, [])

        changed = False
        for m in msgs:
            if not m.get('outgoing', False) and m.get('unread', False):
                m['unread'] = False
                changed = True
        if changed:
            self.history._data['conversations'][self.current_chat] = msgs
            self.history._save_nolock()
            self.unread_counts[self.current_chat] = 0
            self._update_chat_list()

        last_out_idx = None
        for i, m in enumerate(msgs):
            if m.get('outgoing', False):
                last_out_idx = i

        for i, m in enumerate(msgs):
            self._render_bubble(m, register_tick=(i == last_out_idx))

        self._scroll_to_bottom()

    def _render_bubble(self, msg: dict, register_tick: bool = False):
        msg_type = msg.get('type', '')
        content  = msg.get('content', '')
        sender   = msg.get('sender', '')
        ts       = msg.get('timestamp', '')
        outgoing = msg.get('outgoing', False)
        status   = msg.get('status', 'pending')   
        unread   = msg.get('unread', False)

        is_notification = (
            msg_type == 'system'
            or (msg_type == 'group' and sender == 'SYSTEM')
            or (msg_type == 'dm'   and sender == 'SYSTEM')
        )
        if is_notification:
            if outgoing:
                outer = tk.Frame(self.msg_inner, bg=C_PANEL)
                outer.pack(fill='x', padx=8, pady=2)
                bubble = tk.Frame(outer, bg=C_SENT, padx=10, pady=6)
                bubble.pack(anchor='e', padx=4)
                tk.Label(
                    bubble, text=content, font=FONT_APP,
                    fg=C_TEXT, bg=C_SENT, wraplength=480, justify='left'
                ).pack(anchor='w')
                meta = tk.Frame(bubble, bg=C_SENT)
                meta.pack(anchor='e')
                tk.Label(meta, text=ts.split(' ')[1] if ' ' in ts else ts,
                         font=FONT_MICRO, fg=C_SECONDARY, bg=C_SENT).pack(side='left')
                self.msg_canvas.update_idletasks()
                self._scroll_to_bottom()
                return
            pill_frame = tk.Frame(self.msg_inner, bg=C_PANEL)
            pill_frame.pack(fill='x', pady=6)
            pill = tk.Frame(pill_frame, bg='#1A3040', padx=14, pady=5)
            pill.pack(anchor='center')
            tk.Label(
                pill, text=content,
                font=("Segoe UI", 8), fg='#7DC8A8', bg='#1A3040',
                wraplength=460, justify='center'
            ).pack()
            self.msg_canvas.update_idletasks()
            self._scroll_to_bottom()
            return

        if msg_type == 'system':
            tk.Label(
                self.msg_inner, text=content, font=FONT_MICRO,
                fg=C_SECONDARY, bg=C_PANEL, wraplength=500, pady=2
            ).pack(padx=60, pady=2)
            return

        if msg_type == 'voice':
            outer = tk.Frame(self.msg_inner, bg=C_PANEL)
            outer.pack(fill='x', padx=8, pady=2)
            align = 'e' if outgoing else 'w'
            bg    = C_SENT if outgoing else C_RECV
            bubble = tk.Frame(outer, bg=bg, padx=10, pady=8)
            bubble.pack(anchor=align, padx=4)

            if not outgoing and msg.get('type') == 'group':
                tk.Label(bubble, text=sender, font=("Segoe UI", 8, "bold"),
                         fg=C_ACCENT, bg=bg).pack(anchor='w')

            row = tk.Frame(bubble, bg=bg)
            row.pack(anchor='w')

            voice_path = msg.get('voice_path', '')
            dur        = msg.get('voice_dur', 0)

            play_btn = tk.Button(
                row, text="▶", font=("Segoe UI", 14),
                bg=C_ACCENT, fg=C_TEXT, relief='flat',
                cursor='hand2', width=2
            )
            play_btn.pack(side='left', padx=(0, 8))

            def _on_play(btn=play_btn, path=voice_path, d=dur):
                if not path:
                    self._show_status("⚠ No path for this voice note.")
                    return
                if not os.path.exists(path):
                    self._show_status(f"⚠ File missing: {path}")
                    return
                btn.config(text="⏸", state='disabled', bg=C_ACCENT_LT)

                def _run():
                    try:
                        pa = pyaudio.PyAudio()
                        with _wave.open(path, 'rb') as wf:
                            stream = pa.open(
                                format=pa.get_format_from_width(wf.getsampwidth()),
                                channels=wf.getnchannels(),
                                rate=wf.getframerate(),
                                output=True
                            )
                            chunk = wf.readframes(1024)
                            while chunk:
                                stream.write(chunk)
                                chunk = wf.readframes(1024)
                            stream.stop_stream()
                            stream.close()
                        pa.terminate()
                    except Exception as e:
                        self.gui_queue.put(('STATUS', f'⚠ Playback error: {e}'))
                    finally:
                        try:
                            btn.config(text="▶", state='normal', bg=C_ACCENT)
                        except tk.TclError:
                            pass

                threading.Thread(target=_run, daemon=True).start()

            play_btn.config(command=_on_play)

            bar_cv = tk.Canvas(row, bg=bg, highlightthickness=0, width=110, height=30)
            bar_cv.pack(side='left')
    
            seed = int(_hs.md5((voice_path or ts).encode()).hexdigest()[:8], 16)
            rng  = _rnd.Random(seed)
            for i in range(22):
                h = rng.randint(4, 24)
                x = 2 + i * 5
                bar_cv.create_rectangle(x, 15 - h//2, x+3, 15 + h//2,
                                        fill=C_ACCENT_LT, outline='')

            tk.Label(row, text=f"{dur}s", font=FONT_MICRO,
                     fg=C_SECONDARY, bg=bg).pack(side='left', padx=(6, 0))

            meta = tk.Frame(bubble, bg=bg)
            meta.pack(anchor='e')
            time_str = ts.split(' ')[1] if ' ' in ts else ts
            tk.Label(meta, text=time_str, font=FONT_MICRO,
                     fg=C_SECONDARY, bg=bg).pack(side='left')
            if outgoing:
                tick_txt, tick_col = self._tick_appearance(status)
                tick_lbl = tk.Label(meta, text=tick_txt,
                                    font=("Segoe UI", 9), fg=tick_col, bg=bg)
                tick_lbl.pack(side='left', padx=(4, 0))
                if register_tick:
                    self._tick_labels[self.current_chat] = tick_lbl

            self.msg_canvas.update_idletasks()
            self._scroll_to_bottom()
            return

        outer = tk.Frame(self.msg_inner, bg=C_PANEL)
        outer.pack(fill='x', padx=8, pady=2)

        align = 'e' if outgoing else 'w'
        bg    = C_SENT if outgoing else C_RECV

        bubble = tk.Frame(outer, bg=bg, padx=10, pady=6)
        bubble.pack(anchor=align, padx=4)

        if unread and not outgoing:
            stripe = tk.Frame(bubble, bg=C_GREEN, width=3)
            stripe.pack(side='left', fill='y', padx=(0, 6))

        if not outgoing and msg_type == 'group':
            tk.Label(
                bubble, text=sender,
                font=("Segoe UI", 8, "bold"), fg=C_GREEN, bg=bg
            ).pack(anchor='w')

        tk.Label(
            bubble, text=content, font=FONT_APP,
            fg=C_TEXT, bg=bg, wraplength=480, justify='left'
        ).pack(anchor='w')

        meta = tk.Frame(bubble, bg=bg)
        meta.pack(anchor='e', fill='x')

        time_str = ts.split(' ')[1] if ' ' in ts else ts
        tk.Label(meta, text=time_str, font=FONT_MICRO, fg=C_SECONDARY, bg=bg
                 ).pack(side='left')

        if outgoing:
            tick_txt, tick_col = self._tick_appearance(status)
            tick_lbl = tk.Label(
                meta, text=tick_txt, font=("Segoe UI", 9),
                fg=tick_col, bg=bg
            )
            tick_lbl.pack(side='left', padx=(4, 0))
            if register_tick:
                self._tick_labels[self.current_chat] = tick_lbl

        self.msg_canvas.update_idletasks()
        self._scroll_to_bottom()

    @staticmethod
    def _tick_appearance(status: str) -> tuple:
        if status == 'read':
            return '✓✓', C_TICK_BLUE
        if status == 'delivered':
            return '✓✓', C_TICK_GREY
        return '✓', C_TICK_GREY

    def _scroll_to_bottom(self):
        self.msg_canvas.yview_moveto(1.0)

    def _update_last_tick(self, chat_id: str, new_status: str):
        msgs = self.conversations.get(chat_id, [])
        for m in reversed(msgs):
            if m.get('outgoing', False):
                m['status'] = new_status
                self.history._data.get('conversations', {}).get(chat_id, [])
                self.history._save_nolock()
                break

        if chat_id == self.current_chat and chat_id in self._tick_labels:
            lbl = self._tick_labels.get(chat_id)
            if lbl and lbl.winfo_exists():
                txt, col = self._tick_appearance(new_status)
                lbl.config(text=txt, fg=col)

    def _append_message(self, chat_id: str, msg_dict: dict):
        if chat_id not in self.conversations:
            self.conversations[chat_id] = []
        self.conversations[chat_id].append(msg_dict)

        if msg_dict.get('type') != 'system' or msg_dict.get('content', '').startswith('['):
            self.history.append(chat_id, msg_dict)

        is_incoming = not msg_dict.get('outgoing', False)
        is_active   = (self.current_chat == chat_id)

        if is_incoming and not is_active:
            self.unread_counts[chat_id] = self.unread_counts.get(chat_id, 0) + 1
            self._update_chat_list()
        elif is_active and hasattr(self, 'msg_inner'):
            is_last_out = msg_dict.get('outgoing', False)
            self._render_bubble(msg_dict, register_tick=is_last_out)
            self._update_chat_list()
        else:
            self._update_chat_list()

    @staticmethod
    def _draw_avatar(canvas, cx, cy, is_group: bool, active: bool):
        r = 20
        bg = C_ACCENT if is_group else "#1E3A5F"
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=bg, outline='')
        fg = C_TEXT
        if is_group:
            for dx, layer in [(-5, 0), (5, 1)]:
                hx = cx + dx
                canvas.create_oval(hx - 5, cy - 14, hx + 5, cy - 4,
                                   fill=fg, outline='')
                canvas.create_arc(hx - 9, cy - 6, hx + 9, cy + 10,
                                  start=0, extent=180,
                                  fill=fg, outline='', style='chord')
        else:
            canvas.create_oval(cx - 6, cy - 14, cx + 6, cy - 2,
                               fill=fg, outline='')
            canvas.create_arc(cx - 11, cy - 4, cx + 11, cy + 12,
                              start=0, extent=180,
                              fill=fg, outline='', style='chord')

    def _update_chat_list(self, filter_text=''):
        if not hasattr(self, "_cl_inner"):
            return
        for w in self._cl_inner.winfo_children():
            w.destroy()

        ft = filter_text.lower().strip()
        for chat_id in sorted(self.conversations.keys()):
            if chat_id == 'SYSTEM':
                continue
            if ft and ft not in chat_id.lower():
                continue

            msgs     = self.conversations[chat_id]
            last     = ''
            for m in reversed(msgs):
                c = m.get('content', '')
                if c and m.get('type') != 'system':
                    last = c[:46] + '…' if len(c) > 46 else c
                    break

            is_group = self._is_group_chat(chat_id)
            unread   = self.unread_counts.get(chat_id, 0)
            is_active = (chat_id == self.current_chat)
            row_bg   = C_HOVER if is_active else C_SIDEBAR

            row = tk.Frame(self._cl_inner, bg=row_bg, cursor='hand2')
            row.pack(fill='x')

            ic = tk.Canvas(row, width=52, height=64,
                           bg=row_bg, highlightthickness=0)
            ic.pack(side='left', padx=(8, 4))
            self._draw_avatar(ic, 26, 32, is_group, is_active)

            txt = tk.Frame(row, bg=row_bg)
            txt.pack(side='left', fill='both', expand=True, pady=10, padx=(0, 8))

            top_row = tk.Frame(txt, bg=row_bg)
            top_row.pack(fill='x')

            tk.Label(top_row, text=chat_id,
                     font=("Segoe UI", 11, "bold"),
                     fg=C_TEXT, bg=row_bg, anchor='w'
                     ).pack(side='left', fill='x', expand=True)

            if unread:
                tk.Label(top_row, text=f" {unread} ",
                         font=("Segoe UI", 8, "bold"),
                         fg='white', bg=C_ACCENT, padx=3, pady=1
                         ).pack(side='right')

            tk.Label(txt, text=last,
                     font=("Segoe UI", 9),
                     fg=C_SECONDARY, bg=row_bg,
                     anchor='w', wraplength=300, justify='left'
                     ).pack(fill='x', anchor='w')

            tk.Frame(self._cl_inner, bg=C_BORDER, height=1).pack(fill='x', padx=12)

            def _select(e=None, cid=chat_id):
                self._open_chat(cid)
            for widget in (row, ic, txt, top_row):
                widget.bind('<Button-1>', _select)
            for child in txt.winfo_children() + top_row.winfo_children():
                child.bind('<Button-1>', _select)

    def _is_group_chat(self, chat_id: str) -> bool:
        if chat_id in self.known_groups:
            return True
        msgs = self.conversations.get(chat_id, [])
        if any(m.get('type') == 'group' for m in msgs):
            self.known_groups.add(chat_id)
            self.history.mark_group(chat_id)
            return True
        return False

    def _open_chat(self, chat_id: str):
        if chat_id and chat_id != self.current_chat:
            self.current_chat = chat_id
            self.unread_counts[chat_id] = 0
            self._update_chat_list()
            self._build_chat_right(chat_id)

    def _filter_chats(self, *_):
        txt = self.search_var.get()
        if txt.startswith("🔍"):
            self._update_chat_list('')
        else:
            self._update_chat_list(txt)

    def _send_message(self):
        if not self.current_chat: return
        text = self.input_var.get().strip()
        if not text: return

        chat_id  = self.current_chat
        is_group = self._is_group_chat(chat_id)
        if is_group:
            self.net.send_group_msg(chat_id, text)
        else:
            self.net.send_dm(chat_id, text)

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append_message(chat_id, {
            'type': 'group' if is_group else 'dm',
            'sender': self.username,
            'content': text,
            'timestamp': ts,
            'outgoing': True,
            'status': 'pending',
        })
        self.input_var.set('')

    def _send_file(self):
        if not self.current_chat:
            messagebox.showinfo("Select chat", "Please select a conversation first.")
            return
        fp = filedialog.askopenfilename(
            title="Choose a file",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.gif *.bmp *.webp"),
                ("PDF",    "*.pdf"),
                ("Audio",  "*.mp3 *.wav *.flac *.ogg *.aac"),
                ("Video",  "*.mp4 *.avi *.mov *.mkv *.webm"),
                ("All",    "*.*"),
            ]
        )
        if not fp: return
        ftype = get_file_type(fp)
        if ftype == 'unknown':
            messagebox.showerror("Unsupported", "That file type is not supported.")
            return
        self.net.send_file(self.current_chat, fp)
        self._show_status(f"📤 Sending {os.path.basename(fp)}…")

    def _start_voice_recording(self):
        if not self.current_chat:
            return
        self._is_recording = True
        self._mic_btn.config(fg=C_RED, text="⏹")
        self._show_status("🔴 Recording…  Release to send")
        self._voice_rec.start()

    def _stop_voice_recording(self):
        if not self._is_recording:
            return
        self._is_recording = False
        self._mic_btn.config(fg=C_SECONDARY, text="🎤")

        path, duration = self._voice_rec.stop()
        if not path or duration < 0.5:
            self._show_status("Voice note too short.")
            return

        chat_id = self.current_chat
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = {
            'type':      'voice',
            'sender':    self.username,
            'content':   f'🎤 Voice note  {duration}s',
            'timestamp': ts,
            'outgoing':  True,
            'status':    'pending',
            'voice_path': path,
            'voice_dur':  duration,
        }
        self._append_message(chat_id, msg)
        self.net.send_file(chat_id, path)
        self._show_status(f"📤 Sending voice note ({duration}s)…")

    def _start_call(self, call_type: str):
        if not self.current_chat:
            messagebox.showinfo("No chat selected", "Select a conversation first.")
            return
        if self.active_call_window:
            messagebox.showinfo("Already in call", "You are already in a call.")
            return
        self.net.request_call(self.current_chat, call_type)
        self._show_status(f"📞 Calling {self.current_chat}…")

    def _open_call_window(self, peer: str, call_type: str):
        self.active_call_window = CallWindow(
            self.root, self.net, peer, call_type,
            on_end=self._call_ended_cleanup
        )
        if call_type == 'video' and PIL_AVAILABLE:
            self.active_call_window.poll_local_pip()

    def _call_ended_cleanup(self):
        self.active_call_window = None

    def _new_chat_menu(self):
        menu = tk.Menu(self.root, tearoff=0, bg=C_HEADER, fg=C_TEXT,
                       activebackground=C_HOVER)
        menu.add_command(label="💬 New DM",      command=self._new_dm_dialog)
        menu.add_command(label="👥 Create group", command=self._create_group_dialog)
        menu.add_command(label="📥 Open group",   command=self._open_group_dialog)
        menu.tk_popup(self.root.winfo_rootx() + 300,
                      self.root.winfo_rooty() + 64)

    def _overflow_menu(self):
        menu = tk.Menu(self.root, tearoff=0, bg=C_HEADER, fg=C_TEXT,
                       activebackground=C_HOVER)
        menu.add_command(label="🔴 Disconnect", command=self._disconnect)
        menu.tk_popup(self.root.winfo_rootx() + 300,
                      self.root.winfo_rooty() + 64)

    def _group_options_menu(self):
        if not self.current_chat: return
        menu = tk.Menu(self.root, tearoff=0, bg=C_HEADER, fg=C_TEXT,
                       activebackground=C_HOVER)
        menu.add_command(label="➕ Add member",  command=self._add_member_dialog)
        menu.add_command(label="🚪 Leave group", command=self._leave_group)
        menu.tk_popup(self.root.winfo_pointerx(),
                      self.root.winfo_pointery())

    def _new_dm_dialog(self):
        user = simpledialog.askstring("New DM", "Enter username to message:",
                                      parent=self.root)
        if user:
            user = user.strip()
            if user and user not in self.conversations:
                self.conversations[user] = []
                self.history.ensure_chat(user)
                self._update_chat_list()
            self.current_chat = user
            self._build_chat_right(user)

    def _create_group_dialog(self):
        name = simpledialog.askstring("Create Group", "Group name:", parent=self.root)
        if name:
            name = name.strip()
            self.net.create_group(name)
            self.known_groups.add(name)
            self.history.mark_group(name)
            if name not in self.conversations:
                self.conversations[name] = []
                self.history.ensure_chat(name)
                self._update_chat_list()
            self.current_chat = name
            self._build_chat_right(name)

    def _open_group_dialog(self):
        name = simpledialog.askstring("Open Group",
                                      "Enter group name to open:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return

        if name not in self.known_groups and name not in self.conversations:
            messagebox.showerror(
                "Group Not Found",
                f"'{name}' does not exist or you are not a member.\n"
                "Ask the group admin to add you.",
                parent=self.root
            )
            return

        self.known_groups.add(name)
        self.history.mark_group(name)
        if name not in self.conversations:
            self.conversations[name] = []
            self.history.ensure_chat(name)
        self._update_chat_list()
        self.current_chat = name
        self._build_chat_right(name)

    def _add_member_dialog(self):
        if not self.current_chat: return
        user = simpledialog.askstring("Add Member",
                                      f"Add user to '{self.current_chat}':", parent=self.root)
        if user:
            self.net.add_to_group(self.current_chat, user.strip())

    def _leave_group(self):
        if not self.current_chat: return
        if messagebox.askyesno("Leave Group", f"Leave '{self.current_chat}'?"):
            self.net.leave_group(self.current_chat)
            self.history.delete_chat(self.current_chat)
            del self.conversations[self.current_chat]
            self.known_groups.discard(self.current_chat)
            self.current_chat = None
            self._update_chat_list()
            self._build_empty_right()

    def _disconnect(self):
        if messagebox.askyesno("Disconnect", "Disconnect from C00NECTED?"):
            self.net.disconnect()
            self.root.destroy()

    def _show_status(self, text: str):
        if hasattr(self, 'chat_status_lbl'):
            self.chat_status_lbl.config(text=text)
            self.root.after(5000, lambda: self.chat_status_lbl.config(text='')
                            if hasattr(self, 'chat_status_lbl') else None)

    def _start_event_loop(self):
        self.root.after(50, self._process_queue)

    def _start_group_purge(self):
        self._groups_to_verify = list(self.known_groups)
        self._advance_group_purge()

    def _advance_group_purge(self):
        if not self._groups_to_verify:
            self._verifying_group = None
            return
        self._verifying_group = self._groups_to_verify.pop(0)
        self.net.verify_group(self._verifying_group)

    def _process_queue(self):
        try:
            while True:
                event = self.gui_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(50, self._process_queue)

    def _handle_event(self, event: tuple):
        etype = event[0]

        if etype == 'MESSAGE':
            _, raw_msg, msg_type = event

            _SILENT = {
                "GROUP CREATED", "GROUP EXISTS", "LEFT GROUP",
                "GROUP NOT FOUND OR NOT MEMBER",
            }
            _STATUS_PREFIXES = (
                "ADD_STATUS:", "CALLING:", "MEDIA_ID:",
                "ERROR:", "GROUP CREATED", "GROUP EXISTS",
            )

            if raw_msg == "DELIVERED" and self.current_chat:
                self._update_last_tick(self.current_chat, 'delivered')
                return
            if raw_msg.startswith("USER OFFLINE") and self.current_chat:
                self._update_last_tick(self.current_chat, 'queued')
                self._show_status(raw_msg)
                return

            if raw_msg in _SILENT:
                return
            if any(raw_msg.startswith(p) for p in _STATUS_PREFIXES):
                if raw_msg.startswith("ADD_STATUS:") and self._verifying_group:
                    self._verifying_group = None
                    self._advance_group_purge()
                    return
                self._show_status(raw_msg)
                return

            parsed = parse_incoming_message(raw_msg, self.username)

            if parsed['type'] == 'dm':
                sender = parsed['sender']
                if sender == 'SYSTEM':
                    content = parsed.get('content', '')
                    target = next(
                        (g for g in self.known_groups if g in content),
                        self.current_chat if self._is_group_chat(self.current_chat or '') else None
                    )
                    if target:
                        self._append_message(target, {
                            'type': 'system', 'content': content,
                            'sender': 'SYSTEM', 'outgoing': False
                        })
                    else:
                        self._show_status(content)
                    return
                parsed['unread'] = (sender != self.username and
                                    self.current_chat != sender)
                self._append_message(sender, {**parsed, 'outgoing': False})
                self._update_last_tick(sender, 'read')

            elif parsed['type'] == 'group':
                group = parsed['group']
                self.known_groups.add(group)
                if parsed.get('sender') == self.username:
                    return
                parsed['unread'] = (self.current_chat != group)
                self._append_message(group, {**parsed, 'outgoing': False})
                self._update_last_tick(group, 'read')

            else:
                content = parsed.get('content', raw_msg)
                if msg_type == 'C':
                    self._show_status(content)
                elif self.current_chat:
                    self._append_message(self.current_chat, parsed)

        elif etype == 'STATUS':
            self._show_status(event[1])

        elif etype == 'GROUP_NOT_FOUND':
            if self._verifying_group:
                stale = self._verifying_group
                self._verifying_group = None
                self.known_groups.discard(stale)
                self.conversations.pop(stale, None)
                self.history.delete_chat(stale)
                self._update_chat_list()
                self._advance_group_purge()
                return
            if getattr(self, '_pending_group_open', None):
                messagebox.showerror(
                    "Group Not Found",
                    f"'{self._pending_group_open}' does not exist or you are not a member.\n"
                    "Ask the group admin to add you.",
                    parent=self.root
                )
                self._pending_group_open = None

        elif etype == 'INCOMING_CALL':
            _, caller, call_type = event
            self._pending_call_info = (caller, call_type)

            def on_accept():
                self.net.accept_call(caller)
                self.net.call_manager.accept_incoming_call(call_type)
                self._open_call_window(caller, call_type)

            def on_reject():
                self.net.reject_call(caller)
                self.net.call_manager.end_call()

            IncomingCallDialog(self.root, caller, call_type, on_accept, on_reject)

        elif etype == 'INCOMING_CALL_UDP':
            pass   

        elif etype == 'CALL_RINGING':
            _, peer = event
            self._show_status(f"📞 Ringing {peer}…")

        elif etype == 'CALL_ACCEPTED':
            _, callee = event
            self._show_status(f"✅ {callee} accepted!")
            if not self.active_call_window:
                ctype = self.net.current_call_type or 'audio'
                self._open_call_window(callee, ctype)
                if self.active_call_window:
                    self.active_call_window.update_status("Connected")

        elif etype == 'CALL_REJECTED':
            _, callee = event
            self._show_status(f"❌ {callee} declined the call.")
            messagebox.showinfo("Call Declined", f"{callee} declined your call.")

        elif etype == 'CALL_OFFLINE':
            self._show_status(f"📵 {event[1]}")

        elif etype == 'CALL_ENDED_REMOTE':
            self._show_status("📵 Call ended by remote.")
            if self.active_call_window:
                self.active_call_window._end_call()

        elif etype == 'FILE_SENT':
            _, filename, recipient = event
            self._show_status(f"✅ '{filename}' sent to {recipient}")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            note = {
                'type':     'system',
                'content':  f"📎 Sent file: {filename}",
                'timestamp': ts,
                'outgoing': True,
                'status': 'pending'
            }
            self._append_message(self.username, note)

        elif etype == 'FILE_RECEIVED':
            _, filename, ftype, save_path = event[:4]
            sender = event[4] if len(event) > 4 else None
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if filename.startswith('c00n_voice_') and filename.endswith('.wav'):
                dur = 0.0
                try:
                    with _wave.open(save_path, 'rb') as wf:
                        dur = round(wf.getnframes() / wf.getframerate(), 1)
                except Exception:
                    pass

                chat_id = sender or self.current_chat
                if not chat_id:
                    self._show_status(f"📥 Voice note saved: {save_path}")
                    return

                msg = {
                    'type':       'voice',
                    'sender':     sender or '?',
                    'content':    f'🎤 Voice note  {dur}s',
                    'timestamp':  ts,
                    'outgoing':   False,
                    'unread':     (chat_id != self.current_chat),
                    'voice_path': save_path,
                    'voice_dur':  dur,
                }
                self._append_message(chat_id, msg)
                return

            self._show_status(f"📥 Received: {filename}")
            note = {
                'type':    'system',
                'content': f"📎 '{filename}' received  →  {save_path}",
            }
            target_chat = sender or self.current_chat
            if target_chat:
                self._append_message(target_chat, note)

        elif etype == 'TIMEOUT':
            messagebox.showwarning("Session Timeout",
                                   "You were disconnected due to inactivity.")
            self.root.destroy()

        elif etype == 'DISCONNECTED':
            if not self.net.shutting_down:
                messagebox.showerror("Disconnected",
                                     f"Lost connection: {event[1]}\nPlease restart.")
                self.root.destroy()

        elif etype == 'CONNECT_ERROR':
            messagebox.showerror("Connection Failed",
                                 f"Cannot connect to server:\n{event[1]}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────────────────

class App:
    def __init__(self):
        self.root      = tk.Tk()
        self.root.title("C00NECTED")
        self.root.configure(bg=C_BG)
        self.root.minsize(980, 640)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = max(1200, int(sw * 0.80))
        h = max(720, int(sh * 0.82))
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.gui_queue = queue.Queue()
        self.net = NetworkClient(self.gui_queue)

        connected = self.net.connect()

        if connected:
            SplashScreen(self.root, self._show_auth)
        else:
            self._show_connect_error()

    def _show_connect_error(self):
        for w in self.root.winfo_children():
            w.destroy()
        tk.Label(
            self.root,
            text="⚠️  Cannot reach server\n\nMake sure the ARCP server is running\nand that SERVER_IP is correct.",
            font=("Segoe UI", 13), fg=C_AMBER, bg=C_BG, justify='center'
        ).place(relx=0.5, rely=0.5, anchor='center')

        tk.Button(
            self.root, text="Retry", font=FONT_BOLD,
            bg=C_GREEN, fg='#000', relief='flat', padx=20, pady=8,
            cursor='hand2', command=self._retry_connect
        ).place(relx=0.5, rely=0.65, anchor='center')

    def _retry_connect(self):
        if self.net.connect():
            SplashScreen(self.root, self._show_auth)
        else:
            self._show_connect_error()

    def _show_auth(self):
        for w in self.root.winfo_children():
            w.destroy()
        AuthWindow(self.root, self.net, self._on_login_success)

    def _on_login_success(self, username: str):
        for w in self.root.winfo_children():
            w.destroy()
        ChatWindow(self.root, self.net, username, self.gui_queue)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()