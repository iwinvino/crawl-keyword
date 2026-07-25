"""Adapter cho nhà cung cấp search. Hiện có Serper, dễ thêm provider khác."""
import json
import threading
import time
from abc import ABC, abstractmethod

import requests


class SerperError(Exception):
    """Lỗi request/tài khoản (KHÔNG phải key hỏng) -> không xoay key, bỏ qua request."""


class SearchProvider(ABC):
    """Giao diện chung — đổi provider không cần sửa pipeline."""

    @abstractmethod
    def image_search(self, query: str, num: int) -> dict:
        ...

    @abstractmethod
    def web_search(self, query: str, num: int) -> dict:
        ...


class SerperProvider(SearchProvider):
    """Serper với nhiều API key + tự xoay (auto-roll) khi key lỗi/hết credit."""

    IMAGES_URL = "https://google.serper.dev/images"
    SEARCH_URL = "https://google.serper.dev/search"
    # Status code = key chết (sai key / hết credit) -> đánh dấu để user xóa
    DEAD_STATUS = {401, 402, 403}
    # Status code = bị rate limit tạm thời -> nghỉ rồi xoay sang key khác
    COOLDOWN_STATUS = {429}
    # Free account giới hạn /search num <= 10 -> phân trang để lấy thêm
    SEARCH_PER_PAGE = 10
    COOLDOWN_SECONDS = 1.0

    def __init__(self, api_keys, gl="us", hl="en", timeout=20, cache=None):
        if isinstance(api_keys, str):
            api_keys = [api_keys]
        self.api_keys = [k.strip() for k in api_keys if k and k.strip()]
        self.gl = gl
        self.hl = hl
        self.timeout = timeout
        self.cache = cache
        self.credits_used = 0   # tổng request thật (không tính cache)
        self._rr = 0            # con trỏ xoay vòng (round-robin)
        self.key_state = {
            k: {"used": 0, "status": "ok", "dead": False} for k in self.api_keys}
        self._lock = threading.Lock()

    def _next_live_index(self):
        """Index key 'sống' kế tiếp theo VÒNG TRÒN, hoặc None nếu tất cả đã chết.
        (Gọi trong _lock.) Hết key cuối thì quay lại key đầu."""
        n = len(self.api_keys)
        for _ in range(n):
            i = self._rr % n
            self._rr += 1
            if not self.key_state[self.api_keys[i]]["dead"]:
                return i
        return None

    def _post(self, url: str, payload: dict) -> dict:
        if not str(payload.get("q") or "").strip():
            raise SerperError("Truy vấn rỗng (q).")

        key_cache = url + "|" + json.dumps(payload, sort_keys=True)
        if self.cache:
            cached = self.cache.get(key_cache)
            if cached is not None:
                return cached

        if not self.api_keys:
            raise RuntimeError("Chưa cấu hình Serper API key.")

        last_err = None
        attempts = 0
        max_attempts = len(self.api_keys) * 4 + 4
        while attempts < max_attempts:
            attempts += 1
            with self._lock:
                idx = self._next_live_index()
            if idx is None:
                raise RuntimeError(
                    "Tất cả Serper key đã hết credit/lỗi — hãy thay key mới. "
                    f"Lỗi cuối: {last_err}")
            key = self.api_keys[idx]

            headers = {"X-API-KEY": key, "Content-Type": "application/json"}
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                # Lỗi mạng -> KHÔNG đốt key, bỏ qua request này
                raise SerperError(f"Lỗi mạng: {e}")

            code = resp.status_code
            if code == 200:
                data = resp.json()
                with self._lock:
                    self.credits_used += 1
                    st = self.key_state[key]
                    st["used"] += 1
                    st["status"] = "ok"
                if self.cache:
                    self.cache.set(key_cache, data)
                return data

            if code in self.DEAD_STATUS:
                # Sai key / hết credit -> đánh dấu chết, xoay sang key khác
                last_err = f"key #{idx + 1}: HTTP {code}"
                with self._lock:
                    self.key_state[key]["dead"] = True
                    self.key_state[key]["status"] = f"hết credit/lỗi (HTTP {code})"
                continue

            if code in self.COOLDOWN_STATUS:
                # Rate limit -> nghỉ ngắn rồi xoay sang key khác (vòng tròn)
                last_err = f"key #{idx + 1}: HTTP 429 (rate limit)"
                with self._lock:
                    self.key_state[key]["status"] = "rate-limited"
                time.sleep(self.COOLDOWN_SECONDS)
                continue

            # 400 v.v. -> lỗi request/tài khoản, KHÔNG xoay key
            try:
                msg = resp.json().get("message", resp.text[:200])
            except Exception:
                msg = resp.text[:200]
            raise SerperError(f"HTTP {code}: {msg}")

        raise RuntimeError(
            f"Serper: thử {max_attempts} lần vẫn bị giới hạn/lỗi. Lỗi cuối: {last_err}")

    def stats_summary(self):
        """Tóm tắt từng key (đã che) để hiển thị; dead=True -> nên xóa."""
        out = []
        for i, k in enumerate(self.api_keys):
            s = self.key_state[k]
            masked = (k[:4] + "…" + k[-4:]) if len(k) > 8 else "****"
            out.append({
                "key": f"#{i + 1} {masked}",
                "used": s["used"],
                "status": s["status"],
                "dead": s["dead"],
            })
        return out

    def image_search(self, query: str, num: int = 20) -> dict:
        # /images không bị giới hạn num trên free account
        payload = {"q": query, "num": num, "gl": self.gl, "hl": self.hl}
        return self._post(self.IMAGES_URL, payload)

    def web_search(self, query: str, num: int = 10) -> dict:
        # /search free account giới hạn num <= 10 -> phân trang để lấy thêm
        per_page = self.SEARCH_PER_PAGE
        if num <= per_page:
            return self._post(self.SEARCH_URL,
                              {"q": query, "num": num, "gl": self.gl, "hl": self.hl})

        merged = []
        page = 1
        max_pages = (num + per_page - 1) // per_page
        while len(merged) < num and page <= max_pages:
            try:
                data = self._post(self.SEARCH_URL, {
                    "q": query, "num": per_page, "page": page,
                    "gl": self.gl, "hl": self.hl})
            except SerperError:
                if merged:      # đã có kết quả -> giữ lại, dừng phân trang
                    break
                raise
            org = data.get("organic", [])
            if not org:
                break
            merged.extend(org)
            if len(org) < per_page:
                break
            page += 1
        return {"organic": merged[:num]}
