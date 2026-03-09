"""
Chat Server Implementation
This module implements the core server-side logic for managing client connections, group memberships, and message routing in a chat application. It maintains a global registry of connected clients and groups, allowing for direct messaging, group messaging, and peer information retrieval. The server ensures thread-safe access to shared data structures using locks.
Functions:
- register_client: Registers a new client in the global clients dictionary.
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
Date: 2024-06-01
"""

from threading import Lock
import socket
from datetime import datetime
from infrastructure import pg_pool, redis_client

PRESENCE_TTL = 120

# Thread-safe registry for ACTIVE socket connections only
clients = {}
clients_lock: Lock = Lock()

def get_timestamp() -> str:
    """Generates a formatted timestamp for messages."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ----------------------------
# PRESENCE MANAGEMENT (REDIS)
# ----------------------------

def set_user_online(username: str, ip: str, port: int):
    key = f"presence:{username}"
    value = f"{ip}:{port}"

    redis_client.set(key, value, ex=PRESENCE_TTL)

def refresh_presence(username: str):
    key = f"presence:{username}"

    if redis_client.exists(key):
        redis_client.expire(key, PRESENCE_TTL)

def set_user_offline(username: str):
    redis_client.delete(f"presence:{username}")

def get_user_presence(username: str):
    value = redis_client.get(f"presence:{username}")

    if not value:
        return None
    
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    
    ip, port = value.split(":")
    return ip, int(port)

# --------------------------------------
# CLIENT REGISTRATION (MEMORY)
# --------------------------------------

def register_client(username: str, client_socket: socket.socket, ip: str, udp_port: int) -> None:
    """
    Registers a new client in the global clients dictionary.
    Parameters:
        - username: The unique identifier for the client.
        - client_socket: The socket object representing the client's connection.
        - ip: The IP address of the client.
        - udp_port: The UDP port number for P2P media exchange.
    Returns:
        - None
    """
    with clients_lock:
        clients[username] = (client_socket, ip, udp_port)

def remove_client(username: str):
    with clients_lock:
        if username in clients:
            del clients[username]
        set_user_offline(username)

# ----------------------------------------
# GROUP & MESSAGING LOGIC (POSTGRES)
# ----------------------------------------

def is_group(group_id: str) -> bool:
    """
    Checks if a string is a registered group.
    Parameters:
        - group_id: The string to check.
    Returns:
        - True if the group exists, False otherwise.
    """
    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM groups WHERE group_id=%s",
                (group_id,)
            )
            return cur.fetchone() is not None

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

    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username FROM group_members WHERE group_id=%s",
                (group_id,)
            )
            members = [row[0] for row in cur.fetchall()]

    for member in members:
        if member == exclude_user:
            continue

        with clients_lock:
            if member in clients:
                _, ip, port = clients[member]
                online_peers.append((ip, port))
            else:
                offline_members.append(member)

    return online_peers, offline_members

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

def create_group(group_id: str, creator: str) -> bool:
    """
    Creates a new group with the specified group_id and adds the creator as the first member.
    Parameters:
        - group_id: The unique identifier for the group to be created.
        - creator: The username of the client creating the group.
    Returns:
        - True if the group was created successfully, False if a group with the same ID already exists.
    """
    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM groups WHERE group_id=%s",
                (group_id,)
            )

            if cur.fetchone():
                return False
            
            cur.execute(
                "INSERT INTO groups(group_id) VALUES(%s)",
                (group_id)
            )

            cur.execute(
                "INSERT INTO group_members(group_id, username) VALUES(%s,%s)",
                (group_id, creator)
            )

            conn.commit()
    return True
    
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
    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM groups WHERE group_id=%s",
                (group_id)
            )

            if not cur.fetchone():
                return "GROUP_NOT_FOUND"
            
            cur.execute(
                "SELECT 1 FROM group_members WHERE group_id=%s AND username=%s",
                (group_id, adder_username)
            )

            if not cur.fetchone():
                return "NOT MEMBER"
            
            cur.execute(
                "SELECT 1 FROM group_members WHERE group_id=%s AND username=%s",
                (group_id, target_username)
            )

            if cur.fetchone():
                return "ALREADY_MEMBER"
            
            cur.execute(
                "INSERT INTO group_members(group_id, username) VALUES(%s,%s)",
                (group_id, target_username)
            )

            conn.commit()

    return "SUCCESS"
    
def leave_group(group_id: str, username: str) -> bool:
    """
    Removes a user from a group.
    Parameters:
        - group_id: The unique identifier for the group to leave.
        - username: The username of the client attempting to leave the group.
    Returns:
        - True if the user was removed from the group successfully, False if the user is not in the group.
    """
    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM group_members WHERE group_id=%s AND username=%s",
                (group_id, username)
            )

            cur.execute(
                "SELECT COUNT(*) FROM group_members WHERE group_id=%s",
                (group_id,)
            )

            count = cur.fetchone()[0]

            if count == 0:
                cur.execute(
                    "DELETE FROM groups WHERE group_id=%s",
                    (group_id,)
                )

            conn.commit()

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
    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username FROM group_members WHERE group_id=%s",
                (group_id,)
            )

            members = [row[0] for row in cur.fetchall()]

        timestamped = f"[{get_timestamp()}] [{group_id}] {sender}: {message}"

        for member in members:
            if member == sender:
                continue

            if member in clients:
                try:
                    sock = clients[member][0]
                    send_func(sock, timestamped, 'D')
                except:
                    queue_func(member, timestamped)
            else:
                queue_func(member, timestamped)

    return "SUCCESS"

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
    
    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username FROM group_members WHERE group_id=%s",
                (group_id,)
            )
            members = [row[0] for row in cur.fetchall]

    with clients_lock:
        for member in members:
            if member != exclude_user and member in clients:
                _, ip, port, = clients[member]
                peer_list.append((ip, port))
    
    return peer_list





