"""
ARCP Server Implementation

This module implements the ARCP server that handles client authentication, peer discovery, direct messaging, group chat management, and offline message queuing.
The server listens for incoming TCP connections from clients, processes framed messages according to the defined protocol, and maintains global data structures for user management and message routing. It also ensures thread-safe access to shared resources using locks.

Functions:
- send_framed_msg: Frames a message with a header and sends it over a socket.
- receive_framed_msg: Receives a framed message and returns the type and content.
- queue_offline_message: Helper function to append a message to a user's offline queue.
- flush_redis_queue: Checks for queued messages for a recipient and sends them upon login.
- handle_client: Manages the lifecycle of a client connection, including authentication and message processing.
- main_chat_loop: Processes incoming messages from the client after authentication.
- authenticate_client: Handles the authentication process for a new client connection.
- start_server: Initializes the TCP server and listens for incoming client connections.

Integrations:
- SQLite: persistent user, group, and media storage
- Redis: offline message queue and user presence
Date: 2026-03-11
"""

import socket
import threading
import re
import ChatServer
from infrastructure import redis_client, db_lock, get_db, initialise_database, hash_password, verify_password


# Server Configuration
HOST = socket.gethostbyname(socket.gethostname())
PORT = 50000

# Disconnect a client after 20 minutes of inactivity to free up resources.
INACTIVITY_TIMEOUT = 20 * 60

# ----------------------
# SQLITE FUNCTIONS
# ----------------------

def user_exists(username: str) -> bool:
    # Get connection
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("SELECT 1 FROM users WHERE username=?", (username,))
        return cur.fetchone() is not None
    finally:
        conn.close()
    
def auth_user(username: str, password: str) -> bool:
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("SELECT password FROM users WHERE username=?", (username,))
        row = cur.fetchone()

        return verify_password(password, row[0])
    finally:
        conn.close()
    
def register_user(username: str, password: str):
    hashed = hash_password(password)
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute("INSERT INTO users(username, password) VALUES(?, ?)", (username, hashed))
            conn.commit()
        finally:
            conn.close()

# -----------------------
# PASSWORD VALIDATION
# -----------------------

# Common weak passwords to explicitly block
_WEAK_PASSWORDS = {
    "password", "123456", "123456789", "qwerty", "abc123", "letmein", "monkey", "dragon",
    "111111", "baseball", "iloveyou", "trustno1", "1234567", "sunshine", "master",
    "123123", "welcome", "shadow", "ashley", "football", "jesus", "michael",
    "ninja", "mustang", "password1", "passw0rd", "password123"
}

def validate_password(password: str) -> tuple[bool, str]:
    """
    Enforces strong password requirements.
    
    Rules:
        - At least 8 characters long
        - At least one uppercase letter (A-Z)
        - At least one lowercase letter (a-z)
        - At least one digit (0-9)
        - At least one special character (!@#$%^&*...)
        - Not a commonly used weak password

    Parameters:
        - password: The password string to validate.

    Returns:
        - (True, "") if the password is strong.
        - (False, reason) describing the first failed rule.
    """
    if password.lower() in _WEAK_PASSWORDS:
        return False, "Password is too common. Choose a more unique password."

    if len(password) < 8:
        return False, "Password must be at least 8 characters long."

    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter."

    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter."
    
    if not re.search(r'\d', password):
        return False, "Password must contain at least one digit."

    if not re.search(r'[!@#$%^&*()_+\-|=\[\]{};\'\\"|,.<>/?`~]', password):
        return False, "Password must contain at least one special character."
    
    return True, ""

# --------------------
# REDIS OFFLINE QUEUE
# --------------------

def queue_offline_message(recipient: str, formatted_msg: str) -> None:
    redis_client.rpush(f"offline:{recipient}", formatted_msg)

