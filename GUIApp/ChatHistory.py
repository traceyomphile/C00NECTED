"""
ChatHistory.py - Saves and loads chat history as a JSON file per user.
File: chat_history/<username>/json
Format: { chat_id: [msg_dict, ...], ... }

Properties:
    - conversations: Public property that returns the set of all conversations the user had.
    - known_groups: Public property that returns the set of all known groups.

Methods:
    - append: Appends one message dict and persists immediately.
    - ad_to_known_groups: Add a newly created group to the known groups set.
    - create_conv_slot: Create an empty conversation slot for a new conversation.
    - delete_chat: Deletes an a conversation/chat slot.

Date: 15-03-2026
"""
import os
import threading
import json

class ChatHistory:

    HISTORY_DIR = "chat_history"

    def __init__(self, username: str):
        self.username = username
        os.makedirs(self.HISTORY_DIR, exist_ok=True)
        self._path = os.path.join(self.HISTORY_DIR, f"{username}.json")
        self._lock = threading.Lock()
        self._data = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def conversations(self) -> dict:
        return self._data.get('conversations', {})

    @property
    def known_groups(self) -> set:
        return set(self._data.get('known_groups', []))

    def append(self, chat_id: str, msg: dict):
        """Append one message dict and persist immediately."""
        with self._lock:
            convs = self._data.setdefault('conversations', {})
            convs.setdefault(chat_id, []).append(msg)
            self._save_nolock()

    def add_to_known_groups(self, chat_id: str):
        """Record that chat_id is a group."""
        with self._lock:
            groups = self._data.setdefault('known_groups', [])
            if chat_id not in groups:
                groups.append(chat_id)
                self._save_nolock()

    def create_conv_slot(self, chat_id: str):
        """Create an empty conversation slot if it doesn't exist."""
        with self._lock:
            self._data.setdefault('conversations', {}).setdefault(chat_id, [])
            self._save_nolock()

    def delete_chat(self, chat_id: str):
        with self._lock:
            self._data.get('conversations', {}).pop(chat_id, None)
            try:
                self._data.get('known_groups', []).remove(chat_id)
            except ValueError:
                pass
            self._save_nolock()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_nolock(self):
        try:
            with open(self._path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass