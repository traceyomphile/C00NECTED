
import os
import threading
import json
from typing import Dict, List, Optional

class ChatHistory:
    """Manages per-user chat history persisteda as JSON"""
    HISTORY_DIR = "chat_history"
    CACHE_VERSION = 1   # Bump when format changes significantly.

    def __init__(self, username: str):
        self.username = username
        os.makedirs(self.HISTORY_DIR, exist_ok=True)
        self._path = os.path.join(self.HISTORY_DIR, f"{username}.json")
        self._lock = threading.Lock()
        self._data = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def conversations(self) -> dict:
        return self._data.setdefault('conversations', {})

    @property
    def known_groups(self) -> set:
        return set(self._data.get('known_groups', []))
    
    @property
    def last_fetched(self) -> Dict[str, str]:
        """Last server timestamp fetched for each chat (ISO format)"""
        return self._data.setdefault('last_fetched', {})

    def append(self, chat_id: str, msg: dict, from_server: bool = False):
        """
        Add a message to the local cache.
        if from server=True, we try to avoid duplicates based on timestamp.
        """
        with self._lock:
            conv = self.conversations.setdefault(chat_id, [])

            # Deduplication when merging from server
            if from_server:
                msg_ts = msg.get('timestamp')
                msg_content = msg.get('content', '')
                if any(
                    m.get('timestamp') == msg_ts and m.get('content', '') == msg_content
                    for m in conv
                ):
                    return
                
            conv.append(msg)
            conv.sort(key=lambda m: m.get('timestamp', '0000-00-00 00:00:00'))                
            self._save_nolock()

    def add_to_known_groups(self, chat_id: str):
        """Record that chat_id is a group."""
        with self._lock:
            groups = self._data.setdefault('known_groups', [])
            if chat_id not in groups:
                groups.append(chat_id)
                self._save_nolock()

    def set_last_fetched(self, chat_id: str, timestamp: str):
        """Update the last timestamp we successfully fetched from server."""
        with self._lock:
            self.last_fetched[chat_id] = timestamp
            self._save_nolock()

    def get_last_fetched(self, chat_id: str) -> Optional[str]:
        """Get the most recent timestamp we have from serber for this chat."""
        return self.last_fetched.get(chat_id)
    
    def merge_from_server(self, chat_id: str, server_messages: List[dict]):
        """
        Merge a batch of messages received from the server.
        Usually called after GET_HISTORY request.
        """
        if not server_messages:
            return

        # Find the latest timestamp in this batch
        latest_ts = max(
            (m.get('timestamp') for m in server_messages),
            default=None
        )

        for msg in server_messages:
            self.append(chat_id, msg, from_server=True)

        if latest_ts:
            self.set_last_fetched(chat_id, latest_ts)

    def ensure_chat(self, chat_id: str):
        """Create an empty conversation slot if it doesn't exist."""
        with self._lock:
            if chat_id not in self.conversations:
                self.conversations[chat_id] = []
                self._save_nolock()

    def delete_chat(self, chat_id: str):
        with self._lock:
            self.conversations.pop(chat_id, None)
            known = self._data.get('known_groups', [])
            if chat_id in known:
                known.remove(chat_id)
            self.last_fetched.pop(chat_id, None)
            self._save_nolock()

    def clear_all(self):
        """Wipe local cache (e.g. on logout or explicit clear)"""
        with self._lock:
            self._data = {'conversations': {}, 'known_groups': [], 'last_fetched': {}}
            self._save_nolock()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                data = json.load(f)

                if data.get('_version', 0) < self.CACHE_VERSION:
                    return {}
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                'conversations': {},
                'known_groups': [],
                'last_fetched': {},
                '_version': self.CACHE_VERSION
            }

    def _save_nolock(self):
        try:
            data_to_save = {**self._data, '_version': self.CACHE_VERSION}
            with open(self._path, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_messages(self, chat_id: str) -> List[dict]:
        """Get stored messages for a chat (used by UI)"""
        return self.conversations.get(chat_id, [])