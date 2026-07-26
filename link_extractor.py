"""Đếm & liệt kê link STACKING / BIO mà 1 trang nguồn trỏ ra — CHỈ link ĐÃ KHAI BÁO.

Nguyên tắc lọc (quan trọng nhất): một link chỉ hợp lệ khi **đích đến đã được khai
báo**, tức là khớp với một trong hai nguồn khai báo:

  1. 🎯 **Domain của bạn** (`my_domains`) — bất kể đường dẫn nào trên domain đó.
  2. 🔁 **URL trong danh sách đang check** (`check_urls`), theo 2 mức:
     - `khớp URL`   — trùng đúng URL (bỏ qua khác biệt http/https, www, `/` cuối, chữ hoa/thường)
     - `biến thể`   — cùng domain đã khai báo VÀ chứa cùng handle
                      (vd `hitclubdtac.tumblr.com` ↔ `tumblr.com/hitclubdtac`,
                       `bs.gravatar.com/hitclubdtac` ↔ `gravatar.com/hitclubdtac`)

Mọi link khác đều **KHÔNG hợp lệ** và không được liệt kê: link nội bộ cùng domain,
social/nền tảng của chính site chủ (`instagram.com/zachmoonshinemdpr`, `jamroom.net`,
`makewebeasy.com`...), quảng cáo, iframe/js, `mailto:`/`tel:`. Riêng trường hợp
"cùng domain đã khai báo nhưng KHÁC handle" (vd `x.com/ZachMoonshine` trong khi bạn
khai `x.com/HITCLUBDTAC`) cũng bị loại — vì đó là tài khoản của người khác.

Số link bị loại luôn được trả về (`dropped_*`) để hiển thị minh bạch.

URL viết dạng chữ (không click được) và URL chỉ nằm trong JSON/JS/meta (trang render
bằng JS) được liệt kê riêng với nhãn rõ ràng — cũng chỉ khi khớp khai báo.
"""
from __future__ import annotations

import re
import warnings
from urllib.parse import parse_qsl, unquote, urljoin, urlparse

try:
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
    # Vài URL trả RSS/XML thay vì HTML — vẫn parse được, khỏi cần cảnh báo ồn ào.
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    _BS4 = True
except ImportError:
    try:
        from bs4 import BeautifulSoup
        _BS4 = True
    except Exception:
        _BS4 = False
except Exception:
    _BS4 = False

# Số link (đã gộp trùng) giữ lại tối đa cho MỖI trang — tránh phình bộ nhớ.
MAX_LINKS_PER_PAGE = 500

# TLD 2 cấp phổ biến -> để nhận "blog.site.com" và "site.com" là CÙNG site
_TWO_LEVEL_TLD = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.vn", "net.vn", "org.vn", "edu.vn",
    "gov.vn", "com.au", "net.au", "org.au", "edu.au", "co.jp", "ne.jp", "or.jp",
    "co.kr", "com.br", "com.mx", "com.ar", "com.co", "com.pe", "co.in", "com.cn",
    "com.tw", "com.hk", "com.sg", "com.my", "co.th", "com.ph", "co.id", "com.tr",
    "co.za", "co.nz", "com.ua", "co.il", "com.pk", "com.bd", "com.ng", "com.eg",
    "org.ru", "com.ru", "net.ru", "pp.ru", "spb.ru", "msk.ru", "co.ua", "in.ua",
    "pp.ua", "kiev.ua", "com.pl", "net.pl", "org.pl", "com.ro", "com.gr", "or.kr",
    "ne.kr", "ac.in", "net.in", "org.in", "co.ke", "co.tz", "com.gh", "com.uy",
    "com.ec", "com.py", "com.bo", "com.do", "com.gt", "com.pa", "com.cy", "co.at",
    "or.at", "co.hu", "com.hr", "org.il", "ac.il", "co.ma", "com.qa", "com.sa",
    "com.kw", "com.lb", "com.jo", "com.om", "com.bh", "org.tw", "gov.tw", "org.br",
    "net.br", "com.tn", "com.dz", "com.ly", "org.au", "id.au", "net.nz", "org.nz",
}

