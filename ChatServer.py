"""
Chat Server Implementation
This module implements the core server-side logic for managing client connections, group memberships, and message routing in a chat application. It maintains a global registry of connected clients and groups, allowing for direct messaging, group messaging, and peer information retrieval. The server ensures thread-safe access to shared data structures using locks.

Each connected client is tracked with two ports:
- tcp_media_port : The TCP port the clinet listens on for incoming P2P file transfers (images, PDFs, audio, video).
- udp_call_port  : The UDP port the client listens on for real-time audio/video call streaming.

Functions:
- register_client: Registers a new client with both tcp_media_port and udp_call_port.
- remove_client: Removes a client from the global clients dictionary upon disconnection.
- get_peer_info: Retrieves the connection information for a given username.
- get_last_seen: Fetches the exact timestamp a user disconnected.
- is_group: Checks if a target ID is a registered group.
- get_group_peers: Retrieves the IPs and ports of all online group members for P2P multicasting.
- get_group_presence: Finds both online and offline members of a group for P2P file transfers and system notifications.
- send_dm: Sends a direct message from sender to target if the target is online, or queues it if offline.
- create_group: Creates a new group with the specified group_id and adds the creator as the first member.
- add_to_group: Adds a user to an existing group, ensuring the adder is already a member.
- leave_group: Removes a user from a group and cleans up empty groups.
- send_group_message: Sends a message to all members of a group except the sender, queuing for offline members.
- get_call_peer: Returns the IP and UDP call port of a user for real-time call routing.
Date: 2026-03-11
"""

from threading import Lock
import socket
from datetime import datetime
from infrastructure import redis_client, db_lock, get_db

PRESENCE_TTL = 120

# Thread-safe registry for ACTIVE socket connections only
clients = {}
clients_lock: Lock = Lock()

