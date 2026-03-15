"""
CallManager.py - Manages real-time UPD audio calls using the Dispatcher Pattern.
                It ensures exactly one threaad manages the UDP socket at any given time,
                preventing race conditions and packet loss.

Functions:
    - create_udp_socket: Initialises the UDP socket for all interfaces (0.0.0.0) and binds to an ephemeral port.
    - start_dispatch(): Spawns the _udp_dispatch_loop in a daemon thread.
    - start_outgoing_call: Spawbs the media streaming threads.
    - accept_incoming_call: Initialises audio playback and capture.
    - end_call: Resets the listener event.
    - handle_punch_responses: Signals when it is safe to start streaming to both the sender and receiver.
    - listen_for_incoming_calls: Listens for the first audio pavcket from a stranger.

Date: 15-03-2026
"""
import queue
import socket
import threading
import pyaudio
import time

# ----------- Configuration ----------

PKT_AUDIO   = b'\x01'
PKT_END     = b'\xFF'

AUDIO_FORMAT   = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_RATE     = 44100
AUDIO_CHUNK    = 1024

class CallManager:

    def __init__(self, gui_queue: queue.Queue):
        self.gui_queue = gui_queue
        self.udp_sock: socket.socket | None = None
        self.peer_addr: tuple | None = None
        self.call_ended = True
        self.call_type = 'audio'
        self.incoming_caller_addr: tuple | None = None
        
        # Thread-safe queues for media
        self._audio_queue = queue.Queue(maxsize=50)
        
        self._call_done_event = threading.Event()
        self._hole_punched = threading.Event()

    # ── Socket setup ──────────────────────────────────────────────────────────

    def create_udp_socket(self) -> int:
        if self.udp_sock:
            try: 
                self.udp_sock.close()
            except: 
                pass
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.bind(('0.0.0.0', 0))
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        return self.udp_sock.getsockname()[1]

    # ── Safe UDP Dispatcher Thread ────────────────────────────────────────────

    def start_dispatcher(self):
        """Starts the single central reader thread for all UDP packets."""
        threading.Thread(target=self._udp_dispatch_loop, daemon=True).start()

    def _udp_dispatch_loop(self):
        """Central UDP packet mailroom. It reads ALL packets and sorts them."""
        self.udp_sock.settimeout(1.0)
        while True:
            try:
                datagram, addr = self.udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                break

            if not datagram:
                continue

            if datagram in (b'PUNCH', b'PUNCH_ACK'):
                self.handle_punch_response(addr)
                continue

            pkt_type = datagram[0:1]

            if self.call_ended:
                # If we are not in a call, watch for incoming call packets
                if pkt_type == PKT_AUDIO:
                    if self.incoming_caller_addr != addr:
                        self.incoming_caller_addr = addr
                        # Let the GUI know an incoming UDP call arrived
                        self.gui_queue.put(("INCOMING_CALL_UDP",))
            else:
                # We are in a call: Sort packets into their specific playback queues
                if pkt_type == PKT_AUDIO:
                    if not self._audio_queue.full():
                        self._audio_queue.put_nowait(datagram[1:])
                elif pkt_type == PKT_END:
                    self.call_ended = True
                    self.gui_queue.put(("CALL_ENDED_REMOTE",))

    # ── Outgoing call (caller side) ───────────────────────────────────────────

    def start_outgoing_call(self, peer_ip: str, peer_udp_port: int, call_type: str = 'audio'):
        self._begin_call(call_type)
        self.peer_addr = (peer_ip, int(peer_udp_port))

        self._hole_punched.clear()
        threading.Thread(target=self._hole_punch_worker, daemon=True).start()

        if not self._hole_punched.wait(timeout=10.0):
            self.gui_queue.put(('STATUS', 'Call failed: NAT traversal timeout'))
            self.end_call()
            return
        
        self._start_media_threads(call_type)

    def _hole_punch_worker(self):
        punch_packet = b'PUNCH'
        while not self.call_ended and not self._hole_punched.is_set():
            if self.peer_addr:
                self.udp_sock.sendto(punch_packet, self.peer_addr)
                time.sleep(0.25)

    def handle_punch_response(self, addr):
        if not self._hole_punched.is_set():
            self._hole_punched.set()
            self.udp_sock.sendto(b'PUNCH_ACK', addr)

    def _start_media_threads(self, call_type: str):
        # Capture the stop event for THIS call; each thread holds its own
        # reference so it exits when THIS call ends, not some future call.
        stop = self._stop_event
        threading.Thread(target=self._stream_audio, args=(stop,), daemon=True, name='audio-send').start()
        threading.Thread(target=self._recv_audio,   args=(stop,), daemon=True, name='audio-recv').start()

    # ── Accepting an incoming call (callee side) ──────────────────────────────

    def accept_incoming_call(self, call_type: str = 'audio'):
        if not self.incoming_caller_addr:
            self.gui_queue.put(('STATUS', 'Call accept failed: no caller address'))
            return False

        self._begin_call(call_type)
        self.peer_addr = self.incoming_caller_addr

        # Start UDP dispatcher first
        #self._start_udp_dispatcher()

        # Send punch ACK to open our NAT to them
        self.udp_sock.sendto(b'PUNCH_ACK', self.peer_addr)

        self._start_media_threads(call_type)
        return True

    def _begin_call(self, call_type: str):
        self.call_ended = False
        self.call_type  = call_type
        # Fresh stop-event for every call so old threads can't outlive their call
        self._stop_event = threading.Event()

    # ── End call ─────────────────────────────────────────────────────────────

    def end_call(self):
        self.call_ended = True
        # Signal the per-call stop event so any still-running media threads
        # from THIS call exit immediately, even if call_ended gets reset later
        if hasattr(self, '_stop_event'):
            self._stop_event.set()

        # Reset hole-punch state so the next call can punch through cleanly
        self._hole_punched.clear()
        if self.udp_sock and self.peer_addr:
            try: 
                self.udp_sock.sendto(PKT_END, self.peer_addr)
            except: 
                pass

        self.peer_addr = None
        self.incoming_caller_addr = None

        # Flush stale packets out of the queues
        while not self._audio_queue.empty():
            try: 
                self._audio_queue.get_nowait()
            except: 
                break
        
        self._call_done_event.set()     # let the listener resume

    # ── Background listener ───────────────────────────────────────────────────

    def listen_for_incoming(self):
        """
        Runs forever as a daemon thread.
        - While no call is active: reads packets and detects new calls.
        - While a call is active: blocks on _call_done_event (doesn't touch
          the socket so call threads have exclusive access).
        """
        self.udp_sock.settimeout(1.0)
        while True:
            # Block here while a call is active — dispatcher owns the socket
            self._call_done_event.wait()

            try:
                datagram, addr = self.udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                break

            if not datagram:
                continue

            if datagram == b'PUNCH':
                self.udp_sock.sendto(b'PUNCH_ACK', addr)

            pkt = datagram[0:1]
            if pkt != PKT_AUDIO:
                continue

            # First meaningful packet from a caller — notify the GUI
            self.incoming_caller_addr = addr
            self._call_done_event.clear()
            self.gui_queue.put(('INCOMING_CALL_UDP',))

    # ── Audio threads ─────────────────────────────────────────────────────────

    def _stream_audio(self, stop_event: threading.Event):
        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
                rate=AUDIO_RATE, input=True, frames_per_buffer=AUDIO_CHUNK
            )
            while not stop_event.is_set():
                pcm = stream.read(AUDIO_CHUNK, exception_on_overflow=False)
                if self.peer_addr:
                    self.udp_sock.sendto(PKT_AUDIO + pcm, self.peer_addr)
        except Exception as e:
            if not stop_event.is_set():
                self.gui_queue.put(('STATUS', f'[Call] Audio send error: {e}'))
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            pa.terminate()

    def _recv_audio(self, stop_event: threading.Event):
        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
                rate=AUDIO_RATE, output=True, frames_per_buffer=AUDIO_CHUNK
            )
            while not stop_event.is_set():
                try:
                    # Safely pull ONLY audio packets from the mailroom queue
                    payload = self._audio_queue.get(timeout=0.5)
                    stream.write(payload)
                except queue.Empty:
                    continue
        except Exception as e:
            if not stop_event.is_set():
                self.gui_queue.put(('STATUS', f'[Call] Audio recv error: {e}'))
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            pa.terminate()

