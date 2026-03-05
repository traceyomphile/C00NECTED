# C00NECTED

A networked chat application developed for CSC3002F at the University of Cape Town. This project implements a client-server architecture with peer-to-peer (P2P) media transfer capabilities, supporting direct messaging, group chats, and file sharing.

## Features

- **User Authentication**: Register new accounts or login with existing credentials.
- **Direct Messaging**: Send private messages to individual users.
- **Group Messaging**: Create groups, add members, and send messages to groups.
- **P2P File Transfer**: Send files directly to users or groups using UDP for efficient media exchange.
- **Offline Messaging**: Messages are queued for offline users and delivered upon login.
- **Real-time Communication**: TCP for control messages and UDP for media transfers.
- **Thread-safe Operations**: Server handles multiple clients concurrently with proper locking.

## Architecture

The application consists of three main components:

- **ARCPServer.py**: The main server that handles client connections, authentication, and message routing.
- **ChatServer.py**: Contains the core logic for managing clients, groups, and message delivery.
- **Client.py**: The client application that connects to the server and provides the user interface.

### Communication Protocol

- **TCP (Port 50000)**: Used for authentication, control messages, and text chat.
- **UDP**: Used for P2P file transfers on dynamically assigned ports.
- **Message Framing**: All TCP messages use a length-prefixed format: `[Type(1 char)][Length(4 chars)][Data]`.

Message Types:
- `A`: Authentication messages
- `D`: Data messages (chat content)
- `C`: Control messages (system notifications, peer info)

## Installation

1. Ensure Python 3.x is installed on your system.
2. Clone or download the project files.
3. No additional dependencies are required beyond the standard library.

## Usage

### Starting the Server

Run the server to start listening for client connections:

```bash
python ARCPServer.py
```

If using Conda, activate your environment first:

```bash
conda activate <your_env_name>
python ARCPServer.py
```

Alternatively, you can run it directly with Conda:

```bash
conda run -n <your_env_name> python ARCPServer.py
```

The server will start on the local IP address at port 50000.

### Running the Client

Open a new terminal and run the client. On Windows you can also use the `py` launcher:

```bash
python Client.py
# or
py Client.py
```

If using Conda, activate your environment first:

```bash
conda activate <your_env_name>
python Client.py
# or
py Client.py
```

Alternatively:

```bash
conda run -n <your_env_name> python Client.py
```

The client will prompt for username and password. You can register a new account or login with an existing one.

### Commands

Once authenticated, use the following commands in the client interface:

- `SEND:<user>:<message>` - Send a direct message to a user.
- `CREATE_GROUP:<group_name>` - Create a new group.
- `ADD_TO_GROUP:<group_name>:<user>` - Add a user to a group.
- `LEAVE_GROUP:<group_name>` - Leave a group.
- `SEND_GROUP:<group_name>:<message>` - Send a message to a group.
- `SEND_FILE:<user/group>:<filepath>` - Send a file to a user or group.
- `COMMANDS` - Display the help menu.
- `EXIT` - Disconnect from the server.

### File Transfer

Files are sent via P2P UDP connections. The server facilitates peer discovery, and files are transferred directly between clients.

## Technologies Used

- **Python**: Core programming language.
- **Sockets**: TCP and UDP for network communication.
- **Threading**: For concurrent client handling and message processing.
- **Datetime**: For timestamping messages.

## Known Issues and Solutions

### Problem 1: TCP Stream Fragmentation

**Issue**: Messages were being fragmented during TCP transmission, causing incomplete reception.

**Cause**: TCP is stream-oriented and doesn't preserve message boundaries.

**Solution**: Implemented message framing with a length-prefixed header to ensure complete message assembly before processing.

### Broadcasting Messages

To enable real-time chat, the server broadcasts messages to all connected clients except the sender.

## Authors

- Developed as part of CSC3002F coursework at the University of Cape Town.
- Karabo Nkambule, Tracey Letlape, Amahle Mbambo
- Date: 2024-06-01

## License

This project is for educational purposes.
