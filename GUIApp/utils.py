# utils.py

import os
import socket
import re
import threading
import queue
import time
import base64
import pyaudio
import wave as _wave
import tempfile
from datetime import datetime

# ----------- SERVER CONFIGURATION ----------
SERVER_IP = '196.47.192.177'
TCP_PORT = 50000

# --------- VIDEO AND AUDIO CONSTANTS ----------
MAX_VIDEO_SECONDS = 45

PKT_AUDIO   = b'\x01'
PKT_END     = b'\xFF'

AUDIO_FORMAT   = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_RATE     = 44100
AUDIO_CHUNK    = 1024

# ------------ MEDIA FILE EXTENSIONS ------------------

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.aac'}
VIDEO_EXTS = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
PDF_EXTS   = {'.pdf'}

# ----------- COLORS AND FONTS ---------------------------

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

# --------- UTILITY FUNCTIONS ---------------------

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