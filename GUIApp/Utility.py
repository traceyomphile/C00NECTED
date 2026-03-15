"""
VoiceRecorder.py - Records microphone input to a temporary WAV file.

Functions:
    - start: Initializes and starts the audio input stream.
    - stop: Closes the PyAudio stream and creates a temp wave file to store the voice note.
    - play: Allows the user to listen to the voice note and continue using the app simultaneously.

Date: 15-03-2026
"""
import pyaudio
import threading
import time
import tempfile
import os
import wave as _wave

class VoiceRecorder:

    RATE     = 44100
    CHANNELS = 1
    FORMAT   = pyaudio.paInt16
    CHUNK    = 1024

    def __init__(self):
        self._pa       = pyaudio.PyAudio()
        self._stream   = None
        self._frames   = []
        self._recording = False
        self._start_ts  = 0.0

    def start(self):
        if self._recording:
            return
        self._frames    = []
        self._recording = True
        self._start_ts  = time.time()
        self._stream = self._pa.open(
            format=self.FORMAT, channels=self.CHANNELS,
            rate=self.RATE, input=True,
            frames_per_buffer=self.CHUNK,
            stream_callback=self._callback
        )
        self._stream.start_stream()

    def _callback(self, in_data, frame_count, time_info, status):
        if self._recording:
            self._frames.append(in_data)
        return (None, pyaudio.paContinue)

    def stop(self) -> tuple:
        """Stop recording, write WAV, return (path, duration_seconds)."""
        if not self._recording:
            return None, 0
        self._recording = False
        duration = time.time() - self._start_ts

        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return None, 0

        fd, path = tempfile.mkstemp(suffix='.wav', prefix='c00n_voice_')
        os.close(fd)
        with _wave.open(path, 'wb') as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self._pa.get_sample_size(self.FORMAT))
            wf.setframerate(self.RATE)
            wf.writeframes(b''.join(self._frames))

        return path, round(duration, 1)

    @staticmethod
    def play(path: str):
        """Play a WAV file in a daemon thread (non-blocking)."""
        def _play():
            pa = pyaudio.PyAudio()
            try:
                with _wave.open(path, 'rb') as wf:
                    stream = pa.open(
                        format=pa.get_format_from_width(wf.getsampwidth()),
                        channels=wf.getnchannels(),
                        rate=wf.getframerate(),
                        output=True
                    )
                    data = wf.readframes(1024)
                    while data:
                        stream.write(data)
                        data = wf.readframes(1024)
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            finally:
                pa.terminate()
        threading.Thread(target=_play, daemon=True).start()

    def __del__(self):
        try:
            self._pa.terminate()
        except Exception:
            pass