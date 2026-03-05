"""
ARCP Server Implementation
This module implements the ARCP server that handles client authentication, peer discovery, direct messaging, group chat management, and offline message queuing.
The server listens for incoming TCP connections from clients, processes framed messages according to the defined protocol, and maintains global data structures for user management and message routing. It also ensures thread-safe access to shared resources using locks.
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
    UPGRADED to 8-digit length header to support large file buffering.
    Header Format: [Type (1 char)][Length (8 chars)]
    """
    data = message.encode('ascii')
    header = f"{msg_type}{len(data):08d}".encode('ascii')
    sock.sendall(header + data)

def receive_framed_msg(sock: socket.socket) -> tuple[str, str] | tuple[None, None]:
    """
    Receives a framed message and returns the type and content.
    UPGRADED to read 9 byte headers (1 char type + 8 chars length).
    """
    header = sock.recv(9)
    if not header: return None, None
    msg_type = header[0:1].decode('ascii')
    msg_len = int(header[1:9].decode('ascii'))
    
    data = b""
    while len(data) < msg_len:
        packet = sock.recv(msg_len - len(data))
        if not packet: break
        data += packet
    return msg_type, data.decode('ascii')

def queue_offline_message(recipient: str, formatted_msg: str) -> None:
    with db_lock:
        if recipient not in redis_message_queue:
            redis_message_queue[recipient] = []
        redis_message_queue[recipient].append(formatted_msg)

def flush_redis_queue(client_socket: socket.socket, recipient: str) -> None:
    with db_lock:
        if recipient not in redis_message_queue:
            return
        
        for msg in redis_message_queue[recipient]:
            send_framed_msg(client_socket, msg, 'D')
        del redis_message_queue[recipient]

def flush_offline_files(client_socket: socket.socket, recipient: str) -> None:
    """Retrieves buffered TCP files and sends them to the newly logged-in user."""
    files = ChatServer.get_and_clear_offline_files(recipient)
    for sender, filename, b64_data in files:
        # Deliver via TCP using the new 'F' (File) message type
        send_framed_msg(client_socket, f"DELIVER_FILE:{sender}:{filename}:{b64_data}", 'F')

