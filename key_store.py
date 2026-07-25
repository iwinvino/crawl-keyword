"""Lưu & quản lý Serper API key lâu dài (file JSON, không commit lên git).

Mỗi key là dict: {label, key, dead, status}.
  - label : nhãn người dùng đặt (vd 'key chính')
  - key   : chuỗi API key
  - dead  : True nếu hết credit/sai key (đánh dấu, KHÔNG tự xóa)
  - status: mô tả lần kiểm tra gần nhất
"""
from __future__ import annotations

import json
import os

import requests

KEY_FILE = "keys.json"
SEARCH_URL = "https://google.serper.dev/search"
DEAD_STATUS = {401, 402, 403}


def _path(directory: str = ".") -> str:
    return os.path.join(directory, KEY_FILE)


def _normalize(e: dict) -> dict:
    return {
        "label": (e.get("label") or "").strip(),
        "key": (e.get("key") or "").strip(),
        "dead": bool(e.get("dead", False)),
        "status": e.get("status") or "chưa test",
    }


def load_keys(directory: str = ".") -> list[dict]:
    p = _path(directory)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [_normalize(e) for e in data if (e.get("key") or "").strip()]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_keys(keys: list[dict], directory: str = ".") -> None:
    try:
        with open(_path(directory), "w", encoding="utf-8") as f:
            json.dump(keys, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def mask(key: str) -> str:
    """Che key để hiển thị an toàn: abcd…wxyz."""
    return (key[:4] + "…" + key[-4:]) if len(key) > 8 else "****"


def test_key(key: str, timeout: int = 15) -> tuple[bool, str, bool]:
    """Gọi 1 request thật để kiểm tra key. Tốn 1 credit.

    Trả về (ok, status_msg, dead).
    """
    key = (key or "").strip()
    if not key:
        return False, "key rỗng", False
    try:
        r = requests.post(
            SEARCH_URL,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": "test", "num": 1}, timeout=timeout)
    except requests.RequestException as e:
        return False, f"lỗi mạng: {e}", False  # không đánh dấu chết khi lỗi mạng

    if r.status_code == 200:
        return True, "🟢 còn sống", False
    if r.status_code in DEAD_STATUS:
        return False, f"hết credit/sai key (HTTP {r.status_code})", True
    if r.status_code == 429:
        return False, "rate-limited (HTTP 429) — thử lại sau", False
    return False, f"HTTP {r.status_code}", False
