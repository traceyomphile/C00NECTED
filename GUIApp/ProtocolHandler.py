"""
ProtocolHandler.py - Core utility for TCP message framing and string passing.

This module implements the ARCP (All-Round Communication Protocol) framing standard,
ensuring robust message delivery over straming sockets.

Functions:
    - get_file_type: Returns the file type fromthe given filename.
    - send_framed_msg: Sends a frame from client to server through the given socket.
    - receive_framed_msg: Receives frame from server to client through the given socket.
    - parse_incoming_message: Parses a timestamped server message into structured data.

Date: 15-03-2026
"""
import os
import socket
import re

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.aac'}
VIDEO_EXTS = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
PDF_EXTS   = {'.pdf'}

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