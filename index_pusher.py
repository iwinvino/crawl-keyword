"""Đẩy (submit) URL vào Google qua Indexing API, xoay vòng nhiều Service Account.

Mỗi Service Account (SA) = 1 project Google Cloud = quota mặc định 200 URL/ngày.
Muốn đẩy số lượng lớn -> thêm nhiều SA, tool tự xoay như xoay Serper key.

Điều kiện để 1 SA đẩy được 1 URL:
  - SA đã bật Indexing API trong Google Cloud.
  - SA (client_email) được thêm làm **Owner** của domain trong Google Search Console.

Lưu SA trong service_accounts.json (đã gitignore) — chứa private key, KHÔNG chia sẻ.
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account

SA_FILE = "service_accounts.json"
ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"
SCOPES = ["https://www.googleapis.com/auth/indexing"]
DAILY_QUOTA = 200  # quota publish mặc định của Google / project / ngày


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------- Lưu / quản lý Service Account ----------------
def _path(directory: str = ".") -> str:
    return os.path.join(directory, SA_FILE)


def _normalize(a: dict) -> dict:
    info = a.get("info") or {}
    return {
        "label": (a.get("label") or "").strip(),
        "email": a.get("email") or info.get("client_email", ""),
        "project": a.get("project") or info.get("project_id", ""),
        "info": info,
        "used_today": int(a.get("used_today", 0)),
        "date": a.get("date") or _today(),
        "dead": bool(a.get("dead", False)),
        "status": a.get("status") or "chưa dùng",
    }


def load_accounts(directory: str = ".") -> list[dict]:
    p = _path(directory)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [_normalize(a) for a in data if (a.get("info") or {}).get("client_email")]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_accounts(accounts: list[dict], directory: str = ".") -> None:
    try:
        with open(_path(directory), "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def parse_account(raw, label: str = "") -> dict:
    """Tạo account dict từ nội dung JSON của Service Account (str/bytes/dict)."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    info = raw if isinstance(raw, dict) else json.loads(raw)
    if info.get("type") != "service_account" or not info.get("client_email"):
        raise ValueError("Không phải file Service Account hợp lệ (thiếu client_email).")
    return _normalize({"label": label, "info": info})


def remaining_quota(accounts: list[dict]) -> int:
    """Tổng URL còn có thể đẩy hôm nay trên các account còn sống."""
    today = _today()
    total = 0
    for a in accounts:
        if a["dead"]:
            continue
        used = a["used_today"] if a["date"] == today else 0
        total += max(0, DAILY_QUOTA - used)
    return total


# ---------------- Bộ đẩy index có xoay vòng ----------------
class IndexPusher:
    def __init__(self, accounts: list[dict], notif_type: str = "URL_UPDATED",
                 timeout: int = 30):
        self.accounts = accounts        # mutate tại chỗ để cập nhật used_today
        self.notif_type = notif_type
        self.timeout = timeout
        self.today = _today()
        self._tokens: dict[str, str] = {}
        self._lock = threading.Lock()
        self._rr = 0
        for a in accounts:              # reset quota nếu sang ngày mới
            if a["date"] != self.today:
                a["date"] = self.today
                a["used_today"] = 0

    def _pick(self):
        """Account kế tiếp còn sống & còn quota (round-robin). None nếu hết."""
        n = len(self.accounts)
        for _ in range(n):
            i = self._rr % n
            self._rr += 1
            a = self.accounts[i]
            if not a["dead"] and a["used_today"] < DAILY_QUOTA:
                return a
        return None

    def _token(self, a: dict) -> str:
        tok = self._tokens.get(a["email"])
        if tok:
            return tok
        creds = service_account.Credentials.from_service_account_info(
            a["info"], scopes=SCOPES)
        creds.refresh(GoogleRequest())
        self._tokens[a["email"]] = creds.token
        return creds.token

    @staticmethod
    def _err_msg(resp) -> str:
        try:
            return resp.json().get("error", {}).get("message", resp.text[:200])
        except ValueError:
            return resp.text[:200]

    def push(self, url: str) -> dict:
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            return {"url": url, "ok": False, "account": "", "code": 0,
                    "msg": "URL phải bắt đầu bằng http:// hoặc https://"}

        while True:
            with self._lock:
                a = self._pick()
            if a is None:
                return {"url": url, "ok": False, "account": "", "code": 0,
                        "msg": "Hết quota tất cả account (200/ngày/account) — thêm SA hoặc chờ mai"}

            try:
                token = self._token(a)
            except Exception as e:  # lỗi credential/refresh -> đánh dấu SA chết
                with self._lock:
                    a["dead"] = True
                    a["status"] = f"lỗi xác thực: {e}"[:120]
                continue

            try:
                r = requests.post(
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                    json={"url": url, "type": self.notif_type}, timeout=self.timeout)
            except requests.RequestException as e:
                return {"url": url, "ok": False, "account": a["label"] or a["email"],
                        "code": 0, "msg": f"lỗi mạng: {e}"}

            code = r.status_code
            name = a["label"] or a["email"]

            if code == 200:
                with self._lock:
                    a["used_today"] += 1
                    a["status"] = f"đã dùng {a['used_today']}/{DAILY_QUOTA} hôm nay"
                return {"url": url, "ok": True, "account": name, "code": 200,
                        "msg": "đã gửi"}

            if code == 429:  # hết quota/rate limit -> khoá SA hôm nay, xoay tiếp
                with self._lock:
                    a["used_today"] = DAILY_QUOTA
                    a["status"] = "hết quota hôm nay (HTTP 429)"
                continue

            if code == 401:  # credential sai/thu hồi -> SA chết
                with self._lock:
                    a["dead"] = True
                    a["status"] = "credential lỗi (HTTP 401)"
                continue

            if code == 403:  # SA chưa là owner domain này trong Search Console
                return {"url": url, "ok": False, "account": name, "code": 403,
                        "msg": "403: SA chưa là Owner của domain trong Search Console"}

            return {"url": url, "ok": False, "account": name, "code": code,
                    "msg": f"HTTP {code}: {self._err_msg(r)}"}

    def push_bulk(self, urls, max_workers: int = 4, progress=None) -> list[dict]:
        items = [u.strip() for u in urls if u.strip()]
        order = {u: i for i, u in enumerate(items)}
        results, done, total = [], 0, len(items)
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
            futs = [ex.submit(self.push, u) for u in items]
            for fut in as_completed(futs):
                results.append(fut.result())
                done += 1
                if progress:
                    progress(done, total, f"Đã đẩy {done}/{total}")
        results.sort(key=lambda r: order.get(r["url"], 0))
        return results