def flush_redis_queue(client_socket: socket.socket, recipient: str) -> None:
    """
    Checks if there are any queued messages for the recipient in the Redis queue and sends them sequentially.
    Parameters:
        - client_socket: The socket to send the queued messages through.
        - recipient: The username of the recipient to check for queued messages.
    Returns:
        - None
    """

    key = f"offline:{recipient}"

    while True:
        msg = redis_client.lpop(key)
        if msg is None:
            break

        # FIX: Ensure msg is decoded to a string if fakeredis returns bytes
        if isinstance(msg, bytes):
            msg = msg.decode('utf-8', errors='replace')

        try:
            if msg.startswith("MEDIA_WAITING"):
                _, media_id, sender, filename = msg.split(":", 3)

                result = get_media(int(media_id))
                if result:
                    filename, filetype, b64_data = result
                    send_framed_msg(
                        client_socket,
                        f"FILE:{filename}:{filetype}:{b64_data}",
                        'D'
                    )
            else:
                send_framed_msg(client_socket, msg, 'D')
        except Exception as e:
            print(f"[ERROR] Failed to flush offline message for {recipient}: {e}")
            redis_client.lpush(key, msg)    # Re-queue on failure and stop
            break

# -----------------------
# Message Framing
# -----------------------
def send_framed_msg(sock: socket.socket, message: str, msg_type: str = 'D') -> None:
    """
    Frames a message with a header and sends it over the socket.
    Header Format: [Type (1 ASCII char)][Length (8 decimal digit ASCII)]
    Parameters:
        - sock: The socket to send the message through.
        - message: The content of the message to send.
        - msg_type: A single-character indicating the type of message (e.g., 'D', 'C', 'A').
    Returns:
        - None
    """
    data = message.encode('ascii')
    header = f"{msg_type}{len(data):08d}".encode('ascii')
    sock.sendall(header + data)

def receive_framed_msg(sock: socket.socket) -> tuple[str, str] | tuple[None, None]:
    """
    Receives a framed message and returns the type and content.
    Expects the same header format as send_framed_msg.
    Parameters:
        - sock: The socket to read from.
    Returns:
        - msg_type: The type of the message (e.g., 'D', 'C', 'A').
        - message: The content of the message as a string.
    """
    header = sock.recv(9)

    if not header or len(header) < 5: 
        return None, None

    msg_type = header[0:1].decode('ascii')
    msg_len = int(header[1:9].decode('ascii'))
    
    data = b''

    while len(data) < msg_len:
        packet = sock.recv(msg_len - len(data))

        if not packet: break
        data += packet

    return msg_type, data.decode('ascii')

# -----------------------------
# MEDIA STORAGE AND RETRIEVAL
# -----------------------------

