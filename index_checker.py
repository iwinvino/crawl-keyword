"""Check index hàng loạt qua cú pháp site:... — tái dùng SerperProvider sẵn có.

Mỗi dòng input có thể là:
  - domain:     example.com            -> đếm số page Google đã index
  - URL bài:    https://example.com/abc -> kiểm tra URL đó đã được index chưa
  - tự gõ sẵn:  site:example.com        -> tiền tố "site:" sẽ được bỏ tự động
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urlparse

from providers import SerperError


# TLD 2 cấp phổ biến (VN + quốc tế) -> để lấy đúng domain gốc, vd: foo.com.vn
_MULTI_TLD = {
    "com.vn", "net.vn", "org.vn", "edu.vn", "gov.vn", "biz.vn", "info.vn",
    "name.vn", "pro.vn", "health.vn", "int.vn", "ac.vn",
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.jp", "com.au", "com.cn",
    "co.kr", "com.sg", "com.my", "co.th", "com.br", "com.tw",
}


@dataclass
class IndexResult:
    stt: int
    input: str            # dòng gốc người dùng nhập
    loai: str             # "domain" | "url"
    query: str            # truy vấn site:... đã gửi
    indexed: bool         # có index hay không
    so_page: int          # số page index (domain) / 0-1 (url)
    domain_goc: str = ""  # domain gốc (registrable) để gom nhóm theo site
    link_khop: str = ""   # link khớp khi check URL
    loi: str = ""         # thông báo lỗi nếu có


def root_domain(target: str) -> str:
    """Lấy domain gốc từ domain/URL. Xử lý TLD 2 cấp (foo.com.vn -> foo.com.vn)."""
    s = re.sub(r"^\s*site:\s*", "", (target or "").strip(), flags=re.IGNORECASE)
    if not s:
        return ""
    if not re.match(r"^https?://", s, re.IGNORECASE):
        s = "http://" + s
    host = urlparse(s).netloc.lower()
    host = re.sub(r"^www\.", "", host).split(":")[0]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    last3 = ".".join(parts[-3:])
    if last2 in _MULTI_TLD:        # vd: foo.com.vn
        return last3
    return last2                   # vd: sub.foo.com -> foo.com


def parse_line(line: str):
    """Trả về (loai, target). loai='url' nếu có scheme/path, ngược lại 'domain'."""
    s = re.sub(r"^\s*site:\s*", "", line.strip(), flags=re.IGNORECASE).strip()
    if not s:
        return "", ""
    has_scheme = bool(re.match(r"^https?://", s, re.IGNORECASE))
    parsed = urlparse(s if has_scheme else "http://" + s)
    path = parsed.path.strip("/")
    if has_scheme or path:
        return "url", s
    return "domain", parsed.netloc or s


def _norm_link(link: str) -> str:
    """Chuẩn hoá link để so khớp: bỏ scheme/www/slash cuối, host về lowercase."""
    if not link:
        return ""
    if not re.match(r"^https?://", link, re.IGNORECASE):
        link = "http://" + link
    p = urlparse(link)
    host = re.sub(r"^www\.", "", p.netloc.lower())
    return f"{host}{p.path.rstrip('/')}"


def _total_results(data: dict) -> int:
    """Lấy tổng số kết quả từ searchInformation nếu Serper trả về (num<=10)."""
    info = data.get("searchInformation") or {}
    total = info.get("totalResults")
    if total is None:
        return len(data.get("organic") or [])
    try:
        return int(re.sub(r"[^\d]", "", str(total)) or 0)
    except ValueError:
        return len(data.get("organic") or [])


def check_one(stt: int, line: str, provider, pages: int = 10) -> IndexResult:
    loai, target = parse_line(line)
    if not loai:
        return IndexResult(stt, line, "", "", False, 0, loi="dòng rỗng/không hợp lệ")

    dom = root_domain(target)
    query = f"site:{target}"
    try:
        data = provider.web_search(query, num=pages)
    except (SerperError, RuntimeError) as exc:
        return IndexResult(stt, line, loai, query, False, 0,
                           domain_goc=dom, loi=str(exc))

    organic = data.get("organic") or []

    if loai == "url":
        wanted = _norm_link(target)
        matched = next(
            (it.get("link", "") for it in organic
             if _norm_link(it.get("link", "")) == wanted), "")
        if not matched:  # khớp lỏng: link chứa URL gốc
            matched = next(
                (it.get("link", "") for it in organic
                 if wanted and wanted in _norm_link(it.get("link", ""))), "")
        return IndexResult(stt, line, loai, query, bool(matched),
                           1 if matched else 0, domain_goc=dom, link_khop=matched)

    # domain: indexed nếu có organic; số page ước lượng từ totalResults/organic
    return IndexResult(stt, line, loai, query, bool(organic),
                       _total_results(data), domain_goc=dom)


def check_bulk(lines, provider, pages: int = 10, max_workers: int = 4,
               progress=None):
    """Chạy song song. progress(done, total, msg) gọi sau mỗi dòng xong."""
    items = [(i + 1, ln) for i, ln in enumerate(lines) if ln.strip()]
    total = len(items)
    results: list[IndexResult] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        futures = {ex.submit(check_one, stt, ln, provider, pages): (stt, ln)
                   for stt, ln in items}
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if progress:
                progress(done, total, f"Đã check {done}/{total}")
    results.sort(key=lambda r: r.stt)
    return results
