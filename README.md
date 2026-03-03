# C00NECTED
The repo defines a networked chat application called "C00NECTED" for a CSC3002F assignment at the University of Cape Town

Problem 1
1. TCP Stream Fragmentation (The "amahle" / "Virat Kohli" Issue)
•	The Problem: When sending text messages through the TCP chat socket, the messages were frequently cut off or split across multiple lines on the receiving end (e.g., "amahle" arriving as "amahl" and "e").
•	The Technical Cause: TCP is a stream-oriented transport protocol, meaning it guarantees delivery and order, but it does not preserve message boundaries. The recv() buffer was pulling whatever bytes were currently available on the network stack rather than waiting for a complete application-level message.
•	The Solution: Implemented Message Framing via a Length-Prefix protocol. By prepending a fixed-length header containing the payload size (as specified in the Stage 1 design), the receiving socket was programmed to read exactly the required number of bytes to reassemble the complete message before processing it.


PROBLEM 1 SOLUTION
In your Stage 1, you defined Data messages to carry chat content and media. To make your prototype more "real," let's update the server to broadcast messages so clients can actually talk to each other

Update your Server's handle_client function:

Python
def broadcast(message, sender_socket):
    """Sends a message to everyone except the sender"""
    for user in clients:
        client_sock, _ = clients[user]
        if client_sock != sender_socket:
            try:
                client_sock.send(message)
            except:
                client_sock.close()