def get_timestamp() -> str:
    """
    Generates a formatted timestamp for messages.
    Returns:
        - str: representing timestamp
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ----------------------------
# PRESENCE MANAGEMENT (REDIS)
# ----------------------------

def set_user_online(username: str, ip: str, tcp_media_port: int, udp_call_port: int) -> None:
    """
    Stores the user's IP, TCP media port, and UDP call port in Redis.
    Format: "ip:tcp_media_port:udp_call_port"

    Parameters:
        - username: The unique identifier for the user.
        - ip: The IP address of the user.
        - tcp_media_port: The TCP port for media transfers.
        - udp_call_port: The UDP port for call streaming.
    Returns:
        - None
    """
    key = f"presence:{username}"
    value = f"{ip}:{tcp_media_port}:{udp_call_port}"

    redis_client.set(key, value, ex=PRESENCE_TTL)

def refresh_presence(username: str):
    key = f"presence:{username}"

    if redis_client.exists(key):
        redis_client.expire(key, PRESENCE_TTL)

def set_user_offline(username: str):
    redis_client.delete(f"presence:{username}")

def get_user_presence(username: str) -> tuple[str, int, int] | None:
    """
    Retrieves the user's IP, TCP media port, and UDP call port from Redis if they are online.
    Parameters:
        - username: The unique identifier for the user.
    Returns:
        - A tuple (ip, tcp_media_port, udp_call_port) if the user is online, or None if offline.
    """
    value = redis_client.get(f"presence:{username}")

    if not value:
        return None
    
    if isinstance(value, bytes):
        value = value.decode()
    
    parts = value.split(":")
    if len(parts) != 3:
        return None
    
    return parts[0], int(parts[1]), int(parts[2])

def get_group_presence(group_id: str, exclude_user: str) -> tuple[list, list]:
    """
    Finds both online and offline members of a group for P2P transfers and system notifications.
    Parameters:
        - group_id: The unique identifier for the group.
        - exclude_user: The username of the sender to exclude from the results.
    Returns:
        - A tuple containing a list of online peers [(ip, port), ...] and a list of offline usernames [username, ...].
    """
    online_peers = []
    offline_members = []

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT username FROM group_members WHERE group_id=?",
            (group_id,)
        )
        members = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    for member in members:
        if member == exclude_user:
            continue

        presence = get_user_presence(member)

        if presence:
            ip, tcp_media_port, _ = presence
            online_peers.append((ip, tcp_media_port))
        else:
            offline_members.append(member)

    return online_peers, offline_members

def update_last_seen(username: str):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute(
                "UPDATE users SET last_seen=? WHERE username=?",
                (datetime.now(), username)
            )
            conn.commit()
        finally:
            conn.close()

def get_last_seen(username: str):
    # Check Redis Presence
    if redis_client.get(f"presence:{username}"):
        return "online"
    
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT last_seen FROM users WHERE username=?",
            (username,) 
        )

        row = cur.fetchone()

        if not row or not row[0]:
            return "Never"
        
        raw = row[0]
        if isinstance(raw, str):
            last_seen = datetime.fromisoformat(raw) # SQLite returns datetimes as strings
        else:
            last_seen = raw 

        delta = datetime.now() - last_seen

        return format_last_seen(delta)
    
    finally:
        conn.close()

def format_last_seen(delta: datetime):
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "last seen just now"
    
    if seconds < 3600:
        return f"last seen {seconds // 60} minutes ago"
    
    if seconds < 86400:
        return f"last seen {seconds // 3600} hours ago"
    
    return f"last seen {seconds // 86400} days ago"

# --------------------------------------
# CLIENT REGISTRATION (MEMORY)
# --------------------------------------

def register_client(username: str, client_socket: socket.socket, ip: str, tcp_media_port: int, udp_call_port: int) -> None:
    """
    Registers a new client in the global clients dictionary.
    Parameters:
        - username: The unique identifier for the client.
        - client_socket: The socket object representing the client's connection.
        - ip: The IP address of the client.
        - tcp_media_port: The TCP port number for P2P media exchange.
        - udp_call_port: The UDP port number for P2P voice/video calls.
    Returns:
        - None
    """
    with clients_lock:
        clients[username] = (client_socket, ip, tcp_media_port, udp_call_port)

def remove_client(username: str):
    with clients_lock:
        if username in clients:
            del clients[username]
        update_last_seen(username)
        set_user_offline(username)

# ---------------
# GETTING PEERS
# ---------------

def get_group_peers(group_id: str, exclude_user: str) -> list:
    """
    Gets the IP and UDP port of all ONLINE members of a group for P2P multicast file transfers.
    Parameters:
        - group_id: The group to fetch peers for.
        - exclude_user: The username of the sender to exclude from the return list.
    Returns:
        - A list of tuples containing (ip, port) for each online member.
    """
    peer_list = []
    
    conn = get_db()
    cur = conn.cursor()
    members = None  

    try:
        cur.execute(
            "SELECT username FROM group_members WHERE group_id=?",
            (group_id,)
        )
        members = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    with clients_lock:
        for member in members:
            if member != exclude_user and member in clients:
                _, ip, tcp_media_port, _= clients[member]
                peer_list.append((ip, tcp_media_port))
    
    return peer_list

# ------------------------
# MANAGING GROUPS
# ------------------------

def is_group(group_id: str) -> bool:
    """
    Checks if a string is a registered group.
    Parameters:
        - group_id: The string to check.
    Returns:
        - True if the group exists, False otherwise.
    """
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT 1 FROM groups WHERE group_id=?",
            (group_id,)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()

def create_group(group_id: str, creator: str) -> bool:
    """
    Creates a new group with the specified group_id and adds the creator as the first member.
    Parameters:
        - group_id: The unique identifier for the group to be created.
        - creator: The username of the client creating the group.
    Returns:
        - True if the group was created successfully, False if a group with the same ID already exists.
    """
    with db_lock:

        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute(
                "SELECT 1 FROM groups WHERE group_id=?",
                (group_id,)
            )

            if cur.fetchone():
                return False
            
            with db_lock:
                cur.execute(
                    "INSERT INTO groups(group_id) VALUES(?)",
                    (group_id,)
                )

                cur.execute(
                    "INSERT INTO group_members(group_id, username) VALUES(?,?)",
                    (group_id, creator)
                )

                conn.commit()
                return True
        finally:
            conn.close()
    
def add_to_group(group_id: str, adder_username: str, target_username: str) -> str:
    """
    Adds a user to an existing group.
    Parameters:
        - group_id: The unique identifier for the group.
        - adder_username: The username of the client attempting to add someone.
        - target_username: The username of the user being added.
    Returns:
        - A string status code indicating the result ("GROUP_NOT_FOUND", "NOT_MEMBER", "ALREADY_MEMBER", or "SUCCESS").
    """
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT 1 FROM groups WHERE group_id=?",
            (group_id,)
        )

        if not cur.fetchone():
            return "GROUP_NOT_FOUND"
        
        cur.execute(
            "SELECT 1 FROM group_members WHERE group_id=? AND username=?",
            (group_id, adder_username)
        )

        if not cur.fetchone():
            return "NOT_MEMBER"
        
        cur.execute(
            "SELECT 1 FROM group_members WHERE group_id=? AND username=?",
            (group_id, target_username)
        )

        if cur.fetchone():
            return "ALREADY_MEMBER"

        with db_lock:    
            cur.execute(
                "INSERT INTO group_members(group_id, username) VALUES(?,?)",
                (group_id, target_username)
            )

            conn.commit()

            return "SUCCESS"
    finally:
        conn.close()
    
def leave_group(group_id: str, username: str) -> bool:
    """
    Removes a user from a group.
    Parameters:
        - group_id: The unique identifier for the group to leave.
        - username: The username of the client attempting to leave the group.
    Returns:
        - True if the user was removed from the group successfully, False if the user is not in the group.
    """
    conn = get_db()
    cur = conn.cursor()

    try:
        with db_lock:
            cur.execute(
                "DELETE FROM group_members WHERE group_id=? AND username=?",
                (group_id, username)
            )

            cur.execute(
                "SELECT COUNT(*) FROM group_members WHERE group_id=?",
                (group_id,)
            )

            count = cur.fetchone()[0]

            if count == 0:
                cur.execute(
                    "DELETE FROM groups WHERE group_id=?",
                    (group_id,)
                )

            conn.commit()

        return True
    finally:
        conn.close()

# ----------------------
# MESSAGE MANAGEMENT
# ----------------------

def send_dm(sender: str, target: str, content: str, send_func: callable, queue_func: callable) -> bool:
    """
    Sends a direct message from sender to target if the target is online.
    Parameters:
        - sender: The username of the message sender.
        - target: The username of the message recipient.
        - content: The message content to be sent.
        - send_func: A function to send framed messages to a socket.
        - queue_func: A function to queue the message if the target is offline.
    Returns:
        - True if the message was processed successfully (sent or queued).
    """
    timestamped_msg = f"[{get_timestamp()}] [{sender} (DM)]: {content}"
    with clients_lock:
        if target in clients:
            try:
                sock = clients[target][0]
                send_func(sock, timestamped_msg, 'D')
                return True
            except Exception:
                # Socket failed, fall through to queuing
                pass
    
    queue_func(target, timestamped_msg)
    return True
    
def send_group_message(sender: str, group_id: str, message: str, send_func: callable, queue_func: callable) -> str:
    """
    Sends a message to all members of a group except the sender.
    Parameters:
        - sender: The username of the message sender.
        - group_id: The unique identifier for the group to which the message should be sent.
        - message: The message content to be sent.
        - send_func: A function to send framed messages to a socket.
        - queue_func: A function to queue the message for offline members.
    Returns:
        - A string status indicating success ("SUCCESS") or failure ("GROUP_NOT_FOUND", "NOT_MEMBER").
    """
    conn = get_db()
    cur = conn.cursor()
    members = None

    try:
        cur.execute(
            "SELECT username FROM group_members WHERE group_id=?",
            (group_id,)
        )

        members = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    timestamped = f"[{get_timestamp()}] [{group_id}] {sender}: {message}"

    for member in members:
        if member == sender:
            continue

        with clients_lock:
            sock = clients.get(member)

        if sock:
            try:
                send_func(sock[0], timestamped, 'D')
            except:
                queue_func(member, timestamped)
        else:
            queue_func(member, timestamped)

    return "SUCCESS"

# -----------------
# CALL HANDLING
# -----------------

def get_call_peer(username: str):
    """
    Retrieves the IP and UDP call port of a user for real-time call routing.
    Parameters:
        - username: The unique identifier for the user.
    Returns:
        - A tuple containing the IP address and UDP call port, or None if the user is not found.
    """
    presence = get_user_presence(username)

    if not presence:
        return None
    
    ip, _, _, udp_call_port = presence

    return ip, udp_call_port