def store_media(sender: str, filename: str, filetype: str, data_b64: str, recipient=None, group_id=None) -> int:
    """
    Stores a base64-encoded media file in SQLite media table.
    Parameters:
        - sender : The username of the uploading client.
        - filename : Original filename (e.g. "photo.jpg").
        - filetype : File type category (e.g. 'image', 'audio', 'video', 'pdf').
        - data_b64 : The base64-encoded data of the media file.
        - recipient : The username of the recipient (mutually exclusive with group_id).
        - group_id : The ID of the group (mutually exclusive with recipient).
    Returns:
        - media_id : The unique ID of the stored media record.
    """
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute("""
            INSERT INTO media (sender, recipient, group_id, filename, filetype, data)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (sender, recipient, group_id, filename, filetype, data_b64))

            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

def get_media(media_id: int) -> tuple[str, str, str] | None:
    """
    Retrieves a stored media file from the SQLite media table by its ID.
    Parameters:
        - media_id : The unique ID of the media record to retrieve.
    Returns:
        - A tuple containing (filename, filetype, data) if found, or None if not found.
    """
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT filename, filetype, data FROM media WHERE id=?",
            (media_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return row["filename"], row["filetype"], row["data"]

    finally:
        conn.close()

# ---------------------
# CLIENT HANDLING
# ---------------------

def authenticate_client(client_socket: socket.socket, addr) -> str | None:
    """
    Handles the authentication process for a new client connection.
    Parameters:
        - client_socket : The socket representing the client's connection.
        - addr : The address of the client.
    Returns:
        - The authenticated username, or None if authentication fails.
    """
    username = None

    try:
        while True:
            _, msg = receive_framed_msg(client_socket)
            if not msg: 
                return None# Disconnected during auth
            
            if msg.startswith("CHECK:"):

                check_user = msg.split(":")[1]

                if user_exists(check_user):
                    send_framed_msg(client_socket, "EXISTS", 'A')
                else:
                    send_framed_msg(client_socket, "NOT_FOUND", 'A')
                    
            elif msg.startswith("LOGIN:"):
                _, login_user, login_pwd = msg.split(":", 2)

                if not auth_user(login_user, login_pwd):
                    send_framed_msg(client_socket, "FAIL", 'A')
                    continue

                if ChatServer.get_user_presence(login_user):
                    send_framed_msg(client_socket, "ALREADY ONLINE", 'A')
                    continue

                username = login_user
                send_framed_msg(client_socket, "SUCCESS", 'A')
                break
                        
            elif msg.startswith("REG:"):
                _, reg_user, reg_pwd = msg.split(":", 2)

                if user_exists(reg_user):
                    send_framed_msg(client_socket, "USER_EXISTS", 'A')
                    continue

                ok, reason = validate_password(reg_pwd)
                if not ok:
                    send_framed_msg(client_socket, f"WEAK_PASSWORD:{reason}", 'A')
                    continue

                register_user(reg_user, reg_pwd)
                print(f"[REGISTERED USER] {reg_user}")

                username = reg_user
                send_framed_msg(client_socket, "SUCCESS", 'A')
                break
            
            else:
                continue

        # Force-clear any stale presence key from a previous session.
        ChatServer.set_user_offline(username)

        # Client sends PORT: (TCP media port) then CALL_PORT: (UDP call port) after auth
        _, port_msg = receive_framed_msg(client_socket)
        if not port_msg or not port_msg.startswith("PORT:"):
            print(f"[AUTH ERROR] Expected PORT: from {addr}, got: {port_msg!r}")
            return None
        
        tcp_media_port = int(port_msg.split(":")[1])

        _, call_port_msg = receive_framed_msg(client_socket)
        if not call_port_msg or not call_port_msg.startswith("CALL_PORT:"):
            print(f"[AUTH ERROR] Expected CALL_PORT: from {addr}, got: {call_port_msg!r}")
            return None
        udp_call_port = int(call_port_msg.split(":")[1])

        # Register with the global ChatServer logic using both ports.
        ChatServer.register_client(username, client_socket, addr[0], tcp_media_port, udp_call_port)
        ChatServer.set_user_online(username, addr[0], tcp_media_port, udp_call_port)

        print(f"[REGISTERED] {username} at {addr[0]} | TCP Media : {tcp_media_port} | UDP Call : {udp_call_port}")
        
        return username
    
    except Exception as e:
        print(f"[AUTH ERROR] {addr}: {e}")
        try:
            send_framed_msg(client_socket, f"ERROR: Authentication failed: Please reconnect.", 'A')
        except Exception:
            pass
        return None

def handle_client(client_socket: socket.socket, addr) -> None:
    """
    Handles the lifecycle of a client connection, including authentication and message processing.
    Parameters:
        - client_socket : The socket representing the client's connection.
        - addr : The address of the client.
    Returns:
        - None
    """
    username = None
    try:
        # Authenticate user and register in clients dict
        username = authenticate_client(client_socket, addr)

        if not username:
            return # Authentication failed or client disconnected
        
        # Immediately flush any offline messages waiting for them
        flush_redis_queue(client_socket, username)

        # Implement chat logic
        main_chat_loop(client_socket, username)
                            
    except Exception as e:
        print(f"[ERROR] with {addr}: {e}")

    finally:
        client_socket.close()

        if username: 
            ChatServer.remove_client(username)

            print(f"[DISCONNECTED] {username}")

# ----------------
# MAIN CHAT LOOP
# ----------------

def main_chat_loop(client_socket: socket.socket, username: str) -> None:
    """
    Main loop to process incoming messages from the client after authentication.
    Parameter:
        - client_socket: The socket representing the client's connection.
        - username: The authenticated username of the client.
    Returns:
        - None
    """
    client_socket.settimeout(INACTIVITY_TIMEOUT) # 20 minute inactivity timeout
    
    while True:
        try:
            _, full_message = receive_framed_msg(client_socket)
        except socket.timeout:
            # No activity for 20 minutes - warn the client then drop the connection.
            try:
                send_framed_msg(client_socket, "TIMEOUT: No activity for 20 minutes. Please reconnect.", 'C')
            except Exception:
                pass
            print(f"[TIMEOUT] {username} has been disconnected after {INACTIVITY_TIMEOUT // 60} minutes of inactivity.")
            break

        if not full_message: 
            break

        parts = full_message.split(":", 2)

        if len(parts) < 2:
            send_framed_msg(client_socket, "ERROR: Invalid message format.", 'C')
            continue

        command = parts[0].strip()
        recipient = parts[1]
        data = parts[2] if len(parts) > 2 else ""

        # ---- DIRECT MESSAGE ------
        if command == "SEND":
            # FEATURE: Prevent sending messages to non-existent users
            if not user_exists(recipient):
                send_framed_msg(client_socket, f"ERROR: User '{recipient}' does not exist in the system.", 'C')
                continue

            if recipient == username:
                timestamped_msg = f"[{ChatServer.get_timestamp()}] [You]: {data}"
                send_framed_msg(client_socket, timestamped_msg, 'D')
                send_framed_msg(client_socket, 'DELIVERED', 'C')
                print(f"[TEXT MESSAGE] {username} -> {username} (self)")
                continue

            target_online = ChatServer.get_user_presence(recipient)

            ChatServer.send_dm(username, recipient, data, send_framed_msg, queue_offline_message)
            status = "DELIVERED" if target_online else "QUEUED (offline)"
            print(f"[TEXT MESSAGE] {username} -> {recipient} | {status}")
            
            # FEATURE: Delivery Notification OR Last Seen feedback
            if target_online:
                send_framed_msg(client_socket, f"DELIVERED", 'C')
            else:
                last_seen_time = ChatServer.get_last_seen(recipient)
                send_framed_msg(client_socket, f"USER OFFLINE. {last_seen_time}", 'C')
        
        elif command == "SEND_GROUP":
            if not ChatServer.is_group(recipient):
                send_framed_msg(client_socket, f"ERROR: Group '{recipient}' does not exists.", 'C')
                continue

            print(f"[GROUP MESSAGE] {username} -> group '{recipient}'")

            status = ChatServer.send_group_message(username, recipient, data, send_framed_msg, queue_offline_message)
            
            if status != "SUCCESS":
                send_framed_msg(client_socket, f"ERROR: {status}", 'C')

        # --------- MEDIA UPLOAD ---------
        # Client sends: UPLOAD_MEDIA:<recipient_or_group>:<filename>|<base64_data>
        # Server replies: MEDIA_ID:<id> - the client can share this ID so the recipient can download.
        elif command == "UPLOAD_MEDIA":
            try:
                parts_data = data.split("|", 2)
                if len(parts_data) != 3:
                    send_framed_msg(client_socket, "ERROR: Format: UPLOAD_MEDIA:<target>:<filename>|<base64_data>", 'C')
                    continue

                filename, filetype, b64_data = parts_data

                # Determine whether the target is a group or a user
                if ChatServer.is_group(recipient):
                    media_id = store_media(username, filename, filetype, b64_data, group_id=recipient)
                    print(f"[MEDIA UPLOAD] {username} -> group '{recipient}' | {filetype} '{filename}' | ID: {media_id}")

                    # QUEUE MEDIA_WAITING for every offline group member
                    conn = get_db()
                    cur = conn.cursor()
                    try:
                        cur.execute("SELECT username FROM group_members WHERE group_id=?", (recipient,))
                        members = [row[0] for row in cur.fetchall()]
                    finally:
                        conn.close()
                    
                    for member in members:
                        if member == username:
                            continue

                        peer = ChatServer.get_user_presence(member)

                        if peer:
                            with ChatServer.clients_lock:
                                recipient_sock = ChatServer.clients.get(member)

                            if recipient_sock:
                                try:
                                    send_framed_msg(
                                        recipient_sock[0],
                                        f"FILE:{filename}:{filetype}:{b64_data}",
                                        'D'
                                    )
                                except Exception:
                                    pass

                        else:
                            queue_offline_message(member, f"MEDIA_WAITING:{media_id}:{username}:{filename}")

                else:
                    media_id = store_media(username, filename, filetype, b64_data, recipient=recipient)
                    print(f"[MEDIA UPLOAD] {username} -> {recipient} | {filetype} '{filename}' | ID: {media_id}")

                    # Queue MEDIA_WAITING for offline recipient
                    if not ChatServer.get_user_presence(recipient):
                        queue_offline_message(recipient, f"MEDIA_WAITING:{media_id}:{username}:{filename}")

                send_framed_msg(client_socket, f"MEDIA_ID:{media_id}", 'C')

            except Exception as e:
                send_framed_msg(client_socket, f"ERROR: Media upload failed: {str(e)}", 'C')

        # ------------- MEDIA DOWNLOAD -----------
        # Client sends: DOWNLOAD_MEDIA:<media_id>
        # Server replies: FILE:<filename>:<filetype>:<base64_data> or ERROR: ....
        elif command == "DOWNLOAD_MEDIA":
            try:
                media_id = int(recipient)
                result = get_media(media_id)

                if not result:
                    print(f"[MEDIA DOWNLOAD] {username} requested ID {media_id} | NOT FOUND")
                    send_framed_msg(client_socket, "ERROR: Media not found.", 'C')
                else:
                    filename, filetype, b64_data = result
                    print(f"[MEDIA DOWNLOAD] {username} requested ID {media_id} | {filetype} '{filetype}'")
                    send_framed_msg(client_socket, f"FILE:{filename}:{filetype}:{b64_data}", 'D')

            except ValueError:
                send_framed_msg(client_socket, "ERROR: Invalid media ID.", 'C')

            except Exception as e:
                send_framed_msg(client_socket, f"ERROR: Media download failed: {str(e)}", 'C')

        # -------------- GROUP MANAGEMENT -------------
        elif command == "CREATE_GROUP":
            created = ChatServer.create_group(recipient, username)
            if created:
                print(f"[GROUP CREATED] '{recipient}' by {username}")
            msg = "GROUP CREATED" if created else "GROUP EXISTS"
            send_framed_msg(client_socket, msg, 'C')

        elif command == "ADD_TO_GROUP":
            # 'data' holds the target username to add
            status = ChatServer.add_to_group(recipient, username, data)
            print(f"[GROUP ADD] {username} added {data} to '{recipient}' | {status}")
            
            # Feature: System Notification for Added User
            if status == "SUCCESS":
                sys_msg = f"You were added to group '{recipient}' by {username}."
                ChatServer.send_dm("SYSTEM", data, sys_msg, send_framed_msg, queue_offline_message)
                ChatServer.send_group_message("SYSTEM", recipient, f"{username} added {data} to the group.", send_framed_msg, queue_offline_message)
                
            send_framed_msg(client_socket, f"ADD_STATUS: {status}", 'C')

        elif command == "LEAVE_GROUP":
            left = ChatServer.leave_group(recipient, username)
            print(f"[GROUP LEAVE] {username} left '{recipient}' | {'OK' if left else 'NOT MEMBER'}")
            if left:
                # Feature: System Notification for Remaining Members
                ChatServer.send_group_message("SYSTEM", recipient, f"{username} has left the group.", send_framed_msg, queue_offline_message)
                send_framed_msg(client_socket, "LEFT GROUP", 'C')
            else:
                send_framed_msg(client_socket, "GROUP NOT FOUND OR NOT MEMBER", 'C')

        elif command == "GET_PEER":
            filename = data if data else "a file" 

            peer = ChatServer.get_user_presence(recipient)
            
            if peer:
                ip, tcp_media_port, _ = peer
                print(f"[FILE TRANSFER] {username} -> {recipient} | '{filename}' | routed to {ip}:{tcp_media_port}")
                send_framed_msg(client_socket, f"PEER_INFO:{recipient}:{ip}:{tcp_media_port}", 'C')

            else:
                # Feature: Group File Sharing Support
                if ChatServer.is_group(recipient):
                    online_peers, offline_members = ChatServer.get_group_presence(recipient, exclude_user=username)
                    print(f"[FILE TRANSFER] {username} -> group '{recipient}' | '{filename}' | {len(online_peers)} online, {len(offline_members)} offline")
                    
                    # 1. Uploads files for offline users, UPLOAD_MEDIA handles the queuing
                    if offline_members:
                        send_framed_msg(client_socket, f"STORE_OFFLINE:{recipient}", 'C')

                    # 2. Give the sender the IPs of the online members
                    if online_peers:
                        peers_str = "|".join([f"{ip},{port}" for ip, port in online_peers])
                        send_framed_msg(client_socket, f"GROUP_PEER_INFO:{recipient}:{peers_str}", 'C')
                    elif not offline_members:
                        send_framed_msg(client_socket, f"ERROR: No other members in group {recipient}.", 'C')
                else:
                    # Feature: Prevent sending files to non-existent users
                    if not user_exists(recipient):
                        send_framed_msg(client_socket, f"ERROR: User '{recipient}' does not exist.", 'C')
                    else:
                        print(f"[FILE TRANSFER] {username} -> {recipient} | '{filename}' | OFFLINE")
                        last_seen_time = ChatServer.get_last_seen(recipient)
                        send_framed_msg(client_socket, f"STORE_OFFLINE:{recipient}:{last_seen_time}", 'C')

        elif command in ("AUDIO_CALL", "VIDEO_CALL"):
            call_type = command
            peer = ChatServer.get_call_peer(recipient)

            if not peer:
                print(f"[{call_type} CALL] {username} -> {recipient} | OFFLINE")
                send_framed_msg(client_socket, f"CALLING: {recipient} is offline", 'C')
                continue

            #ip, udp_call_port = peer
            print(f"[{call_type} CALL] {username} is calling {recipient}")
            send_framed_msg(client_socket, f"RINGING: Waiting for {recipient} to accept...", 'C')

            # Also notify the recipient that an incoming call is coming,
            # so they can prompt the user to accept or reject
            with ChatServer.clients_lock:
                recipient_sock = ChatServer.clients.get(recipient)
            if recipient_sock:
                try:
                    send_framed_msg(recipient_sock[0], f"{call_type}:{username}", 'C')
                except Exception:
                    pass

        # CALL_ACCEPT: callee accepted - notify the original caller to start streaming.
        elif command == "CALL_ACCEPT":
            print(f"[CALL ACCEPTED] {username} accepted the call from {recipient}")

            # Get the IP/Ports for BOTH users
            caller_peer = ChatServer.get_call_peer(recipient)
            callee_peer = ChatServer.get_call_peer(username)

            if caller_peer and callee_peer:
                # Tell the Caller to start streaming to the Callee
                with ChatServer.clients_lock:
                    caller_sock = ChatServer.clients.get(recipient)

                    if caller_sock:
                        try:
                            send_framed_msg(caller_sock[0], f"CALL_PEER_INFO:{username}:{callee_peer[0]}:{callee_peer[1]}:audio", 'C')
                        except Exception:
                            pass
                
                # Tell the Callee to start streaming to the Caller
                send_framed_msg(client_socket, f"CALL_PEER_INFO:{recipient}:{caller_peer[0]}:{caller_peer[1]}:audio", 'C')
        
        # CALL_REJECT: callee rejected - notify the original caller.
        elif command == "CALL_REJECT":
            print(f"[CALL REJECTED] {username} rejected the call from {recipient}")
            with ChatServer.clients_lock:
                caller_sock = ChatServer.clients.get(recipient)

                if caller_sock:
                    try:
                        send_framed_msg(caller_sock[0], f"CALL_REJECTED:{username}", 'C')
                    except Exception:
                        pass
        
        # --------- FLUSH OFFLINE QUEUE --------------
        elif command == "FLUSH_OFFLINE":
            flush_redis_queue(client_socket, username)

        elif command == "EXIT":
            print(f"[EXIT] {username} disconnected gracefully.")
            break

        else:
            print(f"[UNKNOWN COMMAND] {command} from {username}")
            send_framed_msg(client_socket, "ERROR: UNKNOWN COMMAND.", 'C')


    
def start_server() -> None:
    """
    Initializes the TCP server and listens for incoming client connections.
    For each accepted connection, it spawns a new thread to handle the client.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    initialise_database()
    server.listen()
    print(f"Server listening on {HOST}:{PORT}...")

    
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    start_server()