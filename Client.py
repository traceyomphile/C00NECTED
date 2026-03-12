"""
Client.py - A Python client for a chat application with P2P media transfer capabilities.

Transport layer:
- TCP   :   used for all file/media transfers (images, PDFs, audio files, video files).
- UDP   :   used exclusively for real-time audio/video call streaming.

Media type handling (receive_tcp_media):
- Images    :   .jpg / .jpeg / .png / .gif / .bmp / .webp
- PDFs  :   .pdf
- Audio :   .mp3 / .wav / .flac / .ogg / .acc
- Video :   .p4 / .avi / .mov / .mkv / .webm    -> Capped at 45sec

Functions:
-   get_file_type        : Classifies a filename into image/pdf/audio/video/unknown.
-   validate_video_length: Checks that a video file does not exceed MAX_VIDEO_SECONDS.
-   receive_tcp_media    : Accepts incoming TCP connections and saves files by type.
-   send_file_tcp        : Sends a file to a peer over TCP with a newline-framed header.
-   stream_audio_udp     : Captures microphone audio and sends datagrams to the peer.
-   receive_audio_udp    : Receives audio datagrams and plays them.
-   stream_video_udp     : Captures webcam frames and sends them as UDP datagrams.
-   receive_video_udp    : Receives video datagrams and displays them.
-   start_call_udp       : Launches all four UDP call threads (send+recv audio/video).
-   listen_for_call_udp  : Waits for the first incoming UDP packet to start a call.
-   send_framed_msg      : Frames and sends a control/data message over TCP.
-   receive_framed_msg   : Reads and unframes a TCP control/data message.
-   receive_tcp_messages : Background thread: handles server push messages.
-   authenticate_console : Interactive login / registration flow.
-   print_commands       : Prints the help menu.
-   start_client         : Entry-point - wires everything together.

Date: 2026-03-11
"""

import socket
import threading
import os
import base64
import struct
import pyaudio
import cv2
import pickle

# ----------------------------------------------
# CONFIGURATION
# ----------------------------------------------

SERVER_IP = socket.gethostbyname(socket.gethostname())
TCP_PORT = 50000

# --------------------------------------
# GRACEFUL SHUTDOWN FLAG
# --------------------------------------
shutting_down = False

# Maximum allowed video length for outgoing transfers (seconds)
MAX_VIDEO_SECONDS = 45

# UDP call packet-type prefixes (single byte)
PKT_AUDIO = b'\x01'
PKT_VIDEO = b'\x02'
PKT_END = b'\xFF'

# Audio stream settings (shared by send and receive)
AUDIO_FORMAT = pyaudio.paInt16  # 16-bit PCM - compatible across platforms
AUDIO_CHANNELS = 1
AUDIO_RATE = 44100
AUDIO_CHUNK = 1024

# --------------------------------------------------
# CALL STATE (MODULE-LEVEL SO THREADS CAN SHARE IT)
# ---------------------------------------------------
pending_caller = None
call_ended = False
call_udp_sock: socket.socket | None = None      # The single UDP socket used for a call
call_peer_addr: tuple | None = None

# ---------------------------------------------------
# PENDING P2P FILE_TRANSFER REGISTRY
# ---------------------------------------------------
pending_transfers: dict[str, str] = {}

# ---------------------------------
# FILE TYPE HELPERS
# ---------------------------------

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.acc'}
VIDEO_EXTS = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
PDF_EXTS = {'.pdf'}


