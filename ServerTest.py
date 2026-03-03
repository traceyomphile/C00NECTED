import socket
import threading

# Configuration based on your Stage 1 design
HOST = socket.gethostbyname(socket.gethostname())  # Get local IP address
PORT = 5000 
clients = {} # Stores username -> (socket, address)

def handle_client(client_socket, addr):
    try:
        # Example of your 'Command' message for registration [cite: 50]
        username = client_socket.recv(1024).decode('ascii')
        clients[username] = (client_socket, addr)
        print(f"[REGISTERED] {username} at {addr}")

        while True:
            # Framing: Expecting Header (Type|Len) then Body [cite: 53, 54]
            header = client_socket.recv(10).decode('ascii') 
            if not header: break
            
            # Logic to route Data or Control messages goes here [cite: 51]
            print(f"[MSG] Received header: {header} from {username}")
            
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        client_socket.close()

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print(f"Server listening on {HOST}:{PORT}...")
    
    while True:
        conn, addr = server.accept()
        thread = threading.Thread(target=handle_client, args=(conn, addr))
        thread.start()

if __name__ == "__main__":
    start_server()