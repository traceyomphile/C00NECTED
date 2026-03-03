"""
Client.py - A Python client for a chat application with P2P media transfer capabilities.
This client connects to a central server for authentication and message routing, while also managing a unique UDP
port for direct peer-to-peer media exchanges. The client supports sending framed messages for both control and data, as well as receiving messages and media from peers.
Functions:
- receive_udp_media: Listens for incoming UDP datagrams and writes them to a file until
  an EOF signal is received.
- send_image_udp: Sends a file to a target IP and UDP port using datagram sockets.
- send_framed_msg: Sends a framed message over a TCP socket with a specified message type.
- receive_framed_msg: Receives a framed message from a TCP socket, extracting the
  message type and content.
- receive_tcp_messages: Listens for incoming TCP messages and processes them based on their type.
- authenticate_console: Handles the interactive console registration/login logic.
- start_client: Initializes the client, manages authentication, and starts the main chat interface.
Date: 2024-06-01
"""

import socket
import threading
import os
import time

SERVER_IP ='196.47.192.177'
TCP_PORT = 50000
UDP_PORT = 0 # Different port for UDP media reception

def receive_udp_media(udp_sock: socket.socket) -> None:
    """
    Listens for incoming UDP datagrams and writes them to a file until an EOF signal is received.
    Parameters:
        - udp_sock: The UDP socket bound to the client's unique port for receiving media.
    Returns:
        - None. The function runs indefinitely until an EOF signal is received, at which point it closes the socket."""
    output_filename = f"received_{int(time.time())}.jpg" 
    try:
        bytes_received = 0
        chunks = {}
        expected_bytes = None
        
        while True:
            data, addr = udp_sock.recvfrom(4100)

            if data.startswith(b"SIZE:"):
                expected_bytes = int(data.decode().split(':')[1])
                print(f"\n[UDP] Expecting {expected_bytes} bytes from {addr}. Receiving...")
                continue

            # Ignore packets until size is known
            if expected_bytes is None:
                continue

            seq = int.from_bytes(data[:4], byteorder='big')
            chunk = data[4:]

            if seq not in chunks:
                chunks[seq] = chunk
                bytes_received += len(chunk)

            # Stop when full file received
            if bytes_received >= expected_bytes:
                break

        with open(output_filename, 'wb') as file:
            for i in sorted(chunks.keys()):
                file.write(chunks[i])

        print(f"\n[UDP] Successfully received {bytes_received} bytes. \nSaved as {output_filename}")
    except Exception as e:
        print(f"\n[ERROR] UDP Reception failed: {e}")


def send_image_udp(filepath, target_ip, target_udp_port) -> None:
    """
    Sends a file to a target IP and UDP port using datagram sockets.
    Parameters:
        - filepath: The path to the file to be sent.
        - target_ip: The IP address of the recipient client.
        - target_udp_port: The UDP port on which the recipient client is listening for media.
    Returns:
        - None. The function reads the file in chunks and sends it as UDP datagrams until the entire file is transmitted, followed by an EOF signal.
        """
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    try:
        # Open the file in 'rb' (read binary) mode
        with open(filepath, 'rb') as file:
            file_size = os.path.getsize(filepath)
            print(f"[UDP] Preparing to send {file_size} bytes to {target_ip}:{target_udp_port}")
            
            udp_sock.sendto(f"SIZE:{file_size}".encode(), (target_ip, target_udp_port))

            bytes_sent = 0
            packet_number = 0

            while True:
                # Read the file in 4KB chunks
                chunk = file.read(4096) 
                if not chunk:
                    break 
                
                # Send the datagram directly to the peer
                header = packet_number.to_bytes(4, byteorder='big')
                udp_sock.sendto(header + chunk, (target_ip, target_udp_port))

                bytes_sent += len(chunk)
                packet_number += 1
                
                # A tiny sleep prevents overwhelming the local buffer during testing
                time.sleep(0.001) 
                
        print(f"[UDP] Successfully transmitted {bytes_sent} bytes.")
        
    except FileNotFoundError:
        print(f"[ERROR] Could not find file: {filepath}")
    except Exception as e:
        print(f"[ERROR] UDP Transmission failed: {e}")
    finally:
        udp_sock.close()

def send_framed_msg(sock: socket.socket, message: str, msg_type: str='DATA') -> None:
    """
    Sends a framed message over a TCP socket with a specified message type.
    Parameters:
        - sock: The TCP socket through which the message will be sent.
        - message: The string message to be sent.
        - msg_type: A string indicating the type of message (default is 'DATA').
    Returns:
        - None. The function encodes the message with a header containing the message type and length, and sends it through the socket.
    """
    data = message.encode('ascii')
    header = f"{msg_type}{len(data):04d}".encode('ascii')
    sock.sendall(header + data)

