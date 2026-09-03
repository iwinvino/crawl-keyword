"""Tìm backlink của 1 domain bằng SERP mining (đa nguồn) + xác minh HTML thật.

Dùng khi Ahrefs/Semrush bị đối thủ chặn bot: không hỏi index backlink của ai cả,
mà tự đi tìm qua SERP rồi tự xác minh.

Luồng 2 TẦNG (SERP chỉ cho *ứng viên*, chưa phải backlink):
  Tầng 1 — SERP : sinh footprint query từ domain đích, hỏi nguồn SERP
                  (Serper / SerpApi / DataForSEO) -> danh sách URL ứng viên.
  Tầng 2 — Xác minh: tải HTML từng ứng viên, dùng `link_extractor` xem có thẻ <a>
                  thật trỏ về domain đích không -> anchor, dofollow/nofollow, vị trí.

Rất nhiều kết quả tầng 1 chỉ *nhắc tên* domain chứ không đặt link — bỏ tầng 2 là
báo số ảo, nên tầng 2 mặc định luôn bật.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

import link_extractor
import url_checker
from link_extractor import build_declared, norm_domain, root_domain
from providers import SerperError, SerperProvider

# Đuôi file không bao giờ là "trang đặt backlink" -> loại khỏi ứng viên
_ASSET_RE = re.compile(
    r"\.(?:jpg|jpeg|png|gif|webp|svg|ico|css|js|mp4|mp3|zip|rar|exe|apk)(?:$|\?)", re.I)


# ==================================================================
# 1) FOOTPRINT — sinh query tìm ứng viên
# ==================================================================
# key -> (nhãn hiển thị, template). {d} = domain đích.
# Mọi template đều kèm -site:{d} để loại trang của chính đối thủ,
# trừ nhóm "social" (không thể vừa site: vừa -site: trong 1 query Google).
FOOTPRINTS = {
    "domain": ("Nhắc domain", '"{d}" -site:{d}'),
    "intext": ("Trong nội dung (intext)", 'intext:"{d}" -site:{d}'),
    "url": ("Viết dạng URL đầy đủ", '"https://{d}" -site:{d}'),
    "blog": ("Trang blog / tin tức", '"{d}" (inurl:blog OR inurl:tin-tuc OR inurl:news) -site:{d}'),
    "forum": ("Diễn đàn", '"{d}" (inurl:forum OR inurl:thread OR inurl:showthread OR inurl:viewtopic) -site:{d}'),
    "profile": ("Trang hồ sơ / thành viên", '"{d}" (inurl:profile OR inurl:member OR inurl:user OR inurl:author) -site:{d}'),
    "guest": ("Guest post", '"{d}" ("guest post" OR "bài viết của" OR "đăng bởi" OR "written by") -site:{d}'),
    "comment": ("Bình luận", '"{d}" (inurl:comment OR "leave a reply" OR "bình luận") -site:{d}'),
    "directory": ("Directory / listing", '"{d}" (inurl:directory OR inurl:listing OR "add your site" OR "submit url") -site:{d}'),
    "social": ("Blog 2.0 / social", '"{d}" (site:medium.com OR site:tumblr.com OR site:wordpress.com OR site:blogspot.com OR site:livejournal.com OR site:weebly.com)'),
    "pdf": ("File PDF/DOC", '"{d}" (filetype:pdf OR filetype:doc OR filetype:docx)'),
}

# Nhóm bật sẵn: rẻ và độ phủ cao nhất
DEFAULT_FOOTPRINTS = ["domain", "intext", "url", "blog", "forum"]


@dataclass
class Query:
    q: str
    label: str              # nhóm footprint / brand / trang con
    gl: str = ""            # quốc gia SERP
    hl: str = ""            # ngôn ngữ SERP

    @property
    def locale(self) -> str:
        return f"{self.gl}/{self.hl}" if self.gl or self.hl else "-"


def parse_locales(raw: str, fallback_gl: str = "vn", fallback_hl: str = "vi") -> list[tuple]:
    """'vn:vi, us:en' -> [('vn','vi'), ('us','en')]. Rỗng -> dùng fallback.

    Cùng 1 query đổi quốc gia sẽ ra tập kết quả khác nhau -> tăng độ phủ.
    """
    out = []
    for part in re.split(r"[,\n;]+", raw or ""):
        part = part.strip()
        if not part:
            continue
        gl, _, hl = part.partition(":")
        gl = gl.strip().lower()
        hl = (hl.strip().lower() or gl)
        if gl:
            out.append((gl, hl))
    return out or [(fallback_gl, fallback_hl)]


# Serper tài khoản FREE trả HTTP 400 "Query pattern not allowed for free accounts"
# khi query có dấu ngoặc kép hoặc intext: (đã kiểm chứng 2026-07-28).
# Các toán tử -site: / inurl: / filetype: / site: / OR thì vẫn chạy bình thường.
_FREE_BLOCKED_RE = re.compile(r"not allowed for free", re.I)
_INTEXT_RE = re.compile(r"\bintext:\s*", re.I)


def sanitize_query(q: str) -> str:
    """Bỏ dấu ngoặc kép + intext: để chạy được trên Serper free.

    Mất khả năng khớp cụm chính xác (Google chuyển sang AND các từ) nên độ chính
    xác giảm nhẹ, đổi lại query không bị chặn.
    """
    q = _INTEXT_RE.sub("", q or "")
    return re.sub(r"\s+", " ", q.replace('"', "")).strip()


def build_queries(target: str, footprint_keys=(), brands=(), paths=(),
                  custom=(), locales=(("vn", "vi"),), no_quotes: bool = False) -> list[Query]:
    """Sinh toàn bộ query tìm ứng viên cho 1 domain đích.

    brands    : tên thương hiệu (bắt được link anchor brand không viết ra domain)
    paths     : đường dẫn trang con quan trọng ('/khuyen-mai') -> tìm deep link
    custom    : query người dùng tự gõ, có thể chứa {d} để thay bằng domain
    no_quotes : bật cho tài khoản Serper free — bỏ ngoặc kép/intext: rồi loại
                các query trùng nhau sau khi bỏ (tránh đốt credit 2 lần).
    """
    d = norm_domain(target)
    if not d:
        return []

    base: list[tuple] = []
    for key in footprint_keys:
        tpl = FOOTPRINTS.get(key)
        if tpl:
            base.append((tpl[0], tpl[1].format(d=d)))

    for b in brands:
        b = str(b).strip()
        if b:
            base.append((f"Brand: {b}", f'"{b}" -site:{d}'))

    for p in paths:
        p = str(p).strip()
        if not p:
            continue
        p = p if p.startswith("/") else "/" + p
        base.append((f"Trang con: {p}", f'"{d}{p}" -site:{d}'))

    for c in custom:
        c = str(c).strip()
        if c:
            base.append(("Tự nhập", c.replace("{d}", d)))

    out, seen = [], {}
    for label, q in base:
        if no_quotes:
            q = sanitize_query(q)
        if not q:
            continue
        for gl, hl in locales:
            key = (q, gl, hl)
            if key in seen:                 # sau khi bỏ ngoặc kép có thể trùng nhau
                prev = seen[key]
                if label not in prev.label:
                    prev.label = f"{prev.label} + {label}"
                continue
            qo = Query(q=q, label=label, gl=gl, hl=hl)
            seen[key] = qo
            out.append(qo)
    return out


def discover_paths(source: "SerpSource", target: str, limit: int = 10) -> list[str]:
    """Lấy các trang con đang index của domain đích (site:) để làm footprint deep link."""
    d = norm_domain(target)
    if not d or limit <= 0:
        return []
    hits = source.search(f"site:{d}", limit)
    paths, seen = [], set()
    for h in hits:
        path = urlparse(h.get("url") or "").path or "/"
        path = path.rstrip("/")
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
        if len(paths) >= limit:
            break
    return paths


# ==================================================================
# 2) NGUỒN SERP — đa nguồn, cùng 1 interface
# ==================================================================
class SerpSource(ABC):
    """Giao diện chung: thêm nguồn mới = 1 class + 1 nhánh trong build_source()."""

    name = "serp"

    @abstractmethod
    def search(self, query: str, num: int, gl: str = "", hl: str = "") -> list[dict]:
        """Trả list dict {url, title, snippet}. Ném Exception nếu lỗi."""

    def credits(self) -> int:
        return 0

    def stats(self) -> list:
        return []


class SerperSource(SerpSource):
    """Serper (Google) — dùng lại SerperProvider: xoay nhiều key, cache, phân trang."""

    name = "Serper"

    def __init__(self, api_keys, gl="vn", hl="vi", timeout=20, cache=None):
        self.gl, self.hl = gl, hl
        self.timeout, self.cache = timeout, cache
        self._keys = list(api_keys)
        self._providers: dict[tuple, SerperProvider] = {}

    def _provider(self, gl: str, hl: str) -> SerperProvider:
        """1 provider cho mỗi locale (gl/hl gắn vào provider), dùng chung key + cache."""
        key = (gl or self.gl, hl or self.hl)
        p = self._providers.get(key)
        if p is None:
            p = SerperProvider(self._keys, key[0], key[1], self.timeout, self.cache)
            self._providers[key] = p
        return p

    def search(self, query: str, num: int, gl: str = "", hl: str = "") -> list[dict]:
        data = self._provider(gl, hl).web_search(query, num)
        return [{"url": o.get("link", ""), "title": o.get("title", ""),
                 "snippet": o.get("snippet", "")}
                for o in (data.get("organic") or []) if o.get("link")]

    def credits(self) -> int:
        return sum(p.credits_used for p in self._providers.values())

    def stats(self) -> list:
        """Gộp thống kê key từ mọi provider locale (mỗi key chỉ hiện 1 dòng)."""
        merged: dict[str, dict] = {}
        for p in self._providers.values():
            for row in p.stats_summary():
                cur = merged.get(row["key"])
                if cur is None:
                    merged[row["key"]] = dict(row)
                else:
                    cur["used"] += row["used"]
                    cur["dead"] = cur["dead"] or row["dead"]
                    if row["dead"]:
                        cur["status"] = row["status"]
        return list(merged.values())

    @property
    def key_state(self) -> dict:
        """Cho app.sync_dead_from_provider() đánh dấu key chết sau khi chạy."""
        out: dict = {}
        for p in self._providers.values():
            for k, s in p.key_state.items():
                cur = out.get(k)
                if cur is None or (s.get("dead") and not cur.get("dead")):
                    out[k] = s
        return out


class SerpApiSource(SerpSource):
    """SerpApi — dự phòng khi Serper hết credit; hỗ trợ cả engine Bing."""

    name = "SerpApi"
    URL = "https://serpapi.com/search.json"

    def __init__(self, api_key: str, gl="vn", hl="vi", timeout=30, cache=None,
                 engine="google"):
        self.api_key = (api_key or "").strip()
        self.gl, self.hl = gl, hl
        self.timeout, self.cache = timeout, cache
        self.engine = engine
        self.used = 0

    def search(self, query: str, num: int, gl: str = "", hl: str = "") -> list[dict]:
        if not self.api_key:
            raise RuntimeError("Chưa nhập SerpApi key.")
        params = {"engine": self.engine, "q": query, "num": min(num, 100),
                  "gl": gl or self.gl, "hl": hl or self.hl, "api_key": self.api_key}
        ck = f"serpapi|{self.engine}|{query}|{num}|{params['gl']}|{params['hl']}"
        if self.cache:
            hit = self.cache.get(ck)
            if hit is not None:
                return hit
        r = requests.get(self.URL, params=params, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"SerpApi HTTP {r.status_code}: {r.text[:160]}")
        data = r.json()
        if data.get("error"):
            raise RuntimeError(f"SerpApi: {data['error']}")
        self.used += 1
        rows = [{"url": o.get("link", ""), "title": o.get("title", ""),
                 "snippet": o.get("snippet", "")}
                for o in (data.get("organic_results") or []) if o.get("link")]
        if self.cache:
            self.cache.set(ck, rows)
        return rows

    def credits(self) -> int:
        return self.used


class DataForSeoSource(SerpSource):
    """DataForSEO SERP live — rẻ theo request, index riêng."""

    name = "DataForSEO"
    URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"

    def __init__(self, login: str, password: str, location="Vietnam", language="vi",
                 timeout=60, cache=None):
        self.login = (login or "").strip()
        self.password = (password or "").strip()
        self.location, self.language = location, language
        self.timeout, self.cache = timeout, cache
        self.used = 0

    def search(self, query: str, num: int, gl: str = "", hl: str = "") -> list[dict]:
        if not (self.login and self.password):
            raise RuntimeError("Chưa nhập login/password DataForSEO.")
        payload = [{"keyword": query, "location_name": self.location,
                    "language_code": hl or self.language, "depth": min(max(num, 10), 100)}]
        ck = f"dfs|{query}|{num}|{self.location}|{payload[0]['language_code']}"
        if self.cache:
            hit = self.cache.get(ck)
            if hit is not None:
                return hit
        r = requests.post(self.URL, auth=(self.login, self.password),
                          json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"DataForSEO HTTP {r.status_code}: {r.text[:160]}")
        data = r.json()
        tasks = data.get("tasks") or []
        if not tasks:
            raise RuntimeError(f"DataForSEO: {data.get('status_message', 'không có task')}")
        task = tasks[0]
        if task.get("status_code") != 20000:
            raise RuntimeError(f"DataForSEO: {task.get('status_message')}")
        self.used += 1
        rows = []
        for res in task.get("result") or []:
            for item in res.get("items") or []:
                if item.get("type") == "organic" and item.get("url"):
                    rows.append({"url": item["url"], "title": item.get("title", ""),
                                 "snippet": item.get("description", "")})
        if self.cache:
            self.cache.set(ck, rows)
        return rows

    def credits(self) -> int:
        return self.used


class AggregateSource(SerpSource):
    """Gộp nhiều nguồn: cộng dồn + loại trùng. Chỉ dùng khi 1 nguồn không đủ phủ."""

    name = "Gộp"

    def __init__(self, sources: list):
        self.sources = [s for s in sources if s]
        self.name = "Gộp: " + " + ".join(s.name for s in self.sources)
        self.errors: list[str] = []

    def search(self, query: str, num: int, gl: str = "", hl: str = "") -> list[dict]:
        merged, seen, fails = [], set(), 0
        for s in self.sources:
            try:
                rows = s.search(query, num, gl, hl)
            except Exception as e:
                fails += 1
                self.errors.append(f"{s.name}: {e}")
                continue
            for row in rows:
                k = link_extractor._norm(row["url"])
                if k and k not in seen:
                    seen.add(k)
                    merged.append(row)
        if fails == len(self.sources) and self.sources:
            raise RuntimeError("Mọi nguồn đều lỗi: " + "; ".join(self.errors[-len(self.sources):]))
        return merged

    def credits(self) -> int:
        return sum(s.credits() for s in self.sources)

    def stats(self) -> list:
        out = []
        for s in self.sources:
            out.extend(s.stats())
        return out


def build_source(kind: str, *, serper_keys=(), serpapi_key="", dfs_login="",
                 dfs_password="", dfs_location="Vietnam", gl="vn", hl="vi",
                 timeout=20, cache=None, serpapi_engine="google") -> SerpSource:
    """Factory 1 nguồn. Thêm nguồn mới = thêm 1 nhánh ở đây."""
    kind = (kind or "").lower()
    if kind == "serper":
        return SerperSource(serper_keys, gl, hl, timeout, cache)
    if kind == "serpapi":
        return SerpApiSource(serpapi_key, gl, hl, max(timeout, 30), cache, serpapi_engine)
    if kind == "dataforseo":
        return DataForSeoSource(dfs_login, dfs_password, dfs_location, hl,
                                max(timeout, 60), cache)
    raise ValueError(f"Nguồn SERP không hỗ trợ: {kind}")


# ==================================================================
# 3) TẦNG 1 — thu ứng viên từ SERP
# ==================================================================
@dataclass
class Candidate:
    url: str
    domain: str                                  # root domain trang nguồn
    title: str = ""
    snippet: str = ""
    queries: set = field(default_factory=set)    # nhãn footprint đã tìm ra nó
    sources: set = field(default_factory=set)    # nguồn SERP đã trả về nó
    hits: int = 0                                # số lần xuất hiện (mọi query)


def collect_candidates(source: SerpSource, queries: list[Query], target: str,
                       num: int = 20, workers: int = 4, exclude_domains=(),
                       progress=None) -> tuple[list[Candidate], list[str]]:
    """Chạy toàn bộ query, gom URL ứng viên (đã dedupe + loại domain đích)."""
    tdomain = norm_domain(target)
    skip_roots = {tdomain} | {norm_domain(d) for d in exclude_domains if str(d).strip()}
    skip_roots.discard("")

    found: dict[str, Candidate] = {}
    errors: list[str] = []
    done = 0
    total = max(1, len(queries))

    def run(qo: Query):
        """Chạy 1 query; nếu nguồn chặn cú pháp (Serper free) thì tự bỏ ngoặc kép
        rồi thử lại 1 lần thay vì mất trắng query đó."""
        try:
            return qo, source.search(qo.q, num, qo.gl, qo.hl), ""
        except Exception as e:
            plain = sanitize_query(qo.q)
            if not _FREE_BLOCKED_RE.search(str(e)) or plain == qo.q:
                raise
            rows = source.search(plain, num, qo.gl, qo.hl)
            return qo, rows, (f"Query bị chặn cú pháp, đã tự bỏ ngoặc kép và chạy lại: "
                              f"{plain}")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(run, q) for q in queries]
        for fut in as_completed(futs):
            done += 1
            try:
                qo, rows, warn = fut.result()
            except (SerperError, Exception) as e:      # 1 query lỗi không dừng cả job
                errors.append(str(e))
                if progress:
                    progress(f"⚠️ Lỗi query: {e}", done / total * 0.55)
                continue
            if warn:
                errors.append(warn)
            for row in rows:
                url = (row.get("url") or "").strip()
                if not url or _ASSET_RE.search(url):
                    continue
                host = (urlparse(url).hostname or "").lower()
                root = root_domain(host)
                if not root or root in skip_roots:
                    continue
                key = link_extractor._norm(url)
                c = found.get(key)
                if c is None:
                    c = Candidate(url=url, domain=root, title=row.get("title", ""),
                                  snippet=row.get("snippet", ""))
                    found[key] = c
                c.queries.add(qo.label)
                c.sources.add(source.name)
                c.hits += 1
            if progress:
                progress(f"SERP {done}/{total} query · {len(found)} ứng viên",
                         done / total * 0.55)

    # Nhiều query cùng trỏ tới 1 trang -> khả năng có link thật cao hơn, xác minh trước
    cands = sorted(found.values(), key=lambda c: (-len(c.queries), -c.hits, c.domain))
    return cands, errors


# ==================================================================
# 4) TẦNG 2 — xác minh có thẻ <a> thật trỏ về domain đích
# ==================================================================
@dataclass
class Backlink:
    source_url: str        # trang đặt link
    source_domain: str     # root domain trang đặt link
    target_url: str        # URL đích thật sự được trỏ tới
    anchor: str
    follow: str            # dofollow / nofollow / ✖ không tính
    zone: str              # nội dung / footer / menu / bio...
    kind: str              # thẻ a / text / trong JSON-JS...
    count: int             # số lần link lặp trên trang
    via: str = ""          # URL trung gian (link redirect) nếu có
    found_by: str = ""     # footprint nào tìm ra trang này
    page_nofollow: bool = False


@dataclass
class VerifyResult:
    candidate: Candidate
    status: str            # "có link" | "không có link" | "chỉ text/JS" | "chết" | "lỗi"
    http_code: int = 0
    note: str = ""
    backlinks: list = field(default_factory=list)


def verify_candidates(cands: list[Candidate], target: str, timeout: int = 20,
                      anti_bot: bool = True, workers: int = 6, retries: int = 1,
                      max_links: int = link_extractor.MAX_LINKS_PER_PAGE,
                      progress=None) -> list[VerifyResult]:
    """Tải HTML từng ứng viên, tìm thẻ <a> trỏ về domain đích.

    Dùng lại `url_checker.check_one` (đa engine + anti-bot + theo redirect) nên
    trang chặn bot vẫn có cơ hội đọc được.
    """
    tdomain = norm_domain(target)
    declared = build_declared(my_domains=[tdomain])
    out: list[VerifyResult] = []
    done = 0
    total = max(1, len(cands))

    def run(i: int, c: Candidate):
        res = url_checker.check_one(
            i, c.url, timeout=timeout, anti_bot=anti_bot, follow=True,
            soft404=False, retries=retries, scan_links=True,
            max_links=max_links, declared=declared, skip_services=False)
        return c, res

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(run, i, c) for i, c in enumerate(cands, 1)]
        for fut in as_completed(futs):
            done += 1
            try:
                c, res = fut.result()
            except Exception as e:
                out.append(VerifyResult(candidate=Candidate("", ""), status="lỗi",
                                        note=str(e)))
                continue

            found_by = ", ".join(sorted(c.queries))
            mine = [r for r in (res.outbound or []) if r.get("mine")]
            real = [r for r in mine if r["loai"] == "ra ngoài"]
            links = [
                Backlink(source_url=res.final_url or res.url, source_domain=c.domain,
                         target_url=r["url"], anchor=r.get("anchor") or "[trống]",
                         follow=r.get("follow", ""), zone=r.get("zone", ""),
                         kind=r.get("kind", ""), count=r.get("count", 1),
                         via=r.get("via", ""), found_by=found_by,
                         page_nofollow=res.page_nofollow)
                for r in mine
            ]

            if not res.alive and not real:
                status = "chết" if res.category in ("chết", "lỗi") else res.category
            elif real:
                status = "có link"
            elif mine:
                status = "chỉ text/JS"
            else:
                status = "không có link"

            out.append(VerifyResult(candidate=c, status=status,
                                    http_code=res.status_code,
                                    note=res.my_links_note or res.note,
                                    backlinks=links))
            if progress:
                progress(f"Xác minh {done}/{total} trang", 0.55 + done / total * 0.45)

    return out


# ==================================================================
# 5) Dựng bảng hiển thị / xuất Excel
# ==================================================================
_STATUS_ICON = {"có link": "✅ có link", "không có link": "❌ không có link",
                "chỉ text/JS": "⚠️ chỉ text/JS", "chết": "💀 trang chết",
                "lỗi": "⚠️ lỗi", "chặn": "🔒 chặn bot", "bỏ qua": "⏭️ bỏ qua"}


def backlink_rows(results: list[VerifyResult]) -> list[dict]:
    """Bảng backlink đã xác minh (mỗi dòng 1 link thật)."""
    rows, i = [], 0
    for r in results:
        for b in r.backlinks:
            i += 1
            rows.append({
                "STT": i,
                "Domain nguồn": b.source_domain,
                "Trang đặt link": b.source_url,
                "Link đích": b.target_url,
                "Anchor": b.anchor,
                "Follow": b.follow,
                "Vị trí": b.zone,
                "Kiểu": b.kind,
                "Số lần": b.count,
                "Qua link trung gian": b.via,
                "Trang nofollow toàn cục": "có" if b.page_nofollow else "",
                "Tìm ra bởi": b.found_by,
            })
    return rows


def candidate_rows(results: list[VerifyResult]) -> list[dict]:
    """Bảng mọi ứng viên SERP + kết quả xác minh (kể cả trang không có link)."""
    rows = []
    for i, r in enumerate(results, 1):
        c = r.candidate
        rows.append({
            "STT": i,
            "Domain nguồn": c.domain,
            "URL ứng viên": c.url,
            "Kết quả": _STATUS_ICON.get(r.status, r.status),
            "Số link về đích": len(r.backlinks),
            "Mã HTTP": r.http_code,
            "Tiêu đề": c.title,
            "Tìm ra bởi": ", ".join(sorted(c.queries)),
            "Ghi chú": r.note,
        })
    return rows


def domain_rows(results: list[VerifyResult]) -> list[dict]:
    """Gom theo referring domain — chỉ số quan trọng nhất của 1 hồ sơ backlink."""
    agg: dict[str, dict] = {}
    for r in results:
        for b in r.backlinks:
            if b.follow == "✖ không tính":
                continue
            a = agg.setdefault(b.source_domain, {
                "Domain nguồn": b.source_domain, "Số backlink": 0,
                "Số trang đặt link": set(), "Dofollow": 0, "Nofollow": 0,
                "Anchor tiêu biểu": ""})
            a["Số backlink"] += 1
            a["Số trang đặt link"].add(b.source_url)
            if b.follow == "dofollow":
                a["Dofollow"] += 1
            else:
                a["Nofollow"] += 1
            if not a["Anchor tiêu biểu"] and b.anchor not in ("[trống]", "—", ""):
                a["Anchor tiêu biểu"] = b.anchor
    out = []
    for a in agg.values():
        a["Số trang đặt link"] = len(a["Số trang đặt link"])
        out.append(a)
    return sorted(out, key=lambda x: (-x["Dofollow"], -x["Số backlink"]))


def summarize(results: list[VerifyResult], queries_run: int = 0,
              credits_used: int = 0) -> dict:
    """Chỉ số tổng quan để hiện metric + sheet Tổng quan."""
    links = [b for r in results for b in r.backlinks if b.follow != "✖ không tính"]
    return {
        "queries": queries_run,
        "candidates": len(results),
        "pages_with_link": sum(1 for r in results if r.status == "có link"),
        "backlinks": len(links),
        "ref_domains": len({b.source_domain for b in links}),
        "dofollow": sum(1 for b in links if b.follow == "dofollow"),
        "nofollow": sum(1 for b in links if b.follow != "dofollow"),
        "text_only": sum(1 for r in results if r.status == "chỉ text/JS"),
        "dead": sum(1 for r in results if r.status in ("chết", "lỗi")),
        "credits": credits_used,
    }
