"""Check URL hợp lệ + sống/chết bằng HTTP request thẳng (KHÔNG tốn credit Serper).

Khác với Check Index (hỏi Google đã index chưa), chế độ này gọi thẳng tới URL để
biết trang có tồn tại/truy cập được không.

Phân loại mỗi dòng:
  - ✅ sống:     HTTP 2xx (sau khi theo redirect nếu bật)
  - 🔗 redirect: HTTP 3xx và KHÔNG theo redirect -> báo trang đích (Location)
  - 🔒 chặn:     server sống nhưng chặn/giới hạn tool (401/403/429/503, trang anti-bot)
  - ❌ chết:     404/410/5xx, hoặc soft 404 (trả 200 nhưng nội dung là trang lỗi)
  - ⚠️ lỗi:      URL sai định dạng / DNS fail / timeout / từ chối kết nối / SSL

Tái dùng chuỗi vượt anti-bot: curl_cffi (giả lập TLS Chrome) -> cloudscraper -> requests.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

import requests

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

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": ('"Chromium";v="124", "Google Chrome";v="124", '
                  '"Not-A.Brand";v="99"'),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# Mã HTTP "chưa kết luận là chết" — server có phản hồi nhưng chặn/giới hạn tool,
# hoặc tạm không phục vụ. KHÁC với chết thật (404/410/5xx server lỗi). Note theo mã:
_BLOCK_REASON = {
    401: "cần đăng nhập (401)",
    403: "bị chặn bot/anti-bot (403) — thử mở bằng trình duyệt",
    405: "server từ chối phương thức GET (405) — thường do WAF, trình duyệt vẫn vào được",
    406: "server từ chối định dạng yêu cầu (406)",
    407: "cần proxy xác thực (407)",
    429: "bị giới hạn tần suất (429) — thử lại sau",
    451: "bị chặn vì lý do pháp lý (451)",
    503: "server tạm không khả dụng (503) — quá tải/bảo trì hoặc anti-bot",
}
_BLOCK_CODES = set(_BLOCK_REASON)

# ---- Soft 404: server trả HTTP 200 nhưng nội dung thật ra là trang lỗi ----
# Rất phổ biến: trang "Account Suspended" của cPanel, profile bị xóa/khóa, SPA
# render lỗi... Chỉ soi <title>/<h1>/og:title -> độ chính xác cao, ít báo nhầm.
_BODY_LIMIT = 200_000        # ký tự body đọc tối đa mỗi URL

# Dấu hiệu MẠNH: cụm từ rõ nghĩa, gặp là kết luận ngay. Gom theo NGÔN NGỮ để dễ
# bổ sung — backlink/profile nằm trên site đủ mọi thị trường. Thêm ngôn ngữ mới =
# thêm 1 dòng vào dict này.
_SOFT404_STRONG_BY_LANG = {
    "mã lỗi": [r"\b404\b", r"\b410\b", r"error 404", r"http 404"],
    "en": [
        r"page not found", r"not found", r"page (?:isn'?t|is not) available",
        r"no longer (?:available|exists?)", r"does(?:n'?t| not) exist",
        r"we can'?t find", r"can'?t find that page", r"couldn'?t find",
        r"has been (?:removed|deleted|terminated)", r"nothing found here",
        r"(?:content|profile|user|page|account) (?:is )?unavailable",
        r"(?:account|site|website|domain|user|profile) (?:has been |is |was )?suspended",
        r"account (?:has been |is )?(?:disabled|terminated|deactivated|closed)",
        r"this (?:page|profile|account|content|user)(?: is| has been)? "
        r"(?:unavailable|removed|deleted|gone)",
        r"site not found", r"domain (?:not configured|is parked|parking)",
    ],
    "vi": [
        r"không tìm thấy", r"không tồn tại", r"lỗi 404", r"trang không tồn tại",
        r"đã bị x[oó]a", r"đã bị xoá", r"đã bị gỡ",
        r"vi phạm (?:điều khoản|chính sách|tiêu chuẩn|nguyên tắc)",
        r"nội dung không khả dụng", r"trang bạn (?:tìm|yêu cầu|truy cập)",
        r"tài khoản (?:này )?(?:đã |bị )",
    ],
    "zh": [   # Trung (giản thể + phồn thể)
        r"页面不存在", r"頁面不存在", r"找不到页面", r"找不到網頁", r"找不到頁面",
        r"无法找到", r"無法找到", r"页面未找到", r"页面走丢", r"不存在或已删除",
        r"已被删除", r"已被刪除", r"用户不存在", r"用戶不存在", r"该用户不存在",
        r"账号已被封", r"帳號已被封", r"账户已停用", r"帳戶已停用", r"已注销", r"已註銷",
    ],
    "ja": [
        r"ページが見つかりません", r"見つかりませんでした", r"存在しません",
        r"削除されました", r"アカウントは(?:停止|凍結)", r"お探しのページ",
        r"利用できません",
    ],
    "ko": [
        r"페이지를 찾을 수 없", r"찾을 수 없습니다", r"존재하지 않", r"삭제되었",
        r"계정이 (?:정지|비활성)", r"이용할 수 없",
    ],
    "ru/uk": [
        r"страница не найдена", r"страницу не найдено", r"не найдено", r"не найдена",
        r"не существует", r"сторінку не знайдено", r"не знайдено", r"не існує",
        r"удален", r"удалён", r"удалена", r"видалено",
        r"аккаунт (?:заблокирован|приостановлен)", r"заблокирован",
    ],
    "es": [
        r"página no encontrada", r"pagina no encontrada", r"no encontrada",
        r"no encontrado", r"no existe", r"no está disponible", r"no esta disponible",
        r"cuenta (?:suspendida|desactivada|cancelada)", r"ha sido eliminad[oa]",
    ],
    "pt": [
        r"página não encontrada", r"pagina nao encontrada", r"não encontrada",
        r"nao encontrada", r"não existe", r"nao existe", r"não está disponível",
        r"conta (?:suspensa|desativada)", r"foi removid[oa]", r"foi excluíd[oa]",
    ],
    "fr": [
        r"page introuvable", r"introuvable", r"n'existe pas", r"page non trouvée",
        r"n'est plus disponible", r"non disponible",
        r"compte (?:suspendu|désactivé|supprimé)", r"a été supprimé",
    ],
    "de": [
        r"seite nicht gefunden", r"nicht gefunden", r"existiert nicht",
        r"nicht (?:mehr )?verfügbar", r"konto (?:gesperrt|deaktiviert)",
        r"wurde (?:gelöscht|entfernt)",
    ],
    "it": [
        r"pagina non trovata", r"non trovata", r"non trovato", r"non esiste",
        r"non disponibile", r"account (?:sospeso|disattivato)",
        r"è stat[oa] rimoss[oa]",
    ],
    "nl": [
        r"pagina niet gevonden", r"niet gevonden", r"bestaat niet",
        r"niet beschikbaar", r"is verwijderd", r"account (?:geschorst|opgeheven)",
    ],
    "pl": [
        r"nie znaleziono", r"strona nie istnieje", r"nie istnieje",
        r"nie jest dostępn", r"zosta(?:ła|ł) usunięt",
        r"konto (?:zawieszone|zablokowane)",
    ],
    "tr": [
        r"sayfa bulunamadı", r"bulunamadı", r"mevcut değil", r"silinmiş", r"silindi",
        r"hesap (?:askıya|kapatıl|dondurul)",
    ],
    "id/ms": [
        r"halaman tidak ditemukan", r"tidak ditemukan", r"halaman tidak ada",
        r"tidak tersedia", r"telah dihapus", r"tidak dijumpai",
        r"akun (?:ditangguhkan|dinonaktifkan)",
    ],
    "th": [r"ไม่พบหน้า", r"ไม่พบ", r"ไม่มีอยู่", r"ถูกลบ", r"ไม่สามารถใช้งาน"],
    "ar": [r"الصفحة غير موجودة", r"غير موجود", r"تم حذف", r"تم تعليق", r"غير متاح"],
    "hi": [r"पृष्ठ नहीं मिला", r"नहीं मिला", r"मौजूद नहीं"],
    "cs/sk": [r"stránka nenalezena", r"nenalezeno", r"nebyla nalezena", r"neexistuje"],
    "sv/no/da/fi": [
        r"sidan hittades inte", r"hittades inte", r"finns inte",
        r"ikke funnet", r"ikke fundet", r"finnes ikke", r"findes ikke",
        r"sivua ei löytynyt", r"ei löytynyt",
    ],
    "ro": [r"pagina nu a fost găsită", r"nu a fost găsit", r"nu există"],
    "el": [r"η σελίδα δεν βρέθηκε", r"δεν βρέθηκε", r"δεν υπάρχει"],
    "he": [r"הדף לא נמצא", r"לא נמצא", r"לא קיים"],
    "fa": [r"صفحه یافت نشد", r"یافت نشد", r"وجود ندارد"],
    "hu": [r"az oldal nem található", r"nem található", r"nem létezik"],
}
_SOFT404_STRONG = [p for pats in _SOFT404_STRONG_BY_LANG.values() for p in pats]

# Dấu hiệu YẾU: 1 từ đơn, dễ trùng nội dung thật -> CHỈ soi <title> và chỉ khi
# title NGẮN (trang lỗi thường tiêu đề cụt). Tránh báo nhầm bài báo kiểu
# "Manchester United player suspended for three matches...".
_SOFT404_WEAK = [
    r"suspended", r"unavailable", r"removed", r"deleted", r"gone",
    # Tiếng Việt: từ chỉ trạng thái chung chung, dễ trùng tiêu đề tin tức
    # ("Cầu thủ bị đình chỉ thi đấu...") -> chỉ tính khi title ngắn.
    r"bị khóa", r"bị khoá", r"đình chỉ", r"tạm ngưng",
    r"tạm khóa", r"tạm khoá", r"ngừng hoạt động",
    r"gesperrt", r"suspendu", r"suspendida", r"suspensa", r"sospeso",
    r"заблокирован", r"封禁", r"停用", r"정지", r"停止",
]
# Trang lỗi thật có tiêu đề rất cụt ("Account Suspended" 17, "Tài khoản bị khóa" 17).
# Tiêu đề tin tức hầu như luôn dài hơn -> 30 là ngưỡng an toàn, tránh báo nhầm.
_WEAK_TITLE_MAXLEN = 30

# ---- Trang CHỐNG BOT / CHẶN TRUY CẬP trả kèm HTTP 200 ----
# Cloudflare/DataDome/Imperva... hay trả 200 cùng trang "Just a moment" hoặc
# "Access Denied". Đây là site SỐNG nhưng chặn tool -> phải xếp "chặn",
# TUYỆT ĐỐI không xếp "chết" (nếu không sẽ báo die oan hàng loạt).
_BLOCK_PAGE_SIGNS = [
    r"just a moment", r"attention required", r"checking your browser",
    r"enable javascript and cookies", r"verify (?:you are|you're) (?:a )?human",
    r"are you a (?:robot|human)", r"human verification", r"bot (?:detection|protection)",
    r"ddos protection", r"security check", r"browser verification",
    r"browser not supported", r"unsupported browser", r"trình duyệt không được hỗ trợ",
    r"you have been blocked", r"unable to access this website",
    r"access denied", r"403 forbidden", r"\b403\b", r"error 10\d\d",
    r"rate limited", r"too many requests", r"\b429\b",
    r"restrict access", r"blocked by", r"firewall",
    r"datadome", r"perimeterx", r"incapsula", r"imperva", r"sucuri",
    r"captcha",
    # Ngôn ngữ khác
    r"访问被拒绝", r"安全验证", r"请稍候", r"人机验证",
    r"проверка браузера", r"доступ запрещ",
    r"truy cập bị từ chối", r"đang kiểm tra trình duyệt", r"xác minh bạn",
    r"acceso denegado", r"accès refusé", r"zugriff verweigert",
    r"アクセスが拒否", r"액세스가 거부",
]


def _is_html(ctype: str) -> bool:
    """Chỉ đọc body khi là HTML/text (bỏ qua ảnh, pdf, video... cho nhanh)."""
    if not ctype:
        return True                      # không khai báo -> cứ thử đọc
    c = ctype.lower()
    return c.startswith("text/") or "html" in c or "xml" in c


# ---- Chuyển hướng bằng <meta http-equiv="refresh"> ----
# Trang trả HTTP 200 nhưng lập tức tự nhảy sang URL khác. Trình duyệt đi theo,
# request thường thì không -> nếu bỏ qua sẽ chấm "sống" cho trang đã bị gỡ.
_META_TAG_RE = re.compile(
    r'<meta[^>]+http-equiv\s*=\s*["\']?refresh["\']?[^>]*>', re.I)
_META_CONTENT_RE = re.compile(r'content\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', re.I)
_META_URL_RE = re.compile(r'url\s*=\s*["\']?\s*([^"\';\s]+)', re.I)
_MAX_META_HOPS = 3


def find_meta_refresh(html: str, base_url: str) -> str:
    """Trả về URL đích của meta refresh (đã ghép tuyệt đối), rỗng nếu không có."""
    m = _META_TAG_RE.search(html or "")
    if not m:
        return ""
    mc = _META_CONTENT_RE.search(m.group(0))
    if not mc:
        return ""
    content = next((g for g in mc.groups() if g), "")
    mu = _META_URL_RE.search(content)
    if not mu:
        return ""
    try:
        return urljoin(base_url, mu.group(1).strip())
    except Exception:
        return ""


def _norm_link(link: str) -> str:
    """Chuẩn hoá URL để so sánh: bỏ scheme/www/slash cuối, host lowercase."""
    if not link:
        return ""
    if not re.match(r"^https?://", link, re.I):
        link = "http://" + link
    p = urlparse(link)
    host = re.sub(r"^www\.", "", p.netloc.lower())
    return f"{host}{p.path.rstrip('/')}"


def check_anchor(html: str, fragment: str):
    """Kiểm tra #fragment có tồn tại trong trang. None = không cần kiểm tra."""
    frag = unquote((fragment or "").strip())
    # Bỏ qua: #top, text-fragment (#:~:text=), và hashbang #!/... (định tuyến
    # SPA — không phải id phần tử nên kiểm tra sẽ luôn báo thiếu oan).
    if not frag or frag.lower() == "top" or frag.startswith((":~:", "!", "/")):
        return None
    esc = re.escape(frag)
    patterns = [
        rf'id\s*=\s*["\']{esc}["\']',
        rf'name\s*=\s*["\']{esc}["\']',
        rf'id\s*=\s*["\']user-content-{esc}["\']',   # GitHub thêm tiền tố này
        rf'id\s*=\s*{esc}[\s>]',                     # id không đặt trong nháy
    ]
    return any(re.search(p, html or "", re.I) for p in patterns)


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _page_signals(html: str):
    """Trả về (title, [h1..., og:title]) — nơi trang lỗi hay ghi lý do."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = _clean_text(m.group(1)) if m else ""
    others = []
    for i, m in enumerate(re.finditer(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)):
        if i >= 3:
            break
        others.append(_clean_text(m.group(1)))
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                  html, re.I | re.S)
    if m:
        others.append(_clean_text(m.group(1)))
    return title, [t for t in others if t]


def detect_block_page(html: str):
    """Trả về (True, tiêu_đề) nếu trang 200 thực chất là trang chống bot/chặn IP.

    Phải kiểm tra TRƯỚC soft 404: các trang này chứa chữ như "Access Denied",
    "403 Forbidden" rất dễ bị nhầm là trang chết, trong khi site vẫn sống.
    """
    title, others = _page_signals(html)
    for text in ([title] if title else []) + others:
        low = text.lower()
        for pat in _BLOCK_PAGE_SIGNS:
            if re.search(pat, low):
                return True, text[:90]
    return False, ""


def detect_soft_404(html: str):
    """Trả về (True, đoạn_tiêu_đề_khớp) nếu trang 200 thực chất là trang lỗi."""
    title, others = _page_signals(html)

    # Dấu hiệu MẠNH: soi cả title + H1 + og:title
    for text in ([title] if title else []) + others:
        low = text.lower()
        for pat in _SOFT404_STRONG:
            if re.search(pat, low):
                return True, text[:90]

    # Dấu hiệu YẾU: CHỈ soi <title> và chỉ khi title ngắn. Không soi H1 —
    # bài báo hay có H1 ngắn kiểu "Player suspended after red card" -> báo nhầm.
    if title and len(title) <= _WEAK_TITLE_MAXLEN:
        low = title.lower()
        for pat in _SOFT404_WEAK:
            if re.search(pat, low):
                return True, title[:90]
    return False, ""


def _bare_host(url: str) -> str:
    return re.sub(r"^www\.", "", (urlparse(url).hostname or "").lower())


def _redirected_to_home(orig: str, final: str) -> bool:
    """URL có đường dẫn nhưng bị đá về trang chủ CÙNG DOMAIN -> trang gốc đã gỡ.

    Bắt buộc cùng domain: chuyển hướng SANG DOMAIN KHÁC là đích đến có chủ đích
    (link rút gọn bit.ly/tinyurl, affiliate...) — đó là hoạt động ĐÚNG, không
    phải trang chết.
    """
    if not final or orig == final:
        return False
    if _bare_host(orig) != _bare_host(final):
        return False                            # khác domain -> bỏ qua
    po, pf = urlparse(orig), urlparse(final)
    path_o = po.path.strip("/").lower()
    if not path_o or pf.path.strip("/"):        # gốc vốn là trang chủ, hoặc đích vẫn có path
        return False
    if path_o in ("index.html", "index.htm", "index.php", "home", "default.aspx"):
        return False
    return True


@dataclass
class UrlResult:
    stt: int
    input: str             # dòng gốc người dùng nhập
    url: str               # URL đã chuẩn hoá (tự thêm https:// nếu thiếu)
    domain: str            # host để gom nhóm
    valid: bool            # định dạng URL hợp lệ
    alive: bool            # sống (2xx) hay không
    category: str          # "sống" | "redirect" | "chặn" | "chết" | "lỗi"
    status_code: int       # mã HTTP (0 nếu không kết nối được)
    final_url: str = ""    # URL cuối sau redirect (hoặc Location nếu 3xx)
    redirected: bool = False
    elapsed_ms: int = 0    # thời gian phản hồi
    content_type: str = ""
    note: str = ""         # mô tả lỗi / ghi chú
    redirect_chain: str = ""    # vd "301 → 302" (chuỗi mã đã đi qua)
    permanent: bool = False     # redirect TOÀN 301/308 -> vĩnh viễn (giữ link juice)
    meta_refresh: bool = False  # có nhảy bằng <meta refresh> không
    anchor: str = ""            # phần #fragment của URL (nếu có)
    anchor_ok: bool | None = None   # None = không kiểm tra; False = thiếu anchor


# Ký tự vô hình hay dính khi copy (zero-width, BOM)
_INVISIBLE = ("​", "‌", "‍", "﻿")
# Ký tự bao/dấu câu thừa hay dính ở 2 đầu khi copy: () [] {} <> « » , . ; : ! ? …
_WRAP = "<>()[]{}«»„“”‘’,.;:!?…"


def normalize_url(raw: str) -> tuple[str, bool, str]:
    """Chuẩn hoá + phát hiện URL sai định dạng / thừa ký tự khi paste.

    Tự dọn artifact copy-paste an toàn (nháy, ngoặc, dấu câu thừa 2 đầu, thiếu/
    thừa ':' '/'); gắn cờ lỗi rõ ràng cho ký tự lạ giữa URL, khoảng trắng, scheme
    gõ sai. Trả về (url_đã_dọn, valid, ly_do_loi).
    """
    s = raw or ""
    # 1) Bỏ ký tự vô hình; nháy/backtick không bao giờ hợp lệ trong URL -> bỏ hẳn
    for ch in _INVISIBLE:
        s = s.replace(ch, "")
    s = s.replace(" ", " ")               # non-breaking space -> space thường
    s = s.replace('"', "").replace("'", "").replace("`", "").strip()
    if not s:
        return "", False, "dòng rỗng"

    # 2) Bỏ ngoặc/dấu câu thừa ở 2 đầu, vd: "(https://a.com)," hay "https://a.com."
    s = s.strip(_WRAP + " ")
    if not s:
        return "", False, "chỉ chứa ký tự thừa (không có URL)"

    # 3) Khoảng trắng GIỮA URL = dán dư ký tự (vd "a.co m", "https:// a.com")
    if re.search(r"\s", s):
        return s, False, "URL chứa khoảng trắng — có thể dán dư/thiếu ký tự"

    # 4) Ký tự không được phép xuất hiện trong URL (còn sót ở giữa)
    stray = sorted({c for c in s if c in '<>{}|\\^`"\''})
    if stray:
        return s, False, "URL chứa ký tự lạ: " + " ".join(stray)

    # 5) Chuẩn hoá phần scheme: gộp mọi ':' '/' thừa/thiếu, chặn scheme gõ sai
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9]{1,6})[:/]{1,4}(.*)$", s)
    if m:                                       # có phần scheme (đúng hoặc lộn xộn)
        scheme, rest = m.group(1).lower(), m.group(2)
        if scheme not in ("http", "https"):
            return s, False, (f"scheme sai chính tả: '{scheme}' "
                              f"(đúng phải là http:// hoặc https://)")
        s = f"{scheme}://{rest}"               # dựng lại đúng 'scheme://...'
    else:
        s = "https://" + s                      # thiếu scheme -> mặc định https

    # 6) Kiểm tra tên miền
    try:
        p = urlparse(s)
    except Exception:
        return s, False, "URL không phân tích được"
    host = (p.hostname or "").strip()
    if not host:
        return s, False, "thiếu tên miền"
    if ".." in host:
        return s, False, "tên miền có dấu chấm thừa (..)"
    if "." not in host and host != "localhost":
        return s, False, "tên miền không hợp lệ (thiếu phần .com/.vn...)"
    return s, True, ""


def _history_codes(r) -> list:
    """Danh sách mã của các bước redirect đã đi qua, vd [301, 302]."""
    out = []
    for h in (getattr(r, "history", None) or []):
        code = getattr(h, "status_code", None)
        if isinstance(code, int):
            out.append(code)
    return out


def _read_body(r, engine: str, limit: int = _BODY_LIMIT) -> str:
    """Đọc TỐI ĐA `limit` ký tự body (đủ để soi title/H1), tránh tải cả trang nặng."""
    try:
        if engine == "cffi":
            return (r.text or "")[:limit]
        chunks, size = [], 0
        for chunk in r.iter_content(chunk_size=16384):
            if not chunk:
                continue
            chunks.append(chunk)
            size += len(chunk)
            if size >= limit:
                break
        return b"".join(chunks).decode(r.encoding or "utf-8", errors="ignore")
    except Exception:
        return ""


def _probe_once(engine: str, url: str, timeout: int, follow: bool,
                want_body: bool = False):
    """Gửi 1 request bằng 1 engine.

    Trả về (status, final_url, redirected, ctype, body). `body` chỉ được đọc khi
    want_body=True và phản hồi là 2xx dạng HTML (để dò soft 404).
    """
    body = ""
    if engine == "cffi":
        r = cffi_requests.get(url, impersonate="chrome", timeout=timeout,
                              allow_redirects=follow)
        try:
            status = r.status_code
            final = getattr(r, "url", "") or url
            hist = _history_codes(r)
            # curl_cffi KHÔNG trả history (luôn rỗng), chỉ có redirect_count.
            rcount = int(getattr(r, "redirect_count", 0) or 0) or len(hist)
            ctype = (r.headers.get("Content-Type", "") or "").split(";")[0].strip()
            location = r.headers.get("Location", "") if not follow else ""
            if want_body and 200 <= status < 300 and _is_html(ctype):
                body = _read_body(r, engine)
        finally:
            close = getattr(r, "close", None)
            if close:
                close()
        # Biết CÓ redirect nhưng không rõ mã -> hỏi thêm 1 request không follow
        # để lấy mã bước đầu (chỉ tốn thêm với URL thật sự có redirect).
        if follow and rcount > 0 and not hist:
            try:
                r0 = cffi_requests.get(url, impersonate="chrome", timeout=timeout,
                                       allow_redirects=False)
                try:
                    if 300 <= r0.status_code < 400:
                        hist = [r0.status_code]
                finally:
                    c0 = getattr(r0, "close", None)
                    if c0:
                        c0()
            except Exception:
                pass
        return status, final, bool(rcount), ctype, body, hist, rcount
    else:
        session = (cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False})
            if engine == "cloud" else requests)
        r = session.get(url, headers=_HEADERS, timeout=timeout,
                        allow_redirects=follow, stream=True)
        try:
            status = r.status_code
            final = r.url or url
            hist = _history_codes(r)
            ctype = (r.headers.get("Content-Type", "") or "").split(";")[0].strip()
            location = r.headers.get("Location", "") if not follow else ""
            if want_body and 200 <= status < 300 and _is_html(ctype):
                body = _read_body(r, engine)
        finally:
            r.close()
    if not follow and 300 <= status < 400 and location:
        final = location            # 3xx không follow -> báo đích Location
    return status, final, bool(hist), ctype, body, hist, len(hist)


def _probe_engines(url: str, timeout: int, anti_bot: bool, follow: bool,
                   want_body: bool):
    """Thử lần lượt các engine. Trả về (status, final, redirected, ctype, err, body, hist)."""
    engines = []
    if anti_bot and _CFFI:
        engines.append("cffi")
    if anti_bot and _CLOUDSCRAPER:
        engines.append("cloud")
    engines.append("requests")

    fallback = None   # kết quả HTTP thật đầu tiên (kể cả 4xx/5xx) để dùng nếu 2xx không có
    last_err = ""
    for eng in engines:
        try:
            status, final, redirected, ctype, body, hist, rcount = _probe_once(
                eng, url, timeout, follow, want_body)
            if 200 <= status < 400:          # sống/redirect -> chốt luôn
                return status, final, redirected, ctype, "", body, hist, rcount
            if fallback is None:             # 4xx/5xx: giữ lại, biết đâu engine khác qua được
                fallback = (status, final, redirected, ctype, "", "", hist, rcount)
            last_err = f"HTTP {status}"
        except Exception as e:
            last_err = f"{eng}: {str(e)[:80]}"
    if fallback is not None:
        return fallback
    return 0, url, False, "", last_err or "không kết nối được", "", [], 0


def _http_probe(url: str, timeout: int, anti_bot: bool, follow: bool,
                want_body: bool = False, retries: int = 1):
    """Như _probe_engines nhưng thử lại khi lỗi mạng / 5xx.

    KHÔNG thử lại 4xx: 404 là kết luận chắc chắn, retry chỉ tốn thời gian.
    Chỉ lỗi tạm thời (timeout, đứt kết nối, 5xx) mới đáng thử lại.
    """
    result = _probe_engines(url, timeout, anti_bot, follow, want_body)
    for _ in range(max(0, retries)):
        status = result[0]
        if not (status == 0 or 500 <= status < 600):
            break                            # 2xx/3xx/4xx -> khỏi thử lại
        time.sleep(1.0)
        result = _probe_engines(url, timeout, anti_bot, follow, want_body)
    return result


def _chain_info(hist: list, rcount: int = 0) -> tuple[str, bool]:
    """(mô tả chuỗi redirect, có phải vĩnh viễn toàn bộ không).

    301/308 = vĩnh viễn (giữ được link juice); 302/303/307 = tạm thời.
    `rcount` là tổng số bước; nếu biết ít mã hơn số bước (curl_cffi không trả
    đủ history) thì ghi rõ còn bước chưa rõ và KHÔNG dám khẳng định vĩnh viễn.
    """
    steps = rcount or len(hist)
    if not steps:
        return "", False
    if not hist:
        return f"{steps} bước (không rõ mã)", False
    chain = " → ".join(str(c) for c in hist)
    if len(hist) < steps:               # chỉ biết mã bước đầu
        return f"{chain} → …({steps} bước)", False
    return chain, all(c in (301, 308) for c in hist)


def check_one(stt: int, line: str, timeout: int = 20, anti_bot: bool = True,
              follow: bool = True, soft404: bool = True,
              retries: int = 1) -> UrlResult:
    url, valid, reason = normalize_url(line)
    domain = urlparse(url).hostname or "" if url else ""
    if not valid:
        return UrlResult(stt, line, url, domain, False, False, "lỗi", 0,
                         note=reason)
    fragment = urlparse(url).fragment

    t0 = time.perf_counter()
    status, final, redirected, ctype, err, body, hist, rcount = _http_probe(
        url, timeout, anti_bot, follow, want_body=soft404, retries=retries)

    # --- Theo <meta http-equiv="refresh"> (trình duyệt đi theo, request thì không) ---
    meta_hit = False
    meta_target = find_meta_refresh(body, final) if (soft404 and follow and body) else ""
    hops = 0
    while meta_target and _norm_link(meta_target) != _norm_link(final) \
            and hops < _MAX_META_HOPS:
        meta_hit, hops = True, hops + 1
        status, final, _rd, ctype, err, body, hist2, rc2 = _http_probe(
            meta_target, timeout, anti_bot, follow, want_body=soft404, retries=retries)
        hist, rcount = hist + hist2, rcount + rc2
        if not (200 <= status < 300) or not body:
            break
        meta_target = find_meta_refresh(body, final)
    redirected = redirected or meta_hit
    chain, permanent = _chain_info(hist, rcount)
    elapsed = int((time.perf_counter() - t0) * 1000)

    base = dict(final_url=final, redirected=redirected, elapsed_ms=elapsed,
                content_type=ctype, redirect_chain=chain, permanent=permanent,
                meta_refresh=meta_hit, anchor=fragment)

    if status == 0:
        return UrlResult(stt, line, url, domain, True, False, "lỗi", 0,
                         **base, note=err)
    if 200 <= status < 300:
        # HTTP 200 CHƯA chắc trang còn sống: rất nhiều site trả 200 kèm nội dung
        # "not found" / "Account Suspended" (soft 404) -> phải soi nội dung.
        if soft404:
            # 1) Trang chống bot/chặn IP trả 200 -> "chặn" (site sống). Phải xét
            #    TRƯỚC soft 404, vì các trang này hay chứa chữ "Access Denied".
            is_block, matched = detect_block_page(body)
            if is_block:
                return UrlResult(stt, line, url, domain, True, True, "chặn", status,
                                 **base,
                                 note=f'trang chống bot/chặn truy cập (HTTP {status}) — "{matched}"')
            # 2) Mới tới lượt soft 404 (trang thật sự đã bị gỡ/khóa)
            is_soft, matched = detect_soft_404(body)
            if is_soft:
                return UrlResult(stt, line, url, domain, True, False, "chết", status,
                                 **base,
                                 note=f'soft 404: HTTP {status} nhưng trang báo lỗi — "{matched}"')
            if _redirected_to_home(url, final):
                return UrlResult(stt, line, url, domain, True, False, "chết", status,
                                 **base,
                                 note=f"bị đá về trang chủ ({final}) — trang gốc nhiều khả năng đã gỡ")

        # Trang sống. Kiểm tra thêm #anchor có thật sự tồn tại không.
        anchor_ok = check_anchor(body, fragment) if (soft404 and body) else None
        notes = []
        if meta_hit:
            notes.append(f"meta refresh → {final}")
        elif redirected and final != url:
            notes.append(f"→ {final}")
        if chain:
            notes.append("301 vĩnh viễn" if permanent else f"redirect tạm ({chain})")
        if anchor_ok is False:
            notes.append(f"⚠️ không tìm thấy anchor #{fragment} trên trang")
        return UrlResult(stt, line, url, domain, True, True, "sống", status,
                         **base, anchor_ok=anchor_ok, note=" · ".join(notes))
    if 300 <= status < 400:   # chỉ xảy ra khi follow=False
        kind = "vĩnh viễn (301/308)" if status in (301, 308) else "tạm thời"
        return UrlResult(stt, line, url, domain, True, True, "redirect", status,
                         **{**base, "redirected": True,
                            "permanent": status in (301, 308)},
                         note=f"chuyển hướng {kind} → {final}")
    if status in _BLOCK_CODES:
        # Server có phản hồi nhưng chặn/giới hạn tool -> KHÔNG tính là chết (dương tính giả).
        return UrlResult(stt, line, url, domain, True, True, "chặn", status,
                         **base, note=_BLOCK_REASON[status])
    # 4xx / 5xx còn lại = trang thật sự chết (404 không tồn tại, 5xx server lỗi...)
    return UrlResult(stt, line, url, domain, True, False, "chết", status,
                     **base, note=f"HTTP {status}")


def check_bulk(lines, timeout: int = 20, anti_bot: bool = True, follow: bool = True,
               max_workers: int = 8, progress=None, soft404: bool = True,
               retries: int = 1):
    """Chạy song song. progress(done, total, msg) gọi sau mỗi dòng xong."""
    items = [(i + 1, ln) for i, ln in enumerate(lines) if ln.strip()]
    total = len(items)
    results: list[UrlResult] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        futures = {
            ex.submit(check_one, stt, ln, timeout, anti_bot, follow,
                      soft404, retries): (stt, ln)
            for stt, ln in items
        }
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if progress:
                progress(done, total, f"Đã check {done}/{total}")
    results.sort(key=lambda r: r.stt)
    return results
