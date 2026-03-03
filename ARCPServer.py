"""
ARCP Server Implementation
This module implements the ARCP server that handles client authentication, peer discovery, direct messaging, group chat management, and offline message queuing.
The server listens for incoming TCP connections from clients, processes framed messages according to the defined protocol, and maintains global data structures for user management and message routing. It also ensures thread-safe access to shared resources using locks.
Functions:
- send_framed_msg: Frames a message with a header and sends it over a socket.
- receive_framed_msg: Receives a framed message and returns the type and content.
- flush_redis_queue: Checks for queued messages for a recipient and sends them upon login.
- handle_client: Manages the lifecycle of a client connection, including authentication and message processing.
- main_chat_loop: Processes incoming messages from the client after authentication.
- authenticate_client: Handles the authentication process for a new client connection.
- start_server: Initializes the TCP server and listens for incoming client connections.
Date: 2024-06-01
"""

import socket
import threading
import ChatServer

# Server Configuration
HOST = socket.gethostbyname(socket.gethostname())
PORT = 50000

# Global Data Structures
db_lock: threading.Lock = threading.Lock()

postgresql_users: dict[str, str] = {
    "kb": "password123",
    "amahle": "securepass",
    "jacques": "cricketGOAT"
}

# Tuple-keyed Dictionary for Offline Queue: recipient -> [(sender, message), ...]
redis_message_queue: dict[str, list[tuple[str, str]]] = {} 

# Online Tracker: username -> (tcp_socket, ip, udp_port)
clients: dict[str, tuple[socket.socket, str, int]] = {} 

def send_framed_msg(sock: socket.socket, message: str, msg_type: str = 'DATA') -> None:
    """
    Frames a message with a header and sends it over the socket.
    Header Format: [Type (1 char)][Length (4 chars)]
    Parameters:
        - sock: The socket to send the message through.
        - message: The content of the message to send.
        - msg_type: A string indicating the type of message (e.g., 'DATA', 'COMMAND', 'ACTION').
    Returns:
        - None
    """
    data = message.encode('ascii')
    header = f"{msg_type}{len(data):04d}".encode('ascii')
    sock.sendall(header + data)