# Vị trí link trên trang (chỉ để THÔNG TIN, không dùng để loại link nữa)
_ZONE_RULES = [
    ("bio/profile", r"bio|profile|signature|about\b|about-|user-?info|userinfo|"
                    r"member-?info|memberinfo|author|vcard|contact|homepage|"
                    r"website|user-?panel|user-?field|custom-?field|social"),
    ("comment", r"comment|reply|respond|disqus|discussion"),
    ("footer", r"footer|colophon|site-?bottom|copyright"),
    ("menu/nav", r"\bnav\b|navbar|navigation|\bmenu\b|breadcrumb|topbar|header"),
    ("sidebar", r"sidebar|widget|aside|related|popular|recent-?posts|blogroll"),
    ("quảng cáo", r"adsbygoogle|advertisement|advertising|\bad-?(?:slot|unit|zone|"
                  r"container|wrapper|box|banner)\b|banner-?ad|\bads\b"),
]
_ZONE_RULES = [(name, re.compile(pat, re.I)) for name, pat in _ZONE_RULES]
_ZONE_TAGS = {"nav": "menu/nav", "footer": "footer", "header": "menu/nav",
              "aside": "sidebar"}
_ZONE_DEFAULT = "nội dung"

_KIND_LABEL = {"a": "🔗 thẻ a", "area": "🗺️ area",
               "text": "📄 text thường (không click)",
               "wrap": "🔀 thẻ a qua trang trung gian",
               "embed_a": "🔗 thẻ a trong JSON/JS (hiện sau khi JS render)",
               "embed": "📦 ẩn trong JSON/JS (cần JS mới hiện)",
               "meta": "📝 trong meta/description (không click)"}

# ---- Link trung gian: href trỏ về chính site rồi mới nhảy sang đích thật ----
# vd chess.com/away?url=... · l.facebook.com/l.php?u=... · pdc.edu/?URL=...
_WRAP_PARAMS = ("url", "u", "uri", "target", "redirect", "redirect_url", "redirecturl",
                "to", "dest", "destination", "link", "out", "outurl", "goto", "next",
                "continue", "jump", "return", "site", "web")

MATCH_LABEL = {
    "mine": "🎯 domain của bạn",
    "exact": "🔁 khớp URL đang check",
    "variant": "🔁 biến thể URL đang check",
}

