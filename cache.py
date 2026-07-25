"""Cache file đơn giản để tránh gọi lại Serper (tiết kiệm credit)."""
import hashlib
import json
import os


class FileCache:
    def __init__(self, directory: str, enabled: bool = True):
        self.dir = directory
        self.enabled = enabled
        if enabled:
            os.makedirs(directory, exist_ok=True)

    def _path(self, key: str) -> str:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.dir, h + ".json")

    def get(self, key: str):
        if not self.enabled:
            return None
        p = self._path(key)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def set(self, key: str, value) -> None:
        if not self.enabled:
            return
        try:
            with open(self._path(key), "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        except Exception:
            pass
