# chat_server.py

from threading import Lock

clients = {}
groups = {} # group_id -> set of usernames
clients_lock = Lock()

def register_client(username, client_socket, ip, udp_port):
    with clients_lock:
        clients[username] = (client_socket, ip, udp_port)

def remove_client(username):
    with clients_lock:
        if username in clients:
            del clients[username]

def get_peer_info(username):
    with clients_lock:
        return clients.get(username)

def broadcast(sender, message, send_func):
    with clients_lock:
        for user, (sock, _, _) in clients.items():
            if user != sender:
                send_func(sock, f"[{sender}]: {message}", 'D')

def send_dm(sender, target, content, send_func, redis_queue):
    with clients_lock:
        if target in clients:
            sock = clients[target][0]
            send_func(sock, f"[{sender} (DM)]: {content}", 'D')
            return True
    return False

def create_group(group_id, creator) -> bool:
    with clients_lock:
        if group_id in groups:
            return False
        groups[group_id] = {creator}
        return True
    
def join_group(group_id, username) -> bool:
    with clients_lock:
        if group_id not in groups:
            return False
        groups[group_id].add(username)
        return False
    
def leave_group(group_id, username) -> bool:
    with clients_lock:
        if group_id in groups and username in groups[group_id]:
            groups[group_id].remove(username)
            return True
        return False
    
def send_group_message(sender, group_id, message, send_func):
    with clients_lock:
        if group_id not in groups:
            return False
        members = groups[group_id]
        for member in members:
            if member != sender and member in clients:
                sock = clients[member][0]
                send_func(sock, f"[{group_id}] {group_id}: {message}", 'D')
        return True