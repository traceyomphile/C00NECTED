"""
Chat Server Implementation
This module implements the core server-side logic for managing client connections, group memberships, and message routing in a chat application. It maintains a global registry of connected clients and groups, allowing for direct messaging, group messaging, and peer information retrieval. The server ensures thread-safe access to shared data structures using locks.
Functions:
- register_client: Registers a new client in the global clients dictionary.
- remove_client: Removes a client from the global clients dictionary upon disconnection and records last seen time.
- get_peer_info: Retrieves the connection information for a given username.
- get_last_seen: Fetches the last seen timestamp of an offline user.
- is_group: Checks if a target string corresponds to a registered group.
- get_group_peers: Gets the IP and UDP port of all online members of a group for P2P transfers.
- send_dm: Sends a direct message from sender to target, or queues it if the target is offline.
- create_group: Creates a new group with the specified group_id and adds the creator as the first member.
- add_to_group: Allows an existing group member to add a new user to the group.
- leave_group: Removes a user from a group and cleans up empty groups.
- send_group_message: Sends a message to all group members, queuing it for those currently offline.
Date: 2024-06-01
"""

from threading import Lock
import socket
from datetime import datetime

clients = {}
last_seen = {} # Tracks when users disconnect: username -> timestamp
groups = {}    # group_id -> set of usernames
clients_lock: Lock = Lock()

def get_timestamp() -> str:
    """Generates a formatted timestamp for messages."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

def remove_client(username: str) -> None:
    """
    Removes a client from the global clients dictionary upon disconnection and records their exact disconnect time.
    Parameters:
        - username: The unique identifier for the client to be removed.
    Returns:
        - None
    """
    with clients_lock:
        if username in clients:
            # Record last seen time before deleting
            last_seen[username] = get_timestamp()
            del clients[username]

def get_last_seen(username: str) -> str:
    """
    Fetches the last seen timestamp of a user.
    Parameters:
        - username: The unique identifier for the client.
    Returns:
        - A string containing the timestamp or a status message.
    """
    with clients_lock:
        return last_seen.get(username, "Never logged in or currently online")

def get_peer_info(username: str) -> tuple:
    """
    Retrieves the connection information for a given username.
    Parameters:
        - username: The unique identifier for the client whose information is being requested.
    Returns:    
        - A tuple containing (socket, ip, udp_port) if the user is online, or None if offline.
    """
    with clients_lock:
        return clients.get(username)

def is_group(group_id: str) -> bool:
    """
    Checks if a string is a registered group.
    Parameters:
        - group_id: The string to check.
    Returns:
        - True if the group exists, False otherwise.
    """
    with clients_lock:
        return group_id in groups

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
    with clients_lock:
        if group_id in groups:
            for member in groups[group_id]:
                if member != exclude_user and member in clients:
                    sock, ip, port = clients[member]
                    peer_list.append((ip, port))
    return peer_list

def send_dm(sender: str, target: str, content: str, send_func: callable, queue_func: callable) -> bool:
    """
    Sends a direct message from sender to target, or queues it if the target is offline.
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
            sock = clients[target][0]
            send_func(sock, timestamped_msg, 'D')
        else:
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
    with clients_lock:
        if group_id in groups:
            return False
        groups[group_id] = {creator}
        return True
    
def add_to_group(group_id: str, adder_username: str, target_username: str) -> str:
    """
    Allows an existing group member to add a new user to the group.
    Parameters:
        - group_id: The unique identifier for the group.
        - adder_username: The username of the client attempting to add someone.
        - target_username: The username of the user being added.
    Returns:
        - A string status code indicating the result ("GROUP_NOT_FOUND", "NOT_MEMBER", "ALREADY_MEMBER", or "SUCCESS").
    """
    with clients_lock:
        if group_id not in groups:
            return "GROUP_NOT_FOUND"
        if adder_username not in groups[group_id]:
            return "NOT_MEMBER"
        if target_username in groups[group_id]:
            return "ALREADY_MEMBER"
        
        groups[group_id].add(target_username)
        return "SUCCESS"
    
def leave_group(group_id: str, username: str) -> bool:
    """
    Removes a user from a group and cleans up the group if it becomes empty.
    Parameters:
        - group_id: The unique identifier for the group to leave.
        - username: The username of the client attempting to leave the group.
    Returns:
        - True if the user was removed from the group successfully, False if the user is not in the group.
    """
    with clients_lock:
        if group_id in groups and username in groups[group_id]:
            groups[group_id].remove(username)
            # Cleanup if the group is now empty
            if not groups[group_id]:
                del groups[group_id]
            return True
        return False
    
def send_group_message(sender: str, group_id: str, message: str, send_func: callable, queue_func: callable) -> str:
    """
    Sends a message to all members of a group except the sender, queuing it for offline members.
    Parameters:
        - sender: The username of the message sender.
        - group_id: The unique identifier for the group.
        - message: The message content to be sent.
        - send_func: A function to send framed messages to a socket.
        - queue_func: A function to queue the message for offline members.
    Returns:
        - A string status indicating success ("SUCCESS") or failure ("GROUP_NOT_FOUND", "NOT_MEMBER").
    """
    with clients_lock:
        if group_id not in groups:
            return "GROUP_NOT_FOUND"
        
        if sender not in groups[group_id]:
            return "NOT_MEMBER"
            
        timestamped_msg = f"[{get_timestamp()}] [{group_id}] {sender}: {message}"
        
        for member in groups[group_id]:
            if member != sender:
                if member in clients:
                    sock = clients[member][0]
                    send_func(sock, timestamped_msg, 'D')
                else:
                    queue_func(member, timestamped_msg)
                    
        return "SUCCESS"