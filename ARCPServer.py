import socket
import threading
import ChatServer

HOST = socket.gethostbyname(socket.gethostname())
PORT = 50000

db_lock = threading.Lock()

# --- MOCK INFRASTRUCTURE (Python Data Structures) ---
# Dictionary for Auth
postgresql_users = {
    "kb": "password123",
    "amahle": "securepass",
    "jacques": "cricketGOAT"
}

# Tuple-keyed Dictionary for Offline Queue: recipient -> [(sender, message), ...]
redis_message_queue = {} 

# Online Tracker: username -> (tcp_socket, ip, udp_port)
clients = {} 
# ----------------------------------------------------

def send_framed_msg(sock, message, msg_type='D'):
    data = message.encode('ascii')
    header = f"{msg_type}{len(data):04d}".encode('ascii')
    sock.sendall(header + data)

def receive_framed_msg(sock):
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

def flush_redis_queue(client_socket, recipient):
    """Delivers all queued messages sequentially when a user logs in."""
    with db_lock:
        if recipient in redis_message_queue:
            for sender, msg in redis_message_queue[recipient]:
                out_msg = f"[OFFLINE QUEUE] [{sender}]: {msg}"
                send_framed_msg(client_socket, out_msg, 'D')
            del redis_message_queue[recipient]

def handle_client(client_socket, addr):
    username = None
    try:
        # --- PHASE 1: AUTHENTICATION LOOP ---
        while True:
            msg_type, msg = receive_framed_msg(client_socket)
            if not msg: return # Disconnected during auth
            
            if msg.startswith("CHECK:"):
                check_user = msg.split(":")[1]
                if check_user in postgresql_users:
                    send_framed_msg(client_socket, "EXISTS", 'A')
                else:
                    send_framed_msg(client_socket, "NOT_FOUND", 'A')
                    
            elif msg.startswith("LOGIN:"):
                _, login_user, login_pwd = msg.split(":", 2)
                if postgresql_users.get(login_user) == login_pwd:
                    username = login_user
                    send_framed_msg(client_socket, "SUCCESS", 'A')
                    break # Exit auth loop!
                else:
                    send_framed_msg(client_socket, "FAIL", 'A')
                    
            elif msg.startswith("REG:"):
                _, reg_user, reg_pwd = msg.split(":", 2)
                with db_lock:
                    postgresql_users[reg_user] = reg_pwd
                username = reg_user
                send_framed_msg(client_socket, "SUCCESS", 'A')
                break # Exit auth loop!

        # Receive the dynamically assigned UDP port after login
        _, port_msg = receive_framed_msg(client_socket)
        udp_port = int(port_msg.split(":")[1])
        
        with db_lock:
            clients[username] = (client_socket, addr[0], udp_port)
        print(f"[REGISTERED] {username} at {addr[0]}:{udp_port}")
        
        # Immediately flush any offline messages waiting for them
        flush_redis_queue(client_socket, username)
        # ------------------------------------

        # --- PHASE 2: MAIN CHAT LOOP ---
        while True:
            msg_type, full_message = receive_framed_msg(client_socket)
            if not full_message: break

            # PEER DISCOVERY LOGIC
            if msg_type == 'C' and full_message.startswith("GET_PEER:"):
                target_user = full_message.split(":")[1]
                if target_user in clients:
                    _, t_ip, t_port = clients[target_user]
                    response = f"PEER_INFO:{target_user}:{t_ip}:{t_port}"
                    send_framed_msg(client_socket, response, 'C')
                else:
                    send_framed_msg(client_socket, f"ERROR: User {target_user} not online.", 'C')
                continue

            # DIRECT MESSAGING LOGIC (/sendmsg)
            if full_message.startswith("DM:"):
                _, target_user, content = full_message.split(":", 2)
                with db_lock:
                    if target_user in clients: # User is Online
                        target_sock = clients[target_user][0]
                        out_msg = f"[{username} (DM)]: {content}"
                        send_framed_msg(target_sock, out_msg, 'D')
                    else: # User is Offline -> Send to Redis Queue
                        if target_user not in redis_message_queue:
                            redis_message_queue[target_user] = []
                        redis_message_queue[target_user].append((username, content))
                        print(f"[REDIS] Queued DM from {username} to {target_user}")
                continue

            # STANDARD GROUP CHAT BROADCAST
            print(f"[{username} | Type: {msg_type}]: {full_message}")
            broadcast_msg = f"[{username}]: {full_message}"
            with db_lock:
                for client_user, (client_sock, _, _) in list(clients.items()):
                    if client_user != username: 
                        try:
                            send_framed_msg(client_sock, broadcast_msg, 'D')
                        except Exception:
                            pass
                            
    except Exception as e:
        print(f"[ERROR] with {addr}: {e}")
    finally:
        client_socket.close()
        with db_lock:
            if username in clients:
                del clients[username]
                print(f"[DISCONNECTED] {username}")

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print(f"Server listening on {HOST}:{PORT}...")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    start_server()