def receive_framed_msg(sock: socket.socket) -> tuple[str, str] | tuple[None, None]:
    """
    Receives a framed message from a TCP socket, extracting the message type and content.
    Parameters:
        - sock: The TCP socket from which the message will be received.
    Returns:
        - A tuple containing the message type (as a string) and the message content (as a string). If the connection is closed, returns (None, None).
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

pending_transfers = {} 

def receive_tcp_messages(sock: socket.socket) -> None:
    """
    Listens for incoming TCP messages and processes them based on their type.
    Parameters:
        - sock: The TCP socket through which messages are received.
    Returns:
        - None. The function runs indefinitely, processing incoming messages until the connection is lost.
    """
    global pending_transfers
    while True:
        try:
            msg_type, msg = receive_framed_msg(sock)
            if not msg: break
            
            if msg_type == 'C' and msg.startswith("PEER_INFO:"):
                _, target_user, t_ip, t_port = msg.split(':')
                if target_user in pending_transfers:
                    filepath = pending_transfers.pop(target_user)
                    print(f"\n[SYSTEM] Peer found! Initiating P2P transfer of {filepath} to {target_user}...")
                    threading.Thread(target=send_image_udp, args=(filepath, t_ip, int(t_port)), daemon=True).start()
                continue
            
            print(f"\n{msg}")
        except Exception as e:
            print(f"\n[ERROR] Connection lost: {e}")
            break

def authenticate_console(tcp_sock: socket.socket) -> str | None:
    """
    Handles the interactive console registration/login logic.
    Parameters:
        - tcp_sock: The TCP socket through which authentication messages are sent and received.
    Returns:
        - The authenticated username as a string if login/registration is successful, or None if the user chooses to exit or authentication fails.
        The function prompts the user for a username and checks with the server if it exists. If it does, it asks for a password and attempts to log in. If the username does not exist, it offers the option to register a new account. The process continues until successful authentication or user exit.
    """
    while True:
        username = input("Enter username: ").strip()
        send_framed_msg(tcp_sock, f"CHECK {username}", 'A')
        _, resp = receive_framed_msg(tcp_sock)
        
        if resp == "EXISTS":
            while True:
                pwd = input(f"Enter password for {username} (or 'exit' to quit): ").strip()
                if pwd.lower() == 'exit':
                    return None
                
                send_framed_msg(tcp_sock, f"LOGIN {username}:{pwd}", 'A')
                _, auth_resp = receive_framed_msg(tcp_sock)
                
                if auth_resp == "SUCCESS":
                    print("\n[SYSTEM] Login successful! Welcome to the Chat.")
                    return username
                else:
                    print("[ERROR] Incorrect password. Try again.")
                    
        elif resp == "NOT_FOUND":
            reg = input("User not found. Do you want to register? (yes/no): ").strip().lower()
            if reg == 'yes':
                while True:
                    new_user = input("Enter a unique username: ").strip()
                    send_framed_msg(tcp_sock, f"CHECK {new_user}", 'A')
                    _, check_resp = receive_framed_msg(tcp_sock)
                    
                    if check_resp == "EXISTS":
                        print("[ERROR] Username already taken. Try another.")
                    else:
                        new_pwd = input("Enter new password: ").strip()
                        send_framed_msg(tcp_sock, f"REG {new_user}:{new_pwd}", 'A')
                        _, reg_resp = receive_framed_msg(tcp_sock)
                        
                        if reg_resp == "SUCCESS":
                            print("\n[SYSTEM] Registration successful! Welcome to the Chat.")
                            return new_user
            else:
                return None # Exits program if they decline registration

def start_client() -> None:
    """
    Initializes the client, manages authentication, and starts the main chat interface.
    Parameters:
        - None. The function sets up the TCP connection, handles user authentication, binds a UDP socket for media reception, and starts threads for listening to both TCP and UDP messages. It also provides a command interface for sending messages and files to other users.
    Returns:
        - None. The function runs indefinitely until the user chooses to exit the chat interface.
    """
    # 1. Boot up TCP socket
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.connect((SERVER_IP, TCP_PORT))
    
    # 2. Execute Blocking Authentication Loop
    username = authenticate_console(tcp_sock)
    if not username:
        print("Terminating program...")
        tcp_sock.close()
        return

    # 3. Bind UDP socket and register port with server
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(('0.0.0.0', 0))
    my_udp_port = udp_sock.getsockname()[1]
    
    send_framed_msg(tcp_sock, f"PORT {my_udp_port}", 'A')
    print(f"[UDP] Listening for P2P media on unique port {my_udp_port}...")

    # 4. Start concurrent listener threads
    threading.Thread(target=receive_udp_media, args=(udp_sock,), daemon=True).start()
    threading.Thread(target=receive_tcp_messages, args=(tcp_sock,), daemon=True).start()

    # 5. Main chat interface
    while True:
        msg = input(">> ").strip()

        if msg.upper() == "EXIT":
            break

        parts = msg.split(" ", 2)
        if len(parts) < 2:
            print("[ERROR] Format: <COMMAND> <RECIPIENT_ID> <DATA>")
            continue

        command, recipient = parts[0].upper(), parts[1]
        data = parts[2] if len(parts) > 2 else ""

        # File transfer command
        if command == "SENDFILE":
            # Usage: SENDFILE <recipient_username> <file_path>
            filepath = data
            if not filepath:
                print("[ERROR]: SENDFILE <>recipient_username> <file_path>")
                continue

            pending_transfers[recipient] = filepath
            send_framed_msg(tcp_sock, f"GET_PEER {recipient}", 'COMMAND')
            continue
        
        # Standard send command
        elif command == "SEND":
            if not data:
                print("[ERROR] Usage: SEND <recipient_username> <message>")
                continue

            send_framed_msg(tcp_sock, f"{recipient} {data}", 'DATA')
            continue

        # Group management commands
        elif command in ["CREATE_GROUP", "JOIN_GROUP", "LEAVE_GROUP"]:
            send_framed_msg(tcp_sock, f"{command} {recipient}", 'COMMAND')
        
        # Peer lookup command
        elif command == "GET_PEER":
            send_framed_msg(tcp_sock, f"GET_PEER {recipient}", 'COMMAND')
            continue

        else:
            print("[ERROR] Unknown command.")

if __name__ == "__main__":
    start_client()