def receive_framed_msg(sock: socket.socket) -> tuple[str, str]:
    """
    Receives a framed message and returns the type and content.
    Expects the same header format as send_framed_msg.
    Parameters:
        - sock: The socket to read from.
    Returns:
        - msg_type: The type of the message (e.g., 'DATA', 'COMMAND', 'ACTION').
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

def flush_redis_queue(client_socket: socket.socket, recipient: str) -> None:
    """
    Checks if there are any queued messages for the recipient in the Redis queue and sends them.
    Parameters:
        - client_socket: The socket to send the queued messages through.
        - recipient: The username of the recipient to check for queued messages.
    Returns:
        - None
    """
    with db_lock:
        if recipient in redis_message_queue:
            for sender, msg in redis_message_queue[recipient]:
                out_msg = f"[OFFLINE QUEUE] [{sender}]: {msg}"
                send_framed_msg(client_socket, out_msg, 'DATA')
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
        authenticate_client(client_socket, addr)

        # Implement chat logic
        main_chat_loop(client_socket, username)
                            
    except Exception as e:
        print(f"[ERROR] with {addr}: {e}")
    finally:
        client_socket.close()
        with db_lock:
            if username in clients:
                del clients[username]
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
        msg_type, full_message = receive_framed_msg(client_socket)
        if not full_message: break

        # PEER DISCOVERY LOGIC
        if msg_type == 'COMMAND' and full_message.startswith("GET_PEER:"):
            target_user = full_message.split(":")[1]
            if target_user in clients:
                _, t_ip, t_port = clients[target_user]
                response = f"PEER_INFO:{target_user}:{t_ip}:{t_port}"
                send_framed_msg(client_socket, response, 'COMMAND')
            else:
                send_framed_msg(client_socket, f"ERROR: User {target_user} not online.", 'C')
            continue

        # DIRECT MESSAGING LOGIC (/sendmsg)
        parts = full_message.split(" ", 2)

        if len(parts) < 2:
            send_framed_msg(client_socket, "ERROR: Invalid message format.", 'COMMAND')
            continue

        command = parts[0]
        recipient = parts[1]
        data = parts[2] if len(parts) > 2 else ""

        if command == "SEND":
            # Case 1: Recipient is an online user
            peer = ChatServer.get_peer_info(recipient)

            if peer:
                sock = peer[0]
                send_framed_msg(sock, f"{username}: {data}", 'DATA')
                continue

            # Case 2: Recipient is a group
            sent_to_group = ChatServer.send_group_message(username, recipient, data, send_framed_msg)
            if sent_to_group:
                continue

            # Case 3: Recipient is offline user -> Queue in Redis
            with db_lock:
                if recipient not in redis_message_queue:
                    redis_message_queue[recipient] = []
                redis_message_queue[recipient].append((username, data))

            send_framed_msg(client_socket, "QUEUED_OFFLINE", 'COMMAND')

        elif command == "CREATE_GROUP":
            created = ChatServer.create_group(recipient, username)
            msg = "GROUP CREATED" if created else "GROUP EXISTS"
            send_framed_msg(client_socket, msg, 'COMMAND')
            continue

        elif command == "JOIN_GROUP":
            joined = ChatServer.join_group(recipient, username)
            msg = "JOINED GROUP" if joined else "GROUP NOT FOUND"
            send_framed_msg(client_socket, msg, 'COMMAND')
            continue

        elif command == "LEAVE_GROUP":
            left = ChatServer.leave_group(recipient, username)
            msg = "LEFT GROUP" if left else "GROUP NOT FOUND OR NOT MEMBER"
            send_framed_msg(client_socket, msg, 'COMMAND')
            continue

        elif command == "GET_PEER":
            peer = ChatServer.get_peer_info(recipient)
            if peer:
                _, ip, port = peer
                response = f"PEER_INFO:{recipient}:{ip}:{port}"
            else:
                response = "User OFFLINE"
            send_framed_msg(client_socket, response, 'COMMAND')
            continue
        
        else:
            send_framed_msg(client_socket, "ERROR: UNKNOWN COMMAND.", 'COMMAND')

def authenticate_client(client_socket: socket.socket, addr) -> None:
    """
    Handles the authentication process for a new client connection.
    Parameters:
        - client_socket: The socket representing the client's connection.
        - addr: The address of the client.
    Returns:
        - None
    """
    while True:
        msg_type, msg = receive_framed_msg(client_socket)
        if not msg: return # Disconnected during auth
        
        if msg.startswith("CHECK:"):
            check_user = msg.split(":")[1]
            if check_user in postgresql_users:
                send_framed_msg(client_socket, "EXISTS", 'ACTION')
            else:
                send_framed_msg(client_socket, "NOT_FOUND", 'ACTION')
                
        elif msg.startswith("LOGIN:"):
            _, login_user, login_pwd = msg.split(":", 2)
            if postgresql_users.get(login_user) == login_pwd:
                username = login_user
                send_framed_msg(client_socket, "SUCCESS", 'ACTION')
                break # Exit auth loop!
            else:
                send_framed_msg(client_socket, "FAIL", 'ACTION')
                
        elif msg.startswith("REG:"):
            _, reg_user, reg_pwd = msg.split(":", 2)
            with db_lock:
                postgresql_users[reg_user] = reg_pwd
            username = reg_user
            send_framed_msg(client_socket, "SUCCESS", 'ACTION')
            break # Exit auth loop!

    # Receive the dynamically assigned UDP port after login
    _, port_msg = receive_framed_msg(client_socket)
    udp_port = int(port_msg.split(":")[1])
    
    with db_lock:
        ChatServer.register_client(username, client_socket, addr[0], udp_port)
    print(f"[REGISTERED] {username} at {addr[0]}:{udp_port}")
    
    # Immediately flush any offline messages waiting for them
    flush_redis_queue(client_socket, username)
    
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