def get_file_type(filename: str) -> str:
    """
    Classifies a filename by its extension.
    Parameters:
        - filename : str -> Represents the name of the file
    Returns:
        - str : Representing the file extension
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext in IMAGE_EXTS:
        return 'image'
    if ext in PDF_EXTS:
        return 'pdf'
    if ext in AUDIO_EXTS:
        return 'audio'
    if ext in VIDEO_EXTS:
        return 'video'
    return 'unknown'

def validate_video_length(filepath: str) -> tuple[bool, float]:
    """
    Opens a video file with OpenCV and cheks its duration.
    Parameters:
        - filepath : str -> Represents the filepath of the video.
    Returns:
        - tuple[bool, float] : Representing the OK status and duration in seconds.
    """
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        return False, 0.0
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    framecount = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()

    if fps <= 0:
        return False, 0.0
    
    duration = framecount / fps
    return duration <= MAX_VIDEO_SECONDS, duration


# --------------------------
# TCP MEDIA - RECEIVE SIDE
# --------------------------

def receive_tcp_media(listen_sock: socket.socket) -> None:
    """
    Accepts incoming TCP connections for media transfer.
    Each accpeted connection is handled in its own thread
    so concurrent transfers do not block each other

    Protocol (sender side, see send_file_tcp):
        Header line (UTF-8, newline-terminated):
            FILE:<filename>:<filesize_bytes>\n
        Followed immediately by <filesize_bytes> raw bytes of file data.

    Files are saved into a 'received/' subdirectory with their original extension
    preserved so the OS opens them with the correct application.
    Parameters:
        - listen_sock: The socket bound to the client's unique port for receiving media.
    Returns:
        - None. The function runs indefinitely until an EOF signal is received, at which point it closes the socket.
    """
    os.makedirs("received", exist_ok=True)

    while True:
        conn, addr = listen_sock.accept()
        threading.Thread(
            target=_handle_incoming_file,
            args=(conn, addr),
            daemon=True
        ).start()

def _handle_incoming_file(conn: socket.socket, addr: tuple) -> None:
    """
    Handles a single incoming file connection.
    Parses the header, determines the file type from the extension,
    and writes the payload to disk under received/<filename>.
    Parameters:
        - conn : socket.socket -> Represents the sender's unique port.
        - addr : tuple   -> Represents the recipient's ip_add and unique port.
    Returns:
        - None.
    """
    try:
        raw_header = b''
        while True:
            byte = conn.recv(1)
            if not byte or byte == b'\n':
                break
            raw_header += byte

        if not raw_header:
            return
        
        header = raw_header.decode('utf-8', errors='replace')
        parts = header.split(":")

        if len(parts) != 3 or parts[0] != 'FILE':
            print(f"[MEDIA] Unexpected header from {addr}: {header!r}")
            return

        filename = parts[1]
        filesize = int(parts[2])
        file_type = get_file_type(filename)

        save_path = os.path.join("received", filename)

        # Avoid overwriting existing files by appending a counter
        base, ext = os.path.splitext(save_path)
        counter = 1
        while os.path.exists(save_path):
            save_path = f"{base}_{counter}-{ext}"
            counter += 1

        print(f"\n[MEDIA] Receiving {file_type} {filename} ({filesize} bytes) from {addr}.")

        bytes_received = 0

        with open(save_path, "wb") as f:
            while bytes_received < filesize:
                chunk = conn.recv(min(4096, filesize - bytes_received))
                if not chunk:
                    break

                f.write(chunk)
                bytes_received += len(chunk)

        if bytes_received < filesize:
            print(f"[MEDIA] Warning: expected {filesize} bytes, received {bytes_received}.")
        else:
            print(f"[MEDIA] Saved {file_type} -> {save_path}")

    except Exception as e:
        print(f"[ERROR] Media reception failed: {e}")

    finally:
        conn.close()


# ----------------------------
# TCP MEDIA - SEND SIDE
# ----------------------------

def send_file_tcp(filepath: str, target_ip: str, target_port: int) -> None:
    """
    Sends a file to a peer over a fresh TCP connection.

    Validates before sending:
        - The file exists on disk.
        - Videos do not exceed MAX_VIDEO_SECONDS.

    Protocol:
        Header line (UTF-8, newline-terminated):
            FILE:<filename>:<filesize_bytes>\n
        Followed immediately by the raw file bytes.

    Parameters:
        - filepath : str -> Local path to the file being sent.
        - target_ip : str ->  The IP address of the receiving peer.
        - target_port : int -> TCP port the receiver is listening on.
    Returns:
        - None.
    """
    if not os.path.isfile(filepath):
        print(f"[ERROR] File not found: {filepath}")

    file_type = get_file_type(filepath)

    # Enforce video length cap before opening a socket
    if file_type == 'video':
        ok, duration = validate_video_length(filepath)

        if not ok:
            if duration == 0.0:
                print(f"[ERROR] Could not read video file: {filepath}")
            else:
                print(
                    f"[ERROR] Video is {duration:.1f}s - exceeds the "
                    f"{MAX_VIDEO_SECONDS}s limit. Transfer cancelled."
                )

    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((target_ip, target_port))

        header = f"FILE:{filename}:{filesize}\n"
        sock.sendall(header.encode('utf-8'))

        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                sock.sendall(chunk)

        print(f"[MEDIA] Sent {file_type} '{filename}' ({filesize:,} bytes) -> {target_ip}:{target_port}")

    except Exception as e:
        print(f"[ERROR] File transfer failed: {e}")

    finally:
        sock.close()

def upload_file_for_offline(sock: socket.socket, recipient: str, filepath: str) -> None:
    """
    Base64-encodes a local file and sends it to the server via UPLOAD_MEDIA so it can be stored
    in SQLite and delivered to an offline recipient on their next login.

    Parameters:
        - sock : The TCP control socket connected to the server.
        - recipient : Username or group_id the file is destined for.
        - filepath : Local path of the file to upload.

    Returns:
        - None
    """
    try:
        filename = os.path.basename(filepath)
        filetype = get_file_type(filepath)

        with open(filepath, 'rb') as f:
            b64_data = base64.b64encode(f.read()).decode('ascii')
        
        send_framed_msg(sock, f"UPLOAD_MEDIA:{recipient}:{filename}|{filetype}|{b64_data}", 'D')
    
    except Exception as e:
        print(f"[ERROR] Failed to store file for offline delivery: {e}")

    finally:
        # Clean up pending entry regardless of outcome
        pending_transfers.pop(recipient, None)

# ------------------------------------------------------------------------------
# UDP CALL STREAMING
# ------------------------------------------------------------------------------
# One UDP socket is shared for both audio and video in each direction.
# Each datagram is prefixed with a 1-byte packer-type:
#   0x01 (PKT_AUDIO)    -   followed by raw PCM bytes
#   0x02 (PKT_VIDEO)    -   followed by 4-byte big-endian payload length
#                           + pickled OpenCV frame 
#   0xFF (PKT_END)      - signals the remote side to stop
# ------------------------------------------------------------------------------   

def stream_audio(udp_sock: socket.socket, peer_addr: tuple) -> None:
    """
    Captures microphone input and sends PCM audio datagrams to peer_addr.
    Stops when the global call_ended flag is set.
    Parameters:
        - udp_sock : socket.socket -> Represents the listening udp socket
        - peer_addr : tuple -> Represnts the peer's ip and port number.
    Returns:
        - None.
    """
    global call_ended

    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=AUDIO_FORMAT,
        channels=AUDIO_CHANNELS,
        rate=AUDIO_RATE,
        input=True,
        frames_per_buffer=AUDIO_CHUNK
    )

    udp_sock.setblocking(False)

    try:
        while not call_ended:
            try:
                pcm_data = stream.read(AUDIO_CHUNK, exception_on_overflow=False)
            except Exception:
                continue

            packet = PKT_AUDIO + pcm_data

            try:
                udp_sock.sendto(packet, peer_addr)
            except OSError:
                if not call_ended:
                    print("[CALL] UDP send failed")
                    break

    except Exception as e:
        if not call_ended:
            print(f"[CALL] Audio send error: {e}")
    
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass

        audio.terminate()

def receive_audio(udp_sock: socket.socket) -> None:
    """
    Receives audio datagrams from the UDP socket and plays them.
    Stops when called_ended is set or a PKT_END datagram arrives.
    Video packets that arrive on this socket are silently skipped
    (the video receiver thread handles them separately).
    Parameters:
        - udp_sock : socket.socket -> Listening UDP socket
    Returns:
        - None.
    """
    global call_ended

    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=AUDIO_FORMAT,
        channels=AUDIO_CHANNELS,
        rate=AUDIO_RATE,
        output=True,
        frames_per_buffer=AUDIO_CHUNK
    )

    # Set timeout so recvfrom doesn't block forever
    udp_sock.settimeout(0.5)

    try:
        while not call_ended:
            try:
                datagram, _ = udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception as e:
                if not call_ended:
                    print(f"[CALL] UDP receive error: {e}")
            
            if not datagram:
                continue
            
            pkt_type = datagram[0:1]
            payload = datagram[1:]

            if pkt_type == PKT_END:
                break
            if pkt_type == PKT_AUDIO:
                if payload:
                    try:
                        stream.write(payload)
                    except Exception as e:
                        print(f"[CALL] Audio playback error: {e}")
            else:
                continue

    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()

def stream_video_udp(udp_sock: socket.socket, peer_addr: tuple) -> None:
    """
    Captures webcam frames, pickles them, and sends them as UDP datagrams.
    Datagram format: PKT_VIDEO + 4-byte big endian length + pickled frame.
    Stops when call_ended is set or the camera connot supply frames.
    Parameters:
        - udp_sock : socket.socket -> Listening UDP port
    Returns:
        - None
    """
    global call_ended

    cap = cv2.VideoCapture(0)

    try:
        while not call_ended:
            ret, frame = cap.read()
            if not ret:
                break

            payload = pickle.dumps(frame, protocol=4)
            length = struct.pack('>I', len(payload))
            udp_sock.sendto(PKT_VIDEO + length + payload, peer_addr)

    except Exception as e:
        if not call_ended:
            print(f"[CALL] Video sent error: {e}")
    finally:
        cap.release()

def receive_video_udp(udp_sock: socket.socket):
    """
    Receives video datagrams from the UDP socket, unpickles the frames, and displays them with OpenCV.
    Stops when call_ended is set, ESC is pressed, or PKT_END arrives.
    Parameters:
        - upd_sock : socket.socket -> Listening UDP socket
    Returns:
        - None
    """
    global call_ended

    try:
        while not call_ended:
            try:
                datagram, _ = udp_sock.recvfrom(65535)
            except socket.timeout:
                continue

            if not datagram:
                break

            pkt_type = datagram[0:1]
            if pkt_type == PKT_END:
                break
            if pkt_type != PKT_VIDEO:
                continue    # audio packet ont this recvfrom - skip

            # Validate minimum datagram size (1 byte + 4 length bytes)
            if len(datagram) < 5:
                continue

            length = struct.unpack('>I', datagram[1:5])[0]
            payload = datagram[5:5 + length]

            if len(payload) < length:
                continue

            frame = pickle.loads(payload)
            cv2.imshow("Video Call", frame)

            if cv2.waitKey(1) == 27:
                call_ended = True
                break

    except Exception as e:
        if not call_ended:
            print(f"[CALL] Video receive error: {e}")

    finally:
        cv2.destroyAllWindows()

def start_call_udp(peer_ip: str, peer_udp_port: int, my_udp_sock: socket.socket) -> None:
    """
    Initiates the outgoing half of a UDP call.
    Sets the 0.5 s socket timeout so all receiver threads can periodically
    check call_ended without blocking forever, then launches four threads:
        - audio send, audio receive, video send, video receive.

    Parameters:
        - peer_ip   : IP address of the remote peer.
        - peer_udp_port : UDP port the remote peer is bound to.
        - my_udp_sock   : The already-bound UDP socket for this client.

    Returns:
        - None.
    """
    global call_ended, call_udp_sock, call_peer_addr

    call_ended = False
    call_udp_sock = my_udp_sock
    call_peer_addr = (peer_ip, peer_udp_port)

    print(f"[CALL] Starting UDP call with {peer_ip}:{peer_udp_port} ...")

    threading.Thread(target=stream_audio, args=(my_udp_sock, call_peer_addr), daemon=True).start()
    threading.Thread(target=receive_audio, args=(my_udp_sock,), daemon=True).start()
    threading.Thread(target=stream_video_udp, args=(my_udp_sock, call_peer_addr), daemon=True).start()
    threading.Thread(target=receive_video_udp, args=(my_udp_sock,), daemon=True).start()

def listen_for_call_udp(my_udp_sock: socket.socket) -> None:
    """
    Background thread passively waits for the first incoming UDP packet.
    When a PKT_AUDIO or PKT_VIDEO datagram arrives from a new address,
    records the peer address and starts the receive-side threads so 
    callee can hear and see the caller without needing a separate handshake.
    Runs indefinitely as a daemon thread.
    Parameters:
        - my_udp_sock : socket.socket -> The listening UDP socket.
    Returns:
        - None.
    """
    global call_ended, call_peer_addr

    my_udp_sock.settimeout(1.0)

    while True:
        try:
            datagram, addr = my_udp_sock.recvfrom(65535)
        except socket.timeout:
            continue
        except Exception:
            break

        if not datagram:
            continue

        pk_type = datagram[0:1]
        if pk_type not in (PKT_AUDIO, PKT_VIDEO):
            continue

        # First packer from a new peer - activate receive side
        if call_peer_addr != addr:
            call_peer_addr = addr
            call_ended = False
            print(f"\n[CALL] Incoming UDP call from {addr[0]}:{addr[1]}")

            my_udp_sock.settimeout(0.5)
            threading.Thread(target=receive_audio, args=(my_udp_sock,), daemon=True).start()
            threading.Thread(target=receive_video_udp, args=(my_udp_sock,), daemon=True).start()


# -------------------------------------------------------
# TCP CONTROL MESSAGE FRAMING (shared with server)
# --------------------------------------------------------

def send_framed_msg(sock: socket.socket, message: str, msg_type: str='D') -> None:
    """
    Frames a message with a 5-byte header and sends it over TCP.
    Header format: [Type (1 ASCII char)][Lenght (4 decimal digit ASCII)]
    Parameters:
        - sock : The TCP socket through which the message will be sent.
        - message : The string message to be sent.
        - msg_type : A single-character indicating the type of message (default is 'D').
    Returns:
        - None.
    """
    data = message.encode('ascii')
    header = f"{msg_type}{len(data):08d}".encode('ascii')
    sock.sendall(header + data)

def receive_framed_msg(sock: socket.socket) -> tuple[str, str] | tuple[None, None]:
    """
    Reads a framed TCP message.
    Parameters:
        - sock: The TCP socket from which the message will be received.
    Returns:
        - tuple[str, str] | tuple[None, None] -> Representing msg_type, content on normal behaviour.
    """
    header = sock.recv(9)
    if not header or len(header) < 9: 
        return None, None
    
    msg_type = header[0:1].decode('ascii')
    msg_len = int(header[1:9].decode('ascii'))
    
    data = b''
    while len(data) < msg_len:
        packet = sock.recv(msg_len - len(data))

        if not packet: 
            break

        data += packet

    return msg_type, data.decode('ascii')

# -----------------------------------------------------
# SERVER PUSH MESSAGE HANDLER (BACKGROUND THREAD)
# ------------------------------------------------------

def receive_tcp_messages(sock: socket.socket, my_udp_socket: socket.socket) -> None:
    """
    Listens for messages pushed by the server over the control TCP connection.
    Handles:
        - PEER_INFO : triggers a P2P file transfer to a single user.
        - GROUP_PEER_INFO : triggers parallel P2P file transfers to all group members.
        - AUDIO_CALL /
          VIDEO_CALL    : incoming call notification; prompts the user to accept.
        - CALL_PEER_INFO   : server returns peer UDP address; starts the UDP call.

    Parameters:
        - sock : socket.socket -> The TCP control socket.
        - my_udp_sock : socket.socket -> The bound UDP socket (passed to start_call_udp)
    Returns:
        - None.
    """
    global pending_transfers

    while True:
        try:
            msg_type, msg = receive_framed_msg(sock)
            if not msg: 
                break
            
            # --- P2P: Direct User File Transfer ---
            if msg_type == 'C' and msg.startswith("PEER_INFO:"):
                _, target_user, t_ip, t_port = msg.split(':')
                if target_user in pending_transfers:
                    filepath = pending_transfers.pop(target_user)
                    print(f"\n[SYSTEM] Peer found! Initiating P2P transfer of {filepath} to {target_user}...")
                    threading.Thread(target=send_file_tcp, args=(filepath, t_ip, int(t_port)), daemon=True).start()
                continue

            # --- P2P: Group Broadcast File Transfer ---
            if msg_type == 'C' and msg.startswith("GROUP_PEER_INFO:"):
                parts = msg.split(':', 2)
                group_name = parts[1]
                peers_str = parts[2]
                if group_name in pending_transfers:
                    filepath = pending_transfers.pop(group_name)
                    print(f"\n[SYSTEM] Group members found! Initiating Multicast P2P transfer to '{group_name}'...")
                    for peer_str in peers_str.split('|'):
                        ip, port = peer_str.split(',')
                        threading.Thread(target=send_file_tcp, args=(filepath, ip, int(port)), daemon=True).start()
                continue

            # ------- Call Signalling: Incoming Call Notification -----------
            if msg.startswith("AUDIO_CALL:") or msg.startswith("VIDEO_CALL:"):
                global pending_caller  #I was here
                
                caller = msg.split(":")[1]
                pending_caller = caller
                call_type = "audio" if msg.startswith("AUDIO_CALL:") else "video"

                print(f"\n[CALL] Incoming {call_type} call from {caller}")
                print("Type ACCEPT_CALL or REJECT_CALL")

            # ----------- Call SignallingL Server returns Peer UDP address --------------
            if msg_type == 'C' and msg.startswith("CALL_PEER_INFO:"):
                parts = msg.split(":")

                peer_user = parts[1]
                peer_ip = parts[2]
                peer_udp_port = int(parts[3])
                call_type = parts[4]

                print(f"\n[CALL] Ringing {peer_user}")

                threading.Thread(
                    target=start_call_udp,
                    args=(peer_ip, peer_udp_port, my_udp_socket),
                    daemon=True
                ).start()
                continue

            # ----- Call Signalling: callee accepted our call ---------
            if msg_type == 'C' and msg.startswith("CALL_ACCEPTED:"):
                callee = msg.split(":")[1]
                print(f"\n[CALL] {callee} accepted your call!")

                send_framed_msg(sock, f"GET_CALL_PEER:{callee}", 'C')
                continue

            # ----- Call Signalling: callee rejected our call ---------
            if msg_type == 'C' and msg.startswith("CALL_REJECTED:"):
                callee = msg.split(":")[1]
                print(f"\n[CALL] {callee} declined your call.")
                continue

            # ----------- Session timeout: server is closing the connection -------------
            if msg_type == 'C' and msg.startswith("TIMEOUT"):
                reason = msg.split(":", 1)[1]
                print(f"\n[SYSTEM] {reason}")
                print("[SYSTEM] You have been disconnected. Please restart the client to reconnect.")
                break

            # ------------------- Offline Users -----------------
            if msg_type == 'C' and msg.startswith("USER_OFFLINE:"):
                parts = msg.split(":", 2)
                
                offline_user = parts[1]
                last_seen = parts[2] if len(parts) > 2 else "unknown"
                pending_transfers.pop(offline_user, None)

            # ------------ Offline File Storage ----------------
            if msg_type == 'C' and msg.startswith("STORE_OFFLINE:"):
                offline_target = msg.split(":", 1)[1]

                filepath = pending_transfers.get(offline_target)
                if filepath:
                    print(f"\n[SYSTEM] '{offline_target}' is offline. Uploading file for delivery...")
                    threading.Thread(
                        target=upload_file_for_offline,
                        args=(sock, offline_target, filepath),
                        daemon=True
                    ).start()
                continue

            # ------------- Offline File Notification --------------
            if msg.startswith("MEDIA_WAITING:"):
                parts = msg.split(":", 3)
                media_id, sender, filename = parts[1], parts[2], parts[3]

                print(f"\n[OFFLINE FILE] '{sender}' sent you '{filename}' while you were offline. Downloading...")
                send_framed_msg(sock, f"DOWNLOAD_MEDIA:{media_id}:", 'C')
                continue

            # ------------- Offline File Download ------------------
            if msg_type == 'D' and msg.startswith("FILE:"):
                parts = msg.split(":", 3)

                if len(parts) == 4:
                    _, filename, filetype, b64_data = parts

                    os.makedirs("received", exist_ok=True)
                    save_path = os.path.join("received", filename)
                    base_p, ext = os.path.splitext(save_path)

                    counter = 1
                    while os.path.exists(save_path):
                        save_path = f"{base_p}_{counter}{ext}"
                        counter += 1
                    
                    with open(save_path, 'wb') as f:
                        f.write(base64.b64decode(b64_data))
                    print(f"\n[OFFLINE LINE] Saved '{filename}' ({filetype}) -> {save_path}")
                continue

            # ------------ All Other Server Messages ----------
            print(f"\n{msg}")

        except Exception as e:
            if not shutting_down:
                print(f"\n[ERROR] Connection lost: {e}")
            break


# ------------------------------------------------
# AUTHENTIFICATION
# ------------------------------------------------

def authenticate_console(tcp_sock: socket.socket) -> str | None:
    """
    Interactive console login / registration flow.
    Parameters:
        - tcp_sock: The TCP socket through which authentication messages are sent and received.
    Returns:
        - The authenticated username as a string if login/registration is successful, or None if the user chooses to exit or authentication fails.
    """
    while True:
        username = input("Enter username: ").strip()
        send_framed_msg(tcp_sock, f"CHECK:{username}", 'A')
        _, resp = receive_framed_msg(tcp_sock)
        
        if resp == "EXISTS":
            while True:
                pwd = input(f"Enter password for {username} (or 'exit' to quit): ").strip()
                if pwd.lower() == 'exit':
                    return None
                
                send_framed_msg(tcp_sock, f"LOGIN:{username}:{pwd}", 'A')
                _, auth_resp = receive_framed_msg(tcp_sock)
                
                if auth_resp == "SUCCESS":
                    print("\n[SYSTEM] Login successful! Welcome to the Chat.")
                    return username
                
                elif auth_resp == "ALREADY ONLINE":
                    print(f"[ERROR] This account is already logged in elsewhere.")
                    return None
                
                else:
                    print("[ERROR] Incorrect password. Try again.")
                    
        elif resp == "NOT_FOUND":
            reg = input("User not found. Do you want to register? (yes/no): ").strip().lower()
            if reg == 'yes':
                while True:
                    new_user = input("Enter a unique username: ").strip()
                    send_framed_msg(tcp_sock, f"CHECK:{new_user}", 'A')
                    _, check_resp = receive_framed_msg(tcp_sock)
                    
                    if check_resp == "EXISTS":
                        print("[ERROR] Username already taken. Try another.")
                    else:
                        while True:
                            new_pwd = input("Enter new password: ").strip()
                            send_framed_msg(tcp_sock, f"REG:{new_user}:{new_pwd}", 'A')
                            _, reg_resp = receive_framed_msg(tcp_sock)
                            
                            if reg_resp is None:
                                print("[ERROR] Lost connection to server durring registration.")
                                return None

                            if reg_resp == "SUCCESS":
                                print("\n[SYSTEM] Registration successful! Welcome to the Chat.")
                                return new_user
                            
                            elif reg_resp.startswith("WEAK_PASSWORD:"):
                                reason = reg_resp.split(":", 1)[1]
                                print(f"[ERROR] {reason}")
                                print(f"[INFO] Requirements: 8+ chars, uppercase, lowercase, digit, special character (e.g. !@#$..)")
                            
                            elif reg_resp == "USER_EXISTS":
                                print(f"[ERROR] Username '{new_user}' was just taken. Please choose another.")
                                break

                            else:
                                print(f"[ERROR] Registration failed: {reg_resp}")
                                break
            else:
                return None # Exits program if they decline registration


# --------------------------------
# HELP MENU
# ---------------------------------

def print_commands():
    """Helper function to print the interactive commands menu."""
    menu = (
        "\n--- Commands ---\n"
        "SEND:<user>:<message>            - Direct Message\n"
        "CREATE_GROUP:<group_name>        - Create a new group\n"
        "ADD_TO_GROUP:<group_name>:<user> - Add a user to a group\n"
        "LEAVE_GROUP:<group_name>         - Leave a group\n"
        "SEND_GROUP:<group_name>:<msg>    - Send a group message\n"
        "SEND_FILE:<user/group>:<filepath> - P2P file transfer (TCP)\n"
        "   Supported types:\n"
        "       Images: .jpg .jpeg .png ..gif .bmp .webp\n"
        "       PDF   : .pdf\n"
        "       Audio : .mp3 .wav .flac .ogg .aac\n"
        f"       Video : .mp4 .avi .mov .mkv .webm (max {MAX_VIDEO_SECONDS}s)\n"
        "AUDIO_CALL:<user>                - Start an audio call (UDP)\n"
        "VIDEO_CALL:<user>                - Start a video call (UDP)\n"
        "CALL_END                         - End the current call\n"
        "COMMANDS                         - Show this menu\n"
        "EXIT                             - Disconnect\n"
    )
    print(menu)


# --------------------------------
# ENTRY POINT
# --------------------------------

def start_client() -> None:
    """
    Initialises the client:
        1. Opens a TCP connection to the server.
        2. Runs the blocking authentication loop.
        3. Binds a TCP listener socket for incoming file/media transfers.
        4. Binds a UDP socket for real-time call streaming.
        5. Registers both ports with the server (PORT: then CALL_PORT)
        6. Starts all background threads.
        7. Enrers the main command loop.
    """
    # 1. Server TCP Connection
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.connect((SERVER_IP, TCP_PORT))
    
    # 2. Authentication
    username = authenticate_console(tcp_sock)
    if not username:
        print("Terminating program...")
        tcp_sock.close()
        return

    # 3. TCP listener for incoming file transfers
    media_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    media_sock.bind(('0.0.0.0', 0))
    media_sock.listen()
    my_media_port = media_sock.getsockname()[1]

    # 4. UDP socket for real-time call streaming
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(('0.0.0.0', 0))
    my_udp_port = udp_sock.getsockname()[1]

    # 5. Register both ports with the server
    #    Server reads PORT: first (TCP media port), then CALL_PORT: (UDP call port)
    send_framed_msg(tcp_sock, f"PORT:{my_media_port}", 'A')
    send_framed_msg(tcp_sock, f"CALL_PORT:{my_udp_port}", 'A')

    print(f"[SYSTEM] TCP media listener on port {my_media_port}")
    print(f"[SYSTEM] UDP call socket    on port {my_udp_port}")

    print_commands()

    # 6. Background threads
    threading.Thread(target=receive_tcp_media, args=(media_sock,), daemon=True).start()
    threading.Thread(target=receive_tcp_messages, args=(tcp_sock, udp_sock), daemon=True).start()

    # 6b. Ask the server to flush any message/files queued while we were offline.
    send_framed_msg(tcp_sock, "FLUSH_OFFLINE:", 'C')

    # 7. Main command loop
    while True:
        try:
            msg = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not msg:
            continue

        if msg.upper() == "EXIT":
            global shutting_down
            shutting_down = True
            send_framed_msg(tcp_sock, "EXIT:", 'C')
            break

        if msg.upper() == "COMMANDS":
            print_commands()
            continue

        # All commands follow <COMMAND>:<RECIPIENT>:<DATA>
        parts = msg.split(":", 2)
        command = parts[0].upper()
        recipient = parts[1] if len(parts) > 1 else ""
        data = parts[2] if len(parts) > 2 else ""

        if not recipient and command not in ("CALL_END",):
            print("[ERROR] Format: <COMMAND>:<RECIPIENT_ID>:<DATA>")
            continue

        # -------- File transfer (TCP) -----------
        if command == "SEND_FILE":
            if not data:
                print("[ERROR] Format: SENDFILE:<recipient_username/group>:<file_path>")
                continue

            filepath = data
            file_type = get_file_type(filepath)

            if file_type == 'unknown':
                print(
                    f"[ERROR] Unsupported file type. Supported:\n"
                    f" Images : {', '.join(sorted(IMAGE_EXTS))}\n"
                    f" PDF    : pdf\n"
                    f" Audio  : {', '.join(sorted(AUDIO_EXTS))}\n"
                    f" Video  : {', '.join(sorted(VIDEO_EXTS))}\n"
                )
                continue

            # Validate video length before asking the server for a peer
            if file_type == 'video':
                ok, duration = validate_video_length(filepath)
                if not ok:
                    if duration == 0.0:
                        print(f"[ERROR] Could not read video: {filepath}")
                    else:
                        print(
                            f"[ERROR] Video is {duration:.1f}s -"
                            f"exceeds the {MAX_VIDEO_SECONDS}s limit."
                        )
                    continue

            pending_transfers[recipient] = filepath
            filename = os.path.basename(filepath)
            send_framed_msg(tcp_sock, f"GET_PEER:{recipient}:{filename}", 'C')
        
        # ------ Direct Message -----------
        elif command == "SEND":
            if not data:
                print("[ERROR] Format: SEND:<recipient_username>:<message>")
                continue
            send_framed_msg(tcp_sock, f"SEND:{recipient}:{data}", 'D')

        # --------- Group Message -------------  
        elif command == "SEND_GROUP":
            if not data:
                print("[ERROR] Format: SEND_GROUP:<group_name>:<message>")
                continue
            send_framed_msg(tcp_sock, f"SEND_GROUP:{recipient}:{data}", 'D')

        # ---------- Group Management ---------------
        elif command in ["CREATE_GROUP", "LEAVE_GROUP"]:
            send_framed_msg(tcp_sock, f"{command}:{recipient}", 'C')
            
        elif command == "ADD_TO_GROUP":
            if not data:
                print("[ERROR] Format: ADD_TO_GROUP:<group_name>:<user_to_add>")
                continue
            send_framed_msg(tcp_sock, f"ADD_TO_GROUP:{recipient}:{data}", 'C')

        # ------------ Calls (UDP) ----------------
        elif command == "AUDIO_CALL":
            send_framed_msg(tcp_sock, f"AUDIO_CALL:{recipient}", 'C')

        elif command == "VIDEO_CALL":
            send_framed_msg(tcp_sock, f"VIDEO_CALL:{recipient}", 'C')

        elif command == "CALL_END":
            global call_ended, call_udp_sock, call_peer_addr

            call_ended = True
            if call_udp_sock and call_peer_addr:
                try:
                    call_udp_sock.sendto(PKT_END, call_peer_addr)
                except Exception:
                    pass
            print("[CALL] Call ended.")

        elif command == "ACCEPT_CALL":
            if pending_caller:
                send_framed_msg(tcp_sock, f"CALL_ACCEPT:{pending_caller}", 'C')
            else:
                print("No incoming call.")
            pending_caller = None

        elif command == "REJECT_CALL":
            if pending_caller:
                send_framed_msg(tcp_sock, f"CALL_REJECT:{pending_caller}", 'C')
                pending_caller = None

        # ------------- Internal peer lookup (manual / debug) -----------
        elif command == "GET_PEER":
            send_framed_msg(tcp_sock, f"GET_PEER:{recipient}", 'C')
        
        else:
            print("[ERROR] Unknown command. Type COMMANDS for help.")
    
    # Shutdown: Signal the background receive thread to stop by shutting down the TCP socket before closing it.
    try:
        tcp_sock.shutdown(socket.SHUT_RDWR)
    except:
        pass

    tcp_sock.close()

    try:
        media_sock.close()
    except Exception:
        pass

    try:
        udp_sock.close()
    except Exception:
        pass

    print("[SYSTEM] Disconnected.")

if __name__ == "__main__":
    start_client()