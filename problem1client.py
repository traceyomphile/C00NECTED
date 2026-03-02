import socket
import threading

SERVER_IP = '127.0.0.1'
TCP_PORT = 5000
UDP_PORT = 6000 # Different port for UDP media reception

def receive_udp_media():
    """Handles P2P binary media exchange [cite: 51, 32]"""
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(('0.0.0.0', UDP_PORT))
    print(f"[UDP] Listening for media on port {UDP_PORT}...")
    
    while True:
        data, addr = udp_sock.recvfrom(4096) # Adjust buffer for binary [cite: 28]
        print(f"[UDP] Received {len(data)} bytes of media from {addr}")

def start_client():
    # 1. Setup TCP for Control/Chat [cite: 31]
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.connect((SERVER_IP, TCP_PORT))
    
    username = input("Enter username: ")
    tcp_sock.send(username.encode('ascii'))

    # 2. Start UDP thread for P2P [cite: 18, 33]
    threading.Thread(target=receive_udp_media, daemon=True).start()

    # 3. Main loop for sending Command/Data messages
    while True:
        msg = input("Enter message (or 'QUIT'): ")
        if msg == 'QUIT': break
        # Formatting: Type(1) + Len(4) + Payload [cite: 53, 54]
        header = f"D{len(msg):04d}" 
        tcp_sock.send((header + msg).encode('ascii'))

if __name__ == "__main__":
    start_client()
