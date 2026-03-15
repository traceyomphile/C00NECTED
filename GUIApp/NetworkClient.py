"""
NetworkClient.py - Handles all TCP communication with the ARCP Server.
                   It acts as the bridge between the network and the UI.

Functions:
    - connect: Establishes the initial TCP connection with the server.
    - post_auth_setup: Opens the P2P media port and the CallManager UDP socket to start background listeners.
    - login: Handles existing user credential verification via framed messaging.
    - register: Handles new user credential verification via framed messaging.
    - check_user: Verifies if the user exists in the system.
    - send_dm: Sends text via the server to another user.
    - send_group_msg: Sends a text via the server to another group of users at once.
    - send_file: Requests peer IP/Port from the server to initiate a Diret P2P transfer.
    - request_call: Signals the server to notify a recipient of an incoming call.

Date: 15-03-2026
"""
import queue
import CallManager
import socket
import ProtocolHandler
import threading
import os
import base64

# ------------ SERVER CONFIGURATION -----------------

SERVER_IP   = '196.47.192.177'
TCP_PORT    = 50000

class NetworkClient:

    def __init__(self, gui_queue: queue.Queue):
        self.gui_queue         = gui_queue
        self.tcp_sock          = None
        self.media_sock        = None
        self.call_manager      = CallManager.CallManager(gui_queue)
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
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"CHECK:{username}", 'A')
        _, resp = ProtocolHandler.receive_framed_msg(self.tcp_sock)
        return resp or "ERROR"

    def login(self, username: str, password: str) -> str:
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"LOGIN:{username}:{password}", 'A')
        _, resp = ProtocolHandler.receive_framed_msg(self.tcp_sock)
        return resp or "ERROR"

    def register(self, username: str, password: str) -> str:
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"REG:{username}:{password}", 'A')
        _, resp = ProtocolHandler.receive_framed_msg(self.tcp_sock)
        return resp or "ERROR"

    def post_auth_setup(self, username: str):
        """Register ports with server, start all background threads."""
        self.username = username

        self.media_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.media_sock.bind(('0.0.0.0', 0))
        self.media_sock.listen()
        media_port = self.media_sock.getsockname()[1]

        udp_port = self.call_manager.create_udp_socket()

        ProtocolHandler.send_framed_msg(self.tcp_sock, f"PORT:{media_port}", 'A')
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"CALL_PORT:{udp_port}", 'A')

        threading.Thread(target=self._recv_tcp_media, daemon=True).start()
        threading.Thread(target=self._recv_tcp_messages, daemon=True).start()
        
        # Start the UDP Dispatcher mailroom
        self.call_manager.start_dispatcher()

        # Deliver any queued offline messages immediately
        ProtocolHandler.send_framed_msg(self.tcp_sock, "FLUSH_OFFLINE:", 'C')

    # ── Messaging ─────────────────────────────────────────────────────────────

    def send_dm(self, recipient: str, text: str):
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"SEND:{recipient}:{text}", 'D')

    def send_group_msg(self, group_id: str, text: str):
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"SEND_GROUP:{group_id}:{text}", 'D')

    # ── File transfer ─────────────────────────────────────────────────────────

    def send_file(self, recipient: str, filepath: str):
        filename = os.path.basename(filepath)
        self.pending_transfers[recipient] = filepath
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"GET_PEER:{recipient}:{filename}", 'C')

    # ── Groups ────────────────────────────────────────────────────────────────

    def create_group(self, group_name: str):
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"CREATE_GROUP:{group_name}:", 'C')

    def add_to_group(self, group_name: str, target_user: str):
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"ADD_TO_GROUP:{group_name}:{target_user}", 'C')

    def leave_group(self, group_name: str):
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"LEAVE_GROUP:{group_name}:", 'C')

    def verify_group(self, group_name: str):
        """Probe the server to check whether this client is a member of group_name.
        The server responds with ADD_STATUS:... (member/exists) or
        GROUP NOT FOUND OR NOT MEMBER (doesn't exist / not a member).
        """
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"ADD_TO_GROUP:{group_name}:{self.username}", 'C')

    # ── Calls ─────────────────────────────────────────────────────────────────

    def request_call(self, recipient: str, call_type: str):
        self.current_call_type = call_type
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"{call_type.upper()}_CALL:{recipient}", 'C')

    def accept_call(self, caller: str):
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"CALL_ACCEPT:{caller}", 'C')

    def reject_call(self, caller: str):
        ProtocolHandler.send_framed_msg(self.tcp_sock, f"CALL_REJECT:{caller}", 'C')

    def end_call(self):
        self.call_manager.end_call()

    # ── Disconnect ────────────────────────────────────────────────────────────

    def disconnect(self):
        self.shutting_down = True
        try: 
            ProtocolHandler.send_framed_msg(self.tcp_sock, "EXIT:", 'C')
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
                msg_type, msg = ProtocolHandler.receive_framed_msg(self.tcp_sock)
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
                    # Run in its own thread so the TCP receive loop is never
                    # blocked by the hole-punch wait (up to 10 s).
                    threading.Thread(
                        target=self.call_manager.start_outgoing_call,
                        args=(peer_ip, peer_udp_port, self.current_call_type),
                        daemon=True
                    ).start()
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
                if msg.startswith("AUDIO_CALL:") or msg.startswith("VIDEO_CALL:"):
                    parts     = msg.split(":")
                    caller    = parts[1]
                    call_type = "audio" if msg.startswith("AUDIO_CALL:") else "video"
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
                    ProtocolHandler.send_framed_msg(self.tcp_sock, f"DOWNLOAD_MEDIA:{media_id}:", 'C')
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
            ftype  = ProtocolHandler.get_file_type(filepath)

            with open(filepath, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
            ProtocolHandler.send_framed_msg(self.tcp_sock, f"UPLOAD_MEDIA:{recipient}:{filename}|{ftype}|{b64}", 'D')
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
            self.gui_queue.put(('FILE_RECEIVED', filename, ProtocolHandler.get_file_type(filename), save_path, sender))
        
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
