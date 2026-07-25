"""Trích xuất & tổng hợp keyword từ SERP và từ HTML trang."""
import re
from collections import defaultdict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from models import ImageHit, KeywordRecord, PageHit

try:
    import yake
    _YAKE_AVAILABLE = True
except Exception:
    _YAKE_AVAILABLE = False

try:
    from curl_cffi import requests as cffi_requests
    _CFFI = True
except Exception:
    _CFFI = False

try:
    import cloudscraper
    _CLOUDSCRAPER = True
except Exception:
    _CLOUDSCRAPER = False

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}


def fetch_html(url: str, timeout: int = 20, anti_bot: bool = True):
    """Lấy HTML, cố vượt anti-bot. Trả về (html, error).

    Thứ tự khi anti_bot=True: curl_cffi (giả lập TLS Chrome, vượt Cloudflare) ->
    cloudscraper -> requests thường. anti_bot=False thì chỉ dùng requests.
    """
    errors = []

    if anti_bot and _CFFI:
        try:
            r = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
            if r.status_code == 200 and r.text:
                return r.text, None
            errors.append(f"curl_cffi HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"curl_cffi: {str(e)[:60]}")

    if anti_bot and _CLOUDSCRAPER:
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False})
            r = scraper.get(url, timeout=timeout)
            if r.status_code == 200 and r.text:
                return r.text, None
            errors.append(f"cloudscraper HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"cloudscraper: {str(e)[:60]}")

    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text, None
        errors.append(f"requests HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"requests: {str(e)[:60]}")

    return None, "; ".join(errors) or "không lấy được HTML"


def domain_of(url: str) -> str:
    try:
        net = urlparse(url).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


# ---------- Parse kết quả Serper ----------

def parse_image_results(data: dict, seed: str):
    hits = []
    for img in data.get("images", []):
        page_url = img.get("link") or ""
        if not page_url:
            continue
        hits.append(ImageHit(
            seed_keyword=seed,
            title=img.get("title", ""),
            image_url=img.get("imageUrl", ""),
            page_url=page_url,
            source=img.get("source", ""),
            domain=img.get("domain") or domain_of(page_url),
            position=img.get("position", 0),
        ))
    return hits


def parse_web_results(data: dict, domain: str):
    pages = []
    for o in data.get("organic", []):
        pages.append(PageHit(
            domain=domain,
            title=o.get("title", ""),
            link=o.get("link", ""),
            snippet=o.get("snippet", ""),
            position=o.get("position", 0),
        ))
    return pages


# ---------- Trích keyword từ SERP (title + snippet + YAKE) ----------

# YAKE không kèm stopwords tiếng Việt -> tự cung cấp để keyphrase sạch hơn
STOPWORDS_VI = {
    "và", "của", "là", "có", "được", "cho", "không", "một", "những", "các", "để",
    "trong", "đã", "với", "này", "đó", "khi", "ra", "nếu", "thì", "mà", "ở", "vào",
    "người", "ta", "tôi", "bạn", "anh", "chị", "em", "họ", "nó", "chúng", "mình",
    "ai", "gì", "nào", "sao", "vì", "do", "bởi", "nên", "cũng", "vẫn", "đang", "sẽ",
    "rồi", "lại", "nữa", "rất", "quá", "lắm", "hơn", "nhất", "càng", "thật", "chỉ",
    "mỗi", "từng", "mọi", "cả", "toàn", "hết", "đều", "nhau", "cùng", "theo", "về",
    "đến", "tới", "qua", "lên", "xuống", "sang", "vô", "ngoài", "trên", "dưới",
    "giữa", "trước", "sau", "bên", "cạnh", "đây", "kia", "ấy", "thế", "vậy", "như",
    "bằng", "hay", "hoặc", "còn", "nhưng", "song", "tuy", "dù", "mặc", "tại", "thôi",
    "có thể", "phải", "cần", "muốn", "biết", "thấy", "làm", "đi", "nói", "cái",
    "con", "chiếc", "việc", "điều", "cách", "lúc", "nơi", "chỗ", "mấy", "vài",
    "thường", "luôn", "ngay", "vừa", "mới", "đôi", "bao", "nhiều", "ít", "đây là",
    "hôm nay", "hiện", "nay", "thêm", "tất", "tất cả", "đối", "với", "cùng", "trở",
    "the", "a", "an", "of", "and", "to", "in", "for", "on", "is", "are", "you",
}


def _yake_keywords(text: str, lang: str = "en", top: int = 20, max_ngram: int = 3):
    if not _YAKE_AVAILABLE or not text.strip():
        return []
    try:
        kw_args = {"lan": lang, "n": max_ngram, "top": top, "dedupLim": 0.9}
        if lang.lower().startswith("vi"):
            kw_args["stopwords"] = STOPWORDS_VI
        kw = yake.KeywordExtractor(**kw_args)
        return [k for k, _score in kw.extract_keywords(text)]
    except Exception:
        # Ngôn ngữ không hỗ trợ -> bỏ qua, không làm vỡ pipeline
        return []


def keywords_from_pages(pages, seed: str, lang: str = "vi", use_yake: bool = True,
                        vi_only: bool = False, vi_threshold: float = 0.02):
    """Sinh KeywordRecord từ danh sách PageHit của 1 domain."""
    records = []
    if not pages:
        return records
    domain = pages[0].domain
    blob_parts = []
    for p in pages:
        if vi_only and not is_vietnamese(f"{p.title} {p.snippet}", vi_threshold):
            continue   # bỏ trang ngoại ngữ (site spam song ngữ)
        if p.title:
            records.append(KeywordRecord(
                keyword=p.title.strip(), domain=p.domain, source_url=p.link,
                origin="serp_title", seed_keyword=seed))
            blob_parts.append(p.title)
        if p.snippet:
            # snippet là câu dài -> chỉ dùng làm input cho YAKE, không tính là keyword
            blob_parts.append(p.snippet)
    if use_yake:
        text = " . ".join(b for b in blob_parts if b)
        for kw in _yake_keywords(text, lang=lang):
            records.append(KeywordRecord(
                keyword=kw, domain=domain, source_url="",
                origin="yake", seed_keyword=seed))
    return records


# ---------- Scrape trang trực tiếp (bổ sung) ----------

# Anchor text rác (điều hướng) — bỏ qua khi lấy keyword.
# Ưu tiên tiếng Việt (site mục tiêu là tiếng Việt), kèm bản không dấu + tiếng Anh.
NAV_ANCHORS = {
    # English
    "home", "read more", "read more »", "click here", "next", "previous", "prev",
    "more", "view more", "see more", "login", "log in", "sign up", "sign in",
    "register", "contact", "contact us", "about", "about us", "privacy policy",
    "terms", "terms of service", "terms and conditions", "faq", "help", "back",
    "top", "menu", "search", "subscribe", "newsletter", "cookie policy",
    "sitemap", "share", "tweet", "facebook", "twitter", "instagram",
    # Tiếng Việt (có dấu)
    "trang chủ", "đọc thêm", "xem thêm", "xem tất cả", "xem chi tiết", "chi tiết",
    "tiếp theo", "trước", "trang trước", "trang sau", "trang tiếp",
    "đăng nhập", "đăng ký", "đăng xuất", "liên hệ", "giới thiệu", "về chúng tôi",
    "chính sách bảo mật", "điều khoản", "điều khoản sử dụng", "tìm kiếm",
    "chia sẻ", "bình luận", "trả lời", "tải về", "tải xuống", "giỏ hàng",
    "thanh toán", "hỗ trợ", "trợ giúp", "quay lại", "đầu trang", "cuối trang",
    "câu hỏi thường gặp", "sơ đồ trang", "danh mục", "xem ngay", "tại đây",
    # Tiếng Việt (không dấu)
    "trang chu", "doc them", "xem them", "xem tat ca", "xem chi tiet", "chi tiet",
    "tiep theo", "truoc", "trang truoc", "trang sau", "trang tiep",
    "dang nhap", "dang ky", "dang xuat", "lien he", "gioi thieu", "ve chung toi",
    "chinh sach bao mat", "dieu khoan", "dieu khoan su dung", "tim kiem",
    "chia se", "binh luan", "tra loi", "tai ve", "tai xuong", "gio hang",
    "thanh toan", "ho tro", "tro giup", "quay lai", "dau trang", "cuoi trang",
    "cau hoi thuong gap", "so do trang", "danh muc", "xem ngay", "tai day",
}


def is_useful_anchor(text: str) -> bool:
    t = normalize(text)
    if len(t) < 3 or len(t) > 80:
        return False
    if t in NAV_ANCHORS:
        return False
    if t.isdigit():
        return False
    if "@" in t or "[email" in t:   # email / email mã hóa
        return False
    return True


def _extract_internal_links(soup, base_url: str):
    """Trả về list[(absolute_url, anchor_text)] cùng domain, đã dedup."""
    base_dom = domain_of(base_url)
    seen = set()
    links = []
    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if not href or href[0] in "#?" or href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url, href).split("#")[0].rstrip("/")
        if not full.startswith("http") or domain_of(full) != base_dom:
            continue
        if full in seen or full.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(full)
        links.append((full, a.get_text(strip=True)))
    return links


_TAG_HREF_HINTS = ("/tag/", "/tags/", "/the-loai/", "/chu-de/", "/danh-muc/", "/chuyen-muc/")


def scrape_page(url: str, timeout: int = 15, collect_links: bool = False,
                anti_bot: bool = True):
    """Trả về (list[(origin, text)], list[(url, anchor)], error_or_None)."""
    items = []
    links = []
    html, err = fetch_html(url, timeout=timeout, anti_bot=anti_bot)
    if html is None:
        return items, links, err
    try:
        soup = BeautifulSoup(html, "lxml")

        # meta keywords = "seed keywords" của trang
        mk = soup.find("meta", attrs={"name": re.compile("keywords", re.I)})
        if mk and mk.get("content"):
            for k in mk["content"].split(","):
                k = k.strip()
                if k:
                    items.append(("meta_keywords", k))

        if soup.title and soup.title.string:
            items.append(("title", soup.title.string.strip()))

        md = soup.find("meta", attrs={"name": re.compile("description", re.I)})
        if md and md.get("content"):
            items.append(("meta_description", md["content"].strip()))

        for tag in ("h1", "h2", "h3"):
            for h in soup.find_all(tag)[:10]:
                t = h.get_text(strip=True)
                if t:
                    items.append((tag, t))

        # đoạn văn <p> (làm input cho YAKE ở pipeline)
        for p in soup.find_all("p")[:40]:
            t = p.get_text(strip=True)
            if 20 <= len(t) <= 400:
                items.append(("p", t))

        # link tag/chuyên mục (rel=tag hoặc href chứa /tag/, /chu-de/...)
        for a in soup.find_all("a", href=True):
            rel = " ".join(a.get("rel") or []).lower()
            href = a["href"].lower()
            if "tag" in rel or any(h in href for h in _TAG_HREF_HINTS):
                t = a.get_text(strip=True)
                if t and is_useful_anchor(t):
                    items.append(("tag", t))

        if collect_links:
            links = _extract_internal_links(soup, url)
        return items, links, None
    except Exception as e:
        return items, links, str(e)


def records_from_scraped(items, domain, url, seed, prefix="",
                         use_yake=True, lang="vi"):
    """Chuyển items scrape -> KeywordRecord. <p> chỉ dùng làm input YAKE."""
    recs = []
    body_parts = []
    for origin, text in items:
        if origin == "p":
            body_parts.append(text)
            continue
        recs.append(KeywordRecord(
            keyword=text, domain=domain, source_url=url,
            origin=prefix + origin, seed_keyword=seed))
        if origin in ("h1", "h2", "h3", "title", "tag"):
            body_parts.append(text)
    if use_yake and body_parts:
        for kw in _yake_keywords(" . ".join(body_parts), lang=lang):
            recs.append(KeywordRecord(
                keyword=kw, domain=domain, source_url=url,
                origin=prefix + "yake", seed_keyword=seed))
    return recs


# ---------- Chuẩn hóa & gom nhóm ----------

def normalize(kw: str) -> str:
    return re.sub(r"\s+", " ", (kw or "")).strip().lower()


# Ký tự RIÊNG của tiếng Việt — KHÔNG gồm à á â è é ê ì í ò ó ô ã õ ù ú (trùng
# tiếng Ý/Pháp/Bồ/Tây Ban Nha). Chỉ giữ ký tự mà các ngôn ngữ Romance không có.
_VIET_UNIQUE = set(
    "ăđơư"
    "ảạằắẳẵặầấẩẫậ"
    "ẻẽẹềếểễệ"
    "ỉĩị"
    "ỏọồốổỗộờớởỡợ"
    "ủũụừứửữự"
    "ỷỹỵ"
)


def vietnamese_ratio(text: str) -> float:
    """Tỉ lệ ký tự đặc trưng tiếng Việt trên tổng số chữ cái."""
    letters = [c for c in (text or "").lower() if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c in _VIET_UNIQUE) / len(letters)


def is_vietnamese(text: str, threshold: float = 0.02) -> bool:
    return vietnamese_ratio(text) >= threshold


def aggregate(records, max_keyword_words=6):
    """Gom records -> danh sách dict đã dedup, đếm tần suất, sắp xếp.

    Mỗi dòng gắn thêm `word_count` và `type`:
    - "keyword": ngắn (<= max_keyword_words từ) -> đúng nghĩa keyword
    - "title":   dài hơn -> tiêu đề/heading (không phải keyword)
    """
    agg = defaultdict(lambda: {
        "count": 0, "domains": set(), "origins": set(),
        "seeds": set(), "display": "",
    })
    for r in records:
        norm = normalize(r.keyword)
        if not norm or len(norm) < 2 or len(norm) > 100:   # bỏ rỗng & câu quá dài
            continue
        a = agg[norm]
        a["count"] += 1
        if r.domain:
            a["domains"].add(r.domain)
        a["origins"].add(r.origin)
        if r.seed_keyword:
            a["seeds"].add(r.seed_keyword)
        if not a["display"]:
            a["display"] = r.keyword.strip()

    rows = []
    for _norm, a in agg.items():
        wc = len(a["display"].split())
        rows.append({
            "keyword": a["display"],
            "type": "keyword" if wc <= max_keyword_words else "title",
            "word_count": wc,
            "frequency": a["count"],
            "domain_count": len(a["domains"]),
            "domains": ", ".join(sorted(a["domains"])),
            "origins": ", ".join(sorted(a["origins"])),
            "seed_keywords": ", ".join(sorted(a["seeds"])),
        })
    rows.sort(key=lambda x: (-x["frequency"], -x["domain_count"], x["keyword"]))
    return rows


def filter_by_topic(rows, terms):
    """Chỉ giữ keyword chứa ít nhất 1 cụm từ chủ đề (substring, không dấu cách thừa)."""
    terms = [normalize(t) for t in (terms or []) if t and t.strip()]
    if not terms:
        return rows
    return [r for r in rows if any(t in normalize(r["keyword"]) for t in terms)]