# URL bất kỳ trong mã nguồn (kể cả trong <script>/JSON)
_ANY_URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]{}\\|^`]+", re.I)

# File tài nguyên: gặp trong text/JSON là đường dẫn CSS/JS/ảnh, không phải link
_ASSET_EXT_RE = re.compile(
    r"\.(?:css|js|mjs|json|xml|png|jpe?g|gif|webp|avif|svg|ico|bmp|woff2?|ttf|eot|"
    r"otf|mp[34]|m4a|webm|ogg|wav|zip|rar|7z|gz)(?:$|[?#])", re.I)

# URL viết trần trong nội dung (link "chôn" thành chữ)
_TEXT_URL_RE = re.compile(
    r"(?<![\w@/.-])((?:https?://|www\.)[a-z0-9](?:[-a-z0-9._~%]*[a-z0-9])?"
    r"(?::\d{2,5})?(?:/[^\s<>\"'()\[\]{}]*)?)", re.I)

# Đoạn đường dẫn KHÔNG phải handle (dùng chung cho mọi site) -> bỏ khi lấy handle
_GENERIC_SEGMENTS = {
    "user", "users", "usr", "profile", "profiles", "member", "members", "memberlist",
    "about", "aboutme", "activity", "forum", "forums", "topic", "topics", "thread",
    "post", "posts", "page", "pages", "view", "detail", "details", "index", "home",
    "author", "authors", "channel", "channels", "creator", "creators", "account",
    "accounts", "people", "persons", "person", "shop", "store", "info", "readme",
    "settings", "preferences", "public", "share", "story", "blog", "news", "photos",
    "photo", "video", "videos", "album", "albums", "board", "id", "uid", "u", "p",
    "en", "vi", "ja", "jp", "de", "fr", "es", "it", "ru", "cn", "tw", "kr", "th",
    "enus", "engb", "default", "aspx", "php", "html", "htm", "cgi", "asp", "jsp",
    "citations", "trailblazer", "bookmark", "collections", "designs", "quotes",
    "network", "projects", "recipes", "reports", "viz", "mbr", "app", "apps",
    "profilepreviewtrue", "tab", "mode", "viewprofile", "action", "space",
}
_MIN_HANDLE = 5              # handle ngắn hơn = quá chung, dễ khớp bừa


def _alnum(s: str) -> str:
    """'hitclub-dtac' | 'HITCLUB_DTAC' | 'hitclub.dtac' -> 'hitclubdtac'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _bare_host(url: str) -> str:
    return re.sub(r"^www\.", "", (urlparse(url).hostname or "").lower())


def root_domain(host: str) -> str:
    """site.com từ blog.site.com; giữ nguyên TLD 2 cấp (site.com.vn)."""
    host = re.sub(r"^www\.", "", (host or "").lower()).strip(".")
    host = re.split(r"[/?#]", host)[0].split(":")[0]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in _TWO_LEVEL_TLD and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def norm_domain(raw: str) -> str:
    """Chuẩn hoá domain người dùng nhập → root domain.

    'https://A.com/duong-dan?x=1', 'www.a.com:443', 'a.com/', ' A.COM ' → 'a.com'.
    """
    s = (raw or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = re.split(r"[/?#]", s)[0]
    s = s.split("@")[-1].split(":")[0]
    return root_domain(s)


def _norm(url: str) -> str:
    """Khoá so trùng: bỏ scheme/www/slash cuối/fragment, hạ chữ thường."""
    p = urlparse(url if re.match(r"^https?://", url or "", re.I) else "http://" + (url or ""))
    host = re.sub(r"^www\.", "", (p.netloc or "").lower())
    q = f"?{p.query}" if p.query else ""
    return f"{host}{p.path.rstrip('/')}{q}".lower()


def _tokens(parts) -> set:
    out = set()
    for raw in parts:
        for piece in re.split(r"[=@]", raw or ""):     # 'u=263752', '@name'
            piece = re.sub(r"\.(?:php|html?|aspx?|jsp|cgi|do|action)$", "", piece, flags=re.I)
            tok = _alnum(piece)
            if len(tok) >= _MIN_HANDLE and tok not in _GENERIC_SEGMENTS:
                out.add(tok)
    return out


def _handles_of(url: str) -> set:
    """Lấy 'handle' (tên tài khoản / id) từ 1 URL đã khai báo.

    Ưu tiên lấy từ **đường dẫn + query** (bỏ đuôi .php/.html, bỏ từ chung chung
    user/profile/forum/about..., bỏ token < 5 ký tự). Nhờ đó `x.com/HITCLUBDTAC`
    cho handle 'hitclubdtac' → loại được `x.com/ZachMoonshine` (cùng domain nhưng
    khác tài khoản), và `.../profile.php?u=263752` chỉ cho handle '263752' nên
    `?u=999999` không bị khớp bừa.

    Chỉ khi đường dẫn KHÔNG cho handle nào (vd `hitclubdtac.pixieset.com/`) mới
    lấy nhãn subdomain làm handle.
    """
    p = urlparse(url if re.match(r"^https?://", url or "", re.I) else "http://" + (url or ""))
    host = re.sub(r"^www\.", "", (p.hostname or "").lower())
    root = root_domain(host)
    out = _tokens(re.split(r"[/;]", p.path or "") + re.split(r"[&;]", p.query or ""))
    if not out and host != root:
        out = _tokens(host[: -len(root) - 1].split("."))
    return out


def build_declared(my_domains=(), check_urls=()) -> dict:
    """Gom mọi khai báo thành 1 bộ tra cứu (tính 1 lần, dùng cho mọi trang).

    - mine_roots:   root domain của bạn → link nào trỏ về đây cũng hợp lệ
    - urls:         khoá URL đã khai báo (so trùng tuyệt đối)
    - root_handles: {root domain đã khai báo: {handle...}} → xét biến thể
    """
    mine_roots = {norm_domain(d) for d in my_domains if str(d).strip()} - {""}
    urls, root_handles = set(), {}
    for u in check_urls:
        u = str(u).strip()
        if not u:
            continue
        urls.add(_norm(u))
        root = norm_domain(u)
        if root:
            root_handles.setdefault(root, set()).update(_handles_of(u))
    return {"mine_roots": mine_roots, "urls": urls, "root_handles": root_handles}


_EMPTY_DECLARED = {"mine_roots": set(), "urls": set(), "root_handles": {}}


def match_declared(url: str, declared: dict) -> str:
    """'mine' | 'exact' | 'variant' | '' — link này có khớp khai báo nào không."""
    host = _bare_host(url)
    if not host:
        return ""
    root = root_domain(host)
    if root in declared["mine_roots"]:
        return "mine"
    if _norm(url) in declared["urls"]:
        return "exact"
    handles = declared["root_handles"].get(root)
    if handles:
        blob = _alnum(host + (urlparse(url).path or "") + (urlparse(url).query or ""))
        if any(h in blob for h in handles):
            return "variant"
    return ""


def _clean(text: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def unwrap_url(url: str) -> str:
    """Bóc URL đích thật từ link trung gian, rỗng nếu không phải link trung gian.

    Rất nhiều site không cho trỏ thẳng ra ngoài mà bọc qua trang chuyển tiếp của
    chính họ: `chess.com/away?url=https%3A%2F%2Fdich.com`,
    `l.facebook.com/l.php?u=...`, `pdc.edu/?URL=...`, `.../jump.php?url=...`.
    Nếu không bóc, link đó bị tính là "nội bộ" và mất luôn backlink thật.
    """
    if not url:
        return ""
    try:
        p = urlparse(url)
        for key, val in parse_qsl(p.query, keep_blank_values=False):
            if key.lower() not in _WRAP_PARAMS:
                continue
            cand = unquote(val).strip()
            if re.match(r"^https?://", cand, re.I) and _bare_host(cand):
                return cand
        # Dạng nhét thẳng vào đường dẫn: /redirect/https://dich.com/...
        m = re.search(r"(https?(?::|%3A)(?://|%2F%2F).+)$", p.path or "", re.I)
        if m:
            cand = unquote(m.group(1)).strip()
            if re.match(r"^https?://", cand, re.I) and _bare_host(cand):
                return cand
    except Exception:
        pass
    return ""


def _decode_escapes(html: str) -> str:
    """Giải mã HTML/JSON escape để thấy được URL bị "che" trong JSON khởi tạo.

    Trang render bằng JS thường nhồi cả khối HTML bio vào JSON, ở đó dấu `/` thành
    `\\/` hoặc `\\u002F`, `<` thành `\\u003C`... nên regex URL thường không khớp.
    Chess.com, Ameba Ownd (shopinfo.jp), Gumroad... đều thuộc dạng này.
    """
    s = ((html or "").replace("\\/", "/").replace('\\"', '"')
         .replace("&amp;", "&").replace("&#x2F;", "/"))
    if "\\u" in s or "\\U" in s:
        s = re.sub(r"\\u([0-9a-fA-F]{4})",
                   lambda m: chr(int(m.group(1), 16)), s)
    return s


def _declared_domain_re(declared: dict):
    """Regex bắt MỌI cách viết domain đã khai báo — kể cả thiếu scheme.

    Vimeo ghi bio là `Website: //sunwindtac.com/` (không có `https:`), nhiều site
    chỉ ghi `sunwindtac.com` — regex URL thông thường bỏ sót hết.
    """
    roots = set(declared["mine_roots"]) | set(declared["root_handles"])
    roots = {r for r in roots if r}
    if not roots:
        return None
    alt = "|".join(sorted((re.escape(r) for r in roots), key=len, reverse=True))
    return re.compile(
        r"(?<![\w.-])(?:https?:)?(?://)?((?:[a-z0-9][a-z0-9-]*\.)*(?:" + alt + r"))"
        r"(/[^\s\"'<>\\)\]}]*)?", re.I)


def _zone_of(tag) -> str:
    """Đi ngược cây DOM tìm khu vực chứa link (chỉ để hiển thị)."""
    node, hops = tag, 0
    while node is not None and hops < 14:
        name = getattr(node, "name", "") or ""
        if name in ("body", "html", "[document]"):
            break
        attrs = getattr(node, "attrs", None) or {}
        cls = attrs.get("class")
        blob = " ".join([
            " ".join(cls) if isinstance(cls, list) else str(cls or ""),
            str(attrs.get("id") or ""), str(attrs.get("role") or ""),
            str(attrs.get("itemprop") or ""),
        ])
        if blob.strip():
            for zone, pat in _ZONE_RULES:
                if pat.search(blob):
                    return zone
        if name in _ZONE_TAGS:
            return _ZONE_TAGS[name]
        node, hops = getattr(node, "parent", None), hops + 1
    return _ZONE_DEFAULT


def _follow_label(rel: str, page_nofollow: bool) -> str:
    r = (rel or "").lower()
    tags = [t for t in ("nofollow", "ugc", "sponsored") if re.search(rf"\b{t}\b", r)]
    if page_nofollow and "nofollow" not in tags:
        tags.insert(0, "nofollow")
    return "+".join(tags) if tags else "dofollow"


def _make_soup(html: str):
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style", "template", "noscript"]):
        bad.decompose()
    return soup


def _anchor_links(soup, base_url: str):
    """Sinh (kind, href, anchor, rel, zone, base, page_nofollow) cho <a>/<area>."""
    base_tag = soup.find("base", href=True)
    base = urljoin(base_url, base_tag["href"].strip()) if base_tag else base_url
    meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    page_nofollow = bool(meta and re.search(r"nofollow|none", meta.get("content", ""), re.I))

    for tag in soup.find_all(["a", "area"]):
        rel = tag.get("rel") or ""
        rel = " ".join(rel) if isinstance(rel, list) else str(rel)
        href = tag.get("href", "")
        if tag.name == "area":
            anchor = _clean(tag.get("alt") or "") or "[image map]"
        else:
            anchor = _clean(tag.get_text(" "))
            if not anchor:
                img = tag.find("img")
                anchor = (f"[ảnh] {_clean(img.get('alt') or '', 60)}".strip()
                          if img is not None else "[trống]")
        yield (tag.name, href, anchor, rel, _zone_of(tag), base, page_nofollow)


def _anchor_links_regex(html: str, base_url: str):
    """Dự phòng khi không có bs4: không dò được vị trí -> coi như 'nội dung'."""
    page_nofollow = bool(re.search(
        r'<meta[^>]+name=["\']?robots["\']?[^>]+content=["\'][^"\']*(nofollow|none)',
        html, re.I))
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.I | re.S):
        attrs, inner = m.group(1), m.group(2)
        hm = re.search(r'href\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', attrs, re.I)
        if not hm:
            continue
        rm = re.search(r'rel\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', attrs, re.I)
        href = next((g for g in hm.groups() if g is not None), "")
        rel = next((g for g in (rm.groups() if rm else []) if g), "")
        anchor = _clean(re.sub(r"<[^>]+>", " ", inner)) or "[trống]"
        yield "a", href, anchor, rel, _ZONE_DEFAULT, base_url, page_nofollow


def _text_urls(soup, html: str):
    """Sinh (url, zone) cho URL viết dạng chữ (không nằm trong href nào)."""
    if soup is not None:
        for node in soup.find_all(string=_TEXT_URL_RE):
            parent = getattr(node, "parent", None)
            if parent is not None and getattr(parent, "name", "") in ("a", "area"):
                continue                     # anchor text của link, không phải text trần
            zone = _zone_of(parent) if parent is not None else _ZONE_DEFAULT
            for m in _TEXT_URL_RE.finditer(str(node)):
                yield m.group(1), zone
        return
    body = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1>", " ", html or "")
    body = re.sub(r"<[^>]+>", " ", body)
    for m in _TEXT_URL_RE.finditer(body):
        yield m.group(1), _ZONE_DEFAULT


def _embedded_urls(html: str, declared: dict):
    """Đích ĐÃ KHAI BÁO nhưng không nằm trong thẻ <a> của HTML thô.

    Trang profile render bằng JS (Vimeo, Chess.com, Ameba Ownd, Gumroad, Pinterest,
    Tumblr...) nhồi khối HTML bio vào JSON, hoặc chỉ ghi tên domain trần trong meta
    description. Ở đây giải mã escape rồi tìm đúng các đích đã khai báo (không quét
    bừa nên không nhiễu), phân biệt 3 mức:

      - 'embed_a' : là thẻ <a href> thật bên trong JSON → kèm rel để biết dofollow
      - 'meta'    : chỉ xuất hiện trong thẻ <meta ...> (description/og)
      - 'embed'   : chỉ xuất hiện đâu đó trong mã (JSON/JS)

    Sinh (url, kind, rel).
    """
    if not html or not (declared["mine_roots"] or declared["root_handles"]):
        return
    raw = _decode_escapes(html)
    dom_re = _declared_domain_re(declared)
    seen = set()

    # 1) Thẻ <a> nằm trong JSON đã giải mã -> link thật, có cả rel
    for m in re.finditer(r"<a\b([^>]{0,600}?)>", raw, re.I):
        attrs = m.group(1)
        hm = re.search(r'href\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', attrs, re.I)
        if not hm:
            continue
        url = (next((g for g in hm.groups() if g is not None), "") or "").strip()
        if not re.match(r"^https?://", url, re.I) or _ASSET_EXT_RE.search(url):
            continue
        if not match_declared(url, declared):
            continue
        key = _norm(url)
        if key in seen:
            continue
        seen.add(key)
        rm = re.search(r'rel\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', attrs, re.I)
        yield url, "embed_a", (next((g for g in (rm.groups() if rm else []) if g), "") or "")

    # 2) URL đầy đủ / tên domain viết trần trong meta rồi trong toàn bộ mã
    meta_blob = " ".join(m.group(0) for m in re.finditer(r"<meta\b[^>]*>", raw, re.I))
    for blob, kind in ((meta_blob, "meta"), (raw, "embed")):
        if not blob:
            continue
        for m in _ANY_URL_RE.finditer(blob):
            url = m.group(0).rstrip(".,;:!?)]}\"'")
            if _ASSET_EXT_RE.search(url) or not match_declared(url, declared):
                continue
            key = _norm(url)
            if key not in seen:
                seen.add(key)
                yield url, kind, ""
        if dom_re is None:
            continue
        for m in dom_re.finditer(blob):                # thiếu scheme: //a.com/ hoặc a.com
            url = "https://" + m.group(1) + (m.group(2) or "")
            url = url.rstrip(".,;:!?)]}\"'")
            if _ASSET_EXT_RE.search(url) or not match_declared(url, declared):
                continue
            key = _norm(url)
            if key not in seen:
                seen.add(key)
                yield url, kind, ""


def extract(html: str, base_url: str, my_domains=(), check_urls=(), declared=None,
            max_links: int = MAX_LINKS_PER_PAGE):
    """Phân tích 1 trang → chỉ những link ĐÃ KHAI BÁO + thống kê.

    declared: bộ tra cứu từ `build_declared()` (nên tính 1 lần cho cả danh sách).
              Không truyền thì tự dựng từ my_domains/check_urls.
    """
    stats = {"total": 0, "dropped_internal": 0, "dropped_undeclared": 0,
             "dropped_kind": 0}
    base = {**stats, "external": 0, "external_domains": 0, "dofollow": 0,
            "nofollow": 0, "bio": 0, "content": 0, "text_only": 0, "embedded": 0,
            "embedded_anchor": 0,
            "page_nofollow": False, "truncated": 0, "links": [], "mine": 0,
            "mine_dofollow": 0, "mine_note": "", "to_checked": 0, "checked_exact": 0}
    if declared is None:
        declared = build_declared(my_domains, check_urls)
    if not html:
        return base

    src_host = _bare_host(base_url)
    src_root = root_domain(src_host)
    soup = _make_soup(html) if _BS4 else None
    links_iter = (_anchor_links(soup, base_url) if soup is not None
                  else _anchor_links_regex(html, base_url))

    merged: dict[str, dict] = {}
    href_keys: set = set()
    page_nofollow = False

    for kind, href, anchor, rel, zone, base_href, pnf in links_iter:
        page_nofollow = page_nofollow or pnf
        h = (href or "").strip()
        if not h or h.startswith("#"):
            continue
        low = h.lower()
        if not low.startswith(("http://", "https://")) and re.match(
                r"^[a-z][a-z0-9+.-]*:", low):
            stats["dropped_kind"] += 1         # mailto:/tel:/javascript:...
            continue
        try:
            url = urljoin(base_href, h)
        except Exception:
            continue
        host = _bare_host(url)
        if not host or not urlparse(url).scheme.startswith("http"):
            continue
        stats["total"] += 1

        # Link trung gian (href về chính site rồi mới nhảy ra ngoài) -> lấy đích thật,
        # nếu không sẽ bị tính là "nội bộ" và mất backlink.
        wrapped = ""
        inner = unwrap_url(url)
        if inner and _bare_host(inner) != host:
            wrapped, url, host = url, inner, _bare_host(inner)
            kind = "wrap"

        if host == src_host or root_domain(host) == src_root:
            stats["dropped_internal"] += 1     # link nội bộ -> không phải backlink
            continue
        match = match_declared(url, declared)
        if not match:
            stats["dropped_undeclared"] += 1   # không khớp khai báo -> không hợp lệ
            continue

        key = _norm(url)
        href_keys.add(key)
        follow = _follow_label(rel, page_nofollow)
        row = merged.get(key)
        if row is None:
            merged[key] = {
                "url": url, "domain": host, "loai": "ra ngoài", "anchor": anchor,
                "rel": rel, "follow": follow, "kind": _KIND_LABEL.get(kind, kind),
                "zone": zone, "count": 1, "match": match,
                "mine": match == "mine", "exact": match == "exact",
                "to_checked": match in ("exact", "variant"),
                "via": wrapped,          # URL trung gian đã đi qua (nếu có)
            }
        else:
            row["count"] += 1
            if row["follow"] != "dofollow" and follow == "dofollow":
                row["follow"] = "dofollow"     # có 1 lần dofollow -> tính là được
            if zone != _ZONE_DEFAULT and zone not in row["zone"]:
                row["zone"] = (zone if row["zone"] == _ZONE_DEFAULT
                               else f"{row['zone']}, {zone}")
            if row["anchor"] in ("[trống]", "") and anchor:
                row["anchor"] = anchor

    # ---- URL chôn thành chữ (không click được) — cũng chỉ khi khớp khai báo ----
    text_seen = set()
    for raw, zone in _text_urls(soup, html):
        url = raw if raw.lower().startswith("http") else "http://" + raw
        url = url.rstrip(".,;:!?)]}\"'")
        key, host = _norm(url), _bare_host(url)
        if (not host or not key or key in href_keys or key in text_seen
                or _ASSET_EXT_RE.search(url) or root_domain(host) == src_root):
            continue
        match = match_declared(url, declared)
        if not match:
            continue
        text_seen.add(key)
        merged[f"text:{key}"] = {
            "url": url, "domain": host, "loai": "text (không phải link)",
            "anchor": "—", "rel": "", "follow": "✖ không tính",
            "kind": _KIND_LABEL["text"], "zone": zone, "count": 1, "match": match,
            "mine": match == "mine", "exact": match == "exact",
            "to_checked": match in ("exact", "variant"), "via": "",
        }

    # ---- Link đã khai báo nhưng chỉ nằm trong JSON/JS/meta (trang SPA) ----
    embed_seen, embed_anchor = set(), set()
    for url, ekind, erel in _embedded_urls(html, declared):
        key, host = _norm(url), _bare_host(url)
        if (not host or key in href_keys or key in text_seen or key in embed_seen
                or root_domain(host) == src_root):
            continue
        embed_seen.add(key)
        match = match_declared(url, declared)
        if ekind == "embed_a":
            embed_anchor.add(key)
            loai, follow = "thẻ a trong JSON/JS", _follow_label(erel, page_nofollow)
        else:
            loai, follow = "ẩn trong mã (không phải link)", "✖ không tính"
        merged[f"embed:{key}"] = {
            "url": url, "domain": host, "loai": loai,
            "anchor": "—", "rel": erel, "follow": follow,
            "kind": _KIND_LABEL[ekind], "zone": "ẩn trong mã nguồn", "count": 1,
            "match": match, "mine": match == "mine", "exact": match == "exact",
            "to_checked": match in ("exact", "variant"), "via": "",
        }

    rows = list(merged.values())
    ext = [r for r in rows if r["loai"] == "ra ngoài"]
    dofollow = sum(1 for r in ext if r["follow"] == "dofollow")

    mine_rows = [r for r in rows if r["mine"]]
    mine_anchor = [r for r in mine_rows if r["loai"] == "ra ngoài"]
    mine_do = [r for r in mine_anchor if r["follow"] == "dofollow"]
    mine_json = [r for r in mine_rows if r["loai"] == "thẻ a trong JSON/JS"]
    mine_text = [r for r in mine_rows if r["loai"].startswith("text")]
    if not declared["mine_roots"]:
        mine_note = ""
    elif mine_anchor:
        mine_note = (f"✅ {len(mine_anchor)} link" +
                     (f" ({len(mine_do)} dofollow)" if mine_do else " — toàn nofollow"))
    elif mine_json:
        jdo = sum(1 for r in mine_json if r["follow"] == "dofollow")
        mine_note = (f"✅ {len(mine_json)} thẻ a trong JSON/JS "
                     f"({'dofollow' if jdo else 'nofollow'}) — trang render bằng JS, "
                     f"mở trình duyệt để xác nhận")
    elif mine_text:
        mine_note = "⚠️ chỉ ở dạng text/không click được — không tính là backlink"
    elif mine_rows:
        mine_note = ("⚠️ chỉ thấy trong JSON/JS/meta (trang render bằng JS) — "
                     "mở trình duyệt xem link có thật sự hiện thành thẻ a không")
    else:
        mine_note = "❌ không thấy link về domain của bạn"

    # Ưu tiên hiển thị: link của mình → khớp URL → biến thể → còn lại
    _order = {"mine": 0, "exact": 1, "variant": 2}
    rows.sort(key=lambda r: (_order.get(r.get("match"), 3),
                             0 if r["loai"] == "ra ngoài" else 1,
                             -r["count"], r["domain"]))
    return {
        "total": stats["total"],
        "external": len(ext),
        "external_domains": len({r["domain"] for r in ext if r["domain"]}),
        "dofollow": dofollow,
        "nofollow": len(ext) - dofollow,
        "bio": sum(1 for r in ext if "bio/profile" in r["zone"]),
        "content": sum(1 for r in ext if r["zone"] == _ZONE_DEFAULT),
        "text_only": len(text_seen),
        "embedded": len(embed_seen),
        "embedded_anchor": len(embed_anchor),
        "dropped_internal": stats["dropped_internal"],
        "dropped_undeclared": stats["dropped_undeclared"],
        "dropped_kind": stats["dropped_kind"],
        "page_nofollow": page_nofollow,
        "truncated": max(0, len(rows) - max_links),
        "links": rows[:max_links],
        "mine": len(mine_anchor),
        "mine_dofollow": len(mine_do),
        "mine_note": mine_note,
        "to_checked": sum(1 for r in ext if r["to_checked"]),
        "checked_exact": sum(1 for r in ext if r["exact"]),
    }
