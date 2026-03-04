"""
Chat Server Implementation
This module implements the core server-side logic for managing client connections, group memberships, and message routing in a chat application. It maintains a global registry of connected clients and groups, allowing for direct messaging, group messaging, and peer information retrieval. The server ensures thread-safe access to shared data structures using locks.
Functions:
- register_client: Registers a new client in the global clients dictionary.
- remove_client: Removes a client from the global clients dictionary upon disconnection.
- get_peer_info: Retrieves the connection information for a given username.
- send_dm: Sends a direct message from sender to target if the target is online.
- create_group: Creates a new group with the specified group_id and adds the creator as the first member.
- join_group: Adds a user to an existing group.
- leave_group: Removes a user from a group.
- send_group_message: Sends a message to all members of a group except the sender.
Date: 2024-06-01
"""

from threading import Lock
import socket

clients = {}
groups = {} # group_id -> set of usernames
clients_lock: Lock = Lock()

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
    Removes a client from the global clients dictionary upon disconnection.
    Parameters:
        - username: The unique identifier for the client to be removed.
    Returns:
        - None
    """
    with clients_lock:
        if username in clients:
            del clients[username]

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

def send_dm(sender: str, target: str, content: str, send_func: callable) -> bool:
    """
    Sends a direct message from sender to target if the target is online.
    Parameters:
        - sender: The username of the message sender.
        - target: The username of the message recipient.
        - content: The message content to be sent.
        - send_func: A function to send framed messages to a socket.
    Returns:
        - True if the message was sent successfully, False if the target is offline.
    """
    with clients_lock:
        if target in clients:
            sock = clients[target][0]
            send_func(sock, f"[{sender}]: {content}", 'D')
            return True
    return False

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
    
def join_group(group_id: str, username: str) -> bool:
    """
    Adds a user to an existing group.
    Parameters:
        - group_id: The unique identifier for the group to join.
        - username: The username of the client attempting to join the group.
    Returns:
        - True if the user was added to the group successfully, False if the group does not exist.
    """
    with clients_lock:
        if group_id not in groups:
            return False
        groups[group_id].add(username)
        return True
    
def leave_group(group_id: str, username: str) -> bool:
    """
    Removes a user from a group.
    Parameters:
        - group_id: The unique identifier for the group to leave.
        - username: The username of the client attempting to leave the group.
    Returns:
        - True if the user was removed from the group successfully, False if the user is not in the group.
    """
    with clients_lock:
        if group_id in groups and username in groups[group_id]:
            groups[group_id].remove(username)
            return True
        return False
    
def send_group_message(sender: str, group_id: str, message: str, send_func: callable) -> bool:
    """
    Sends a message to all members of a group except the sender.
    Parameters:
        - sender: The username of the message sender.
        - group_id: The unique identifier for the group to which the message should be sent.
        - message: The message content to be sent.
        - send_func: A function to send framed messages to a socket.
    Returns:
        - True if the message was sent to at least one group member, False if the group does not exist.
    """
    with clients_lock:
        if group_id not in groups:
            return False
        
        delivered = False
        for member in groups[group_id]:
            if member != sender and member in clients:
                sock = clients[member][0]
                send_func(sock, f"[{group_id}] {sender}: {message}", 'D')
                delivered = True
                
        return delivered