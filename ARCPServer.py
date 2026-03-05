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
Date: 2026-03-05
"""

import socket
import threading
import ChatServer
import base64

# Server Configuration
HOST = socket.gethostbyname(socket.gethostname())
PORT = 50000

# Global Data Structures
db_lock: threading.Lock = threading.Lock()

postgresql_users: dict[str, str] = {
    "kb": "password123",
    "amahle": "securepass",
    "jacques": "cricketGOAT",
    "tracy": "testpass"
}

# Dictionary for Offline Queue: recipient -> [formatted_message_string, ...]
redis_message_queue: dict[str, list[str]] = {} 

def send_framed_msg(sock: socket.socket, message: str, msg_type: str = 'D') -> None:
    """
    Frames a message with a header and sends it over the socket.
    Header Format: [Type (1 char)][Length (4 chars)]
    Parameters:
        - sock: The socket to send the message through.
        - message: The content of the message to send.
        - msg_type: A single-character indicating the type of message (e.g., 'D', 'C', 'A').
    Returns:
        - None
    """
    data = message.encode('ascii')
    header = f"{msg_type}{len(data):04d}".encode('ascii')
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
    header = sock.recv(5)
    if not header: return None, None
    msg_type = header[0:1].decode('ascii')
    msg_len = int(header[1:5].decode('ascii'))
    
    data = b""
    while len(data) < msg_len:
        packet = sock.recv(msg_len - len(data))
        if not packet: break
        data += packet
    return msg_type, data.decode('ascii')

def queue_offline_message(recipient: str, formatted_msg: str) -> None:
    """
    Helper function to append a message to a user's offline queue.
    """
    with db_lock:
        if recipient not in redis_message_queue:
            redis_message_queue[recipient] = []
        redis_message_queue[recipient].append(formatted_msg)

def flush_redis_queue(client_socket: socket.socket, recipient: str) -> None:
    """
    Checks if there are any queued messages for the recipient in the Redis queue and sends them sequentially.
    Parameters:
        - client_socket: The socket to send the queued messages through.
        - recipient: The username of the recipient to check for queued messages.
    Returns:
        - None
    """
    with db_lock:
        if recipient not in redis_message_queue:
            return
        
        for msg in redis_message_queue[recipient]:
            send_framed_msg(client_socket, msg, 'D')
        del redis_message_queue[recipient]

def handle_client(client_socket: socket.socket, addr) -> None:
    """
    Handles the lifecycle of a client connection, including authentication and message processing.
    Parameters:
        - client_socket: The socket representing the client's connection.
        - addr: The address of the client.
    Returns:
        - None
    """
    username = None
    try:
        # Authenticate user and register in clients dict
        username = authenticate_client(client_socket, addr)

        if not username:
            return # Authentication failed or client disconnected
        
        # Implement chat logic
        main_chat_loop(client_socket, username)
                            
    except Exception as e:
        print(f"[ERROR] with {addr}: {e}")
    finally:
        client_socket.close()
        with db_lock:
            if username and ChatServer.get_peer_info(username): 
                ChatServer.remove_client(username)
                print(f"[DISCONNECTED] {username}")

def main_chat_loop(client_socket: socket.socket, username: str) -> None:
    """
    Main loop to process incoming messages from the client after authentication.
    Parameter:
        - client_socket: The socket representing the client's connection.
        - username: The authenticated username of the client.
    Returns:
        - None
    """
    while True:
        _, full_message = receive_framed_msg(client_socket)
        if not full_message: break

        parts = full_message.split(":", 2)

        if len(parts) < 2:
            send_framed_msg(client_socket, "ERROR: Invalid message format.", 'C')
            continue

        command = parts[0]
        recipient = parts[1]
        data = parts[2] if len(parts) > 2 else ""

        # FEATURE: Server logs routing info, but remains BLIND to the message payload data
        print(f"[ROUTING] {command} from '{username}' to '{recipient}'")

        if command == "SEND":
            # FEATURE: Prevent sending messages to non-existent users
            if recipient not in postgresql_users:
                send_framed_msg(client_socket, f"ERROR: User '{recipient}' does not exist in the system.", 'C')
                continue

            target_online = ChatServer.get_peer_info(recipient) is not None
            ChatServer.send_dm(username, recipient, data, send_framed_msg, queue_offline_message)
            
            # FEATURE: Delivery Notification OR Last Seen feedback
            if target_online:
                send_framed_msg(client_socket, f"[SYSTEM] Message delivered to '{recipient}'.", 'C')
            else:
                last_seen_time = ChatServer.get_last_seen(recipient)
                sys_msg = f"[SYSTEM] Message sent. User '{recipient}' is currently offline. Last seen at {last_seen_time}."
                send_framed_msg(client_socket, sys_msg, 'C')

        elif command == "SEND_GROUP":
            status = ChatServer.send_group_message(username, recipient, data, send_framed_msg, queue_offline_message)
            if status != "SUCCESS":
                send_framed_msg(client_socket, f"ERROR: {status}", 'C')

        elif command == "CREATE_GROUP":
            created = ChatServer.create_group(recipient, username)
            msg = "GROUP CREATED" if created else "GROUP EXISTS"
            send_framed_msg(client_socket, msg, 'C')

        elif command == "ADD_TO_GROUP":
            # 'data' holds the target username to add
            status = ChatServer.add_to_group(recipient, username, data)
            
            # Feature: System Notification for Added User
            if status == "SUCCESS":
                sys_msg = f"You were added to group '{recipient}' by {username}."
                ChatServer.send_dm("SYSTEM", data, sys_msg, send_framed_msg, queue_offline_message)
                
            send_framed_msg(client_socket, f"ADD_STATUS: {status}", 'C')

        elif command == "LEAVE_GROUP":
            left = ChatServer.leave_group(recipient, username)
            if left:
                # Feature: System Notification for Remaining Members
                sys_msg = f"{username} has left the group."
                ChatServer.send_group_message("SYSTEM", recipient, sys_msg, send_framed_msg, queue_offline_message)
                send_framed_msg(client_socket, "LEFT GROUP", 'C')
            else:
                send_framed_msg(client_socket, "GROUP NOT FOUND OR NOT MEMBER", 'C')

        elif command == "GET_PEER":
            peer = ChatServer.get_peer_info(recipient)
            filename = data if data else "a file" # Extract the filename from the new client format

            if peer:
                # Target is an individual online user
                _, ip, port = peer
                response = f"PEER_INFO:{recipient}:{ip}:{port}"
                send_framed_msg(client_socket, response, 'C')
            else:
                # Feature: Group File Sharing Support
                if ChatServer.is_group(recipient):
                    # THE FIX: Get both online peers and offline members
                    online_peers, offline_members = ChatServer.get_group_presence(recipient, exclude_user=username)
                    
                    # 1. Queue notifications for the offline members
                    for offline_user in offline_members:
                        sys_msg = f"[{ChatServer.get_timestamp()}] [SYSTEM] {username} sent '{filename}' to group '{recipient}' while you were offline."
                        queue_offline_message(offline_user, sys_msg)

                    # 2. Give the sender the IPs of the online members
                    if online_peers:
                        peers_str = "|".join([f"{ip},{port}" for ip, port in online_peers])
                        response = f"GROUP_PEER_INFO:{recipient}:{peers_str}"
                        send_framed_msg(client_socket, response, 'C')
                    else:
                        send_framed_msg(client_socket, f"ERROR: No other members online in group {recipient}. File not sent.", 'C')
                else:
                    # Feature: Prevent sending files to non-existent users
                    if recipient not in postgresql_users:
                        send_framed_msg(client_socket, f"ERROR: User '{recipient}' does not exist in the system.", 'C')
                    else:
                        last_seen_time = ChatServer.get_last_seen(recipient)
                        send_framed_msg(client_socket, f"ERROR: User '{recipient}' is OFFLINE. Last seen at {last_seen_time}", 'C')
        
        else:
            send_framed_msg(client_socket, "ERROR: UNKNOWN COMMAND.", 'C')

def authenticate_client(client_socket: socket.socket, addr) -> str | None:
    """
    Handles the authentication process for a new client connection.
    Parameters:
        - client_socket: The socket representing the client's connection.
        - addr: The address of the client.
    Returns:
        - The authenticated username, or None if authentication fails.
    """
    while True:
        _, msg = receive_framed_msg(client_socket)
        if not msg: return None# Disconnected during auth
        
        if msg.startswith("CHECK:"):
            check_user = msg.split(":")[1]
            if check_user in postgresql_users:
                send_framed_msg(client_socket, "EXISTS", 'A')
            else:
                send_framed_msg(client_socket, "NOT_FOUND", 'A')
                
        elif msg.startswith("LOGIN:"):
            _, login_user, login_pwd = msg.split(":", 2)
            if postgresql_users.get(login_user) != login_pwd:
                send_framed_msg(client_socket, "FAIL", 'A')
                continue

            with db_lock:
                if ChatServer.get_peer_info(login_user):
                    send_framed_msg(client_socket, "ALREADY ONLINE", 'A')
                    continue

            username = login_user
            send_framed_msg(client_socket, "SUCCESS", 'A')
            break # Exit auth loop!
                
                
        elif msg.startswith("REG:"):
            _, reg_user, reg_pwd = msg.split(":", 2)

            with db_lock:
                if reg_user in postgresql_users:
                    send_framed_msg(client_socket, "USER_EXISTS", 'A')
                    continue

                postgresql_users[reg_user] = reg_pwd

            username = reg_user
            send_framed_msg(client_socket, "SUCCESS", 'A')
            break # Exit auth loop!

    # Receive the dynamically assigned UDP port after login
    _, port_msg = receive_framed_msg(client_socket)
    udp_port = int(port_msg.split(":")[1])
    
    # Register with the Global ChatServer logic
    ChatServer.register_client(username, client_socket, addr[0], udp_port)
    print(f"[REGISTERED] {username} at {addr[0]}:{udp_port}")
    
    # Immediately flush any offline messages waiting for them
    flush_redis_queue(client_socket, username)

    return username
    
def start_server() -> None:
    """
    Initializes the TCP server and listens for incoming client connections.
    For each accepted connection, it spawns a new thread to handle the client.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print(f"Server listening on {HOST}:{PORT}...")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    start_server()