def handle_client(client_socket: socket.socket, addr) -> None:
    username = None
    try:
        username = authenticate_client(client_socket, addr)
        if not username:
            return 
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

        print(f"[ROUTING] {command} from '{username}' to '{recipient}'")

        if command == "SEND":
            if recipient not in postgresql_users:
                send_framed_msg(client_socket, f"ERROR: User '{recipient}' does not exist in the system.", 'C')
                continue

            target_online = ChatServer.get_peer_info(recipient) is not None
            ChatServer.send_dm(username, recipient, data, send_framed_msg, queue_offline_message)
            
            if target_online:
                send_framed_msg(client_socket, f"[SYSTEM] Message delivered to '{recipient}'.", 'C')
            else:
                last_seen_time = ChatServer.get_last_seen(recipient)
                sys_msg = f"[SYSTEM] Message sent. User '{recipient}' is currently offline. Last seen at {last_seen_time}."
                send_framed_msg(client_socket, sys_msg, 'C')

        elif command == "SEND_TO_GROUP":
            status = ChatServer.send_group_message(username, recipient, data, send_framed_msg, queue_offline_message)
            if status != "SUCCESS":
                send_framed_msg(client_socket, f"ERROR: {status}", 'C')

        elif command == "CREATE_GROUP":
            created = ChatServer.create_group(recipient, username)
            msg = "GROUP CREATED" if created else "GROUP EXISTS"
            send_framed_msg(client_socket, msg, 'C')

        elif command == "ADD_TO_GROUP":
            status = ChatServer.add_to_group(recipient, username, data)
            if status == "SUCCESS":
                sys_msg = f"You were added to group '{recipient}' by {username}."
                ChatServer.send_dm("SYSTEM", data, sys_msg, send_framed_msg, queue_offline_message)

                # Notify existing group members about the new addition
                notify_msg = f"{data} has been added to the group by {username}."
                ChatServer.send_group_message("SYSTEM", recipient, notify_msg, send_framed_msg, queue_offline_message)

            send_framed_msg(client_socket, f"ADD_STATUS: {status}", 'C')

        elif command == "LEAVE_GROUP":
            left = ChatServer.leave_group(recipient, username)
            if left:
                sys_msg = f"{username} has left the group."
                ChatServer.send_group_message("SYSTEM", recipient, sys_msg, send_framed_msg, queue_offline_message)
                send_framed_msg(client_socket, "LEFT GROUP", 'C')
            else:
                send_framed_msg(client_socket, "GROUP NOT FOUND OR NOT MEMBER", 'C')

        elif command == "GET_PEER":
            peer = ChatServer.get_peer_info(recipient)
            filename = data if data else "a file" 

            if peer:
                _, ip, port = peer
                response = f"PEER_INFO:{recipient}:{ip}:{port}"
                send_framed_msg(client_socket, response, 'C')
            else:
                if ChatServer.is_group(recipient):
                    online_peers, offline_members = ChatServer.get_group_presence(recipient, exclude_user=username)
                    
                    # Instead of queuing a text notification, we instruct the client to upload the file to the server for offline members
                    if offline_members:
                        # Convert list of offline users into a comma-separated string to tell the client who to upload it for
                        offline_str = ",".join(offline_members)
                        send_framed_msg(client_socket, f"UPLOAD_TCP:{recipient}:{filename}:{offline_str}", 'C')

                    if online_peers:
                        peers_str = "|".join([f"{ip},{port}" for ip, port in online_peers])
                        response = f"GROUP_PEER_INFO:{recipient}:{peers_str}"
                        send_framed_msg(client_socket, response, 'C')
                    elif not offline_members:
                        send_framed_msg(client_socket, f"ERROR: No members exist in group {recipient}.", 'C')
                else:
                    if recipient not in postgresql_users:
                        send_framed_msg(client_socket, f"ERROR: User '{recipient}' does not exist in the system.", 'C')
                    else:
                        # Recipient is offline. Tell client to upload it to the server!
                        send_framed_msg(client_socket, f"UPLOAD_TCP:{recipient}:{filename}:{recipient}", 'C')

        # NEW COMMAND: Client uploads the file to the server for storage
        elif command == "STORE_FILE":
            filename = recipient  # In this context, 'recipient' actually contains the filename
            file_parts = data.split(":", 1)
            
            if len(file_parts) == 2:
                b64_data = file_parts[1]

                users = file_parts[0].split(",")

                for user in users:
                    # If user is online -> deliver immediately, otherwise queue for offline delivery

                    if user in ChatServer.clients:
                        target_socket = ChatServer.clients[user]["socket"]

                        send_framed_msg(target_socket, f"FILE_FROM:{username}:{filename}:{b64_data}", 'B')

                    else:
                        ChatServer.queue_offline_file(user, username, filename, b64_data)
                        send_framed_msg(client_socket, f"[SYSTEM] User '{user}' is offline. File '{filename}' stored for offline delivery.", 'C')
            else:
                send_framed_msg(client_socket, "ERROR: Invalid STORE_FILE format.", 'C')
        
        else:
            send_framed_msg(client_socket, "ERROR: UNKNOWN COMMAND.", 'C')

def authenticate_client(client_socket: socket.socket, addr) -> str | None:
    while True:
        _, msg = receive_framed_msg(client_socket)
        if not msg: return None
        
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
            break 
                
        elif msg.startswith("REG:"):
            _, reg_user, reg_pwd = msg.split(":", 2)

            with db_lock:
                if reg_user in postgresql_users:
                    send_framed_msg(client_socket, "USER_EXISTS", 'A')
                    continue

                postgresql_users[reg_user] = reg_pwd

            username = reg_user
            send_framed_msg(client_socket, "SUCCESS", 'A')
            break 

    _, port_msg = receive_framed_msg(client_socket)
    udp_port = int(port_msg.split(":")[1])
    
    ChatServer.register_client(username, client_socket, addr[0], udp_port)
    print(f"[REGISTERED] {username} at {addr[0]}:{udp_port}")
    
    flush_redis_queue(client_socket, username)
    # NEW: Deliver any buffered files immediately after text messages!
    flush_offline_files(client_socket, username)

    return username
    
def start_server() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print(f"Server listening on {HOST}:{PORT}...")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    start_server()
