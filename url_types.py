"""Nhận diện LOẠI domain nguồn để bỏ qua không cần check.

Nhiều URL trong danh sách backlink không phải trang nội dung thật mà là dịch vụ
trung gian: link rút gọn, trang "link in bio", paste/pad công khai, bài đăng
nhanh (Telegraph). Check sống/chết hay đếm link trỏ ra trên các trang này không
có ý nghĩa (chúng vốn sinh ra để chứa link, và hầu hết render bằng JS) → chỉ cần
**ghi nhận nó là dạng gì rồi bỏ qua**, khỏi tốn request.

Thêm domain mới = thêm 1 dòng vào các set dưới đây. Người dùng cũng có thể tự
nhập thêm domain trong giao diện (tham số `extra`).
"""
from __future__ import annotations

import re

from link_extractor import norm_domain, root_domain

# ---- Link rút gọn (redirect thuần) ----
SHORTENERS = {
    "bit.ly", "bitly.com", "j.mp", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
    "is.gd", "v.gd", "cutt.ly", "shorturl.at", "rb.gy", "rebrand.ly", "s.id",
    "tiny.cc", "bl.ink", "shrtco.de", "gg.gg", "zzb.bz", "urlz.fr", "n9.cl",
    "clck.ru", "vurl.com", "soo.gd", "t.ly", "shorte.st", "adf.ly", "linktw.in",
    "1link.vn", "url.vn", "yun.ir", "u.to", "qr.ae", "lnkd.in", "buff.ly",
    "trib.al", "dlvr.it", "ift.tt", "flip.it", "wa.link", "short.gy", "kutt.it",
}

# ---- Trang "link in bio" (chỉ chứa danh sách link) ----
BIO_LINKS = {
    "linktr.ee", "bio.link", "bio.site", "beacons.ai", "allmylinks.com",
    "many.link", "manylink.co", "lit.link", "magic.ly", "linksta.cc",
    "linkmix.co", "mez.ink", "joy.bio", "jaga.link", "biolinku.co", "taplink.cc",
    "campsite.bio", "carrd.co", "solo.to", "hoo.be", "lnk.bio", "linkr.bio",
    "milkshake.app", "znap.link", "komi.io", "direct.me", "tap.bio", "shor.by",
    "linkpop.com", "later.com", "contactinbio.com", "flowpage.com", "pory.app",
    "sowl.co", "linkin.bio", "withkoji.com", "snipfeed.co", "liinks.co",
    "album.link", "song.link", "ffm.to", "linkfire.com", "toneden.io",
}

# ---- Paste / pad / ghi chú công khai ----
PASTES = {
    "pastebin.com", "justpaste.it", "paste.ee", "rentry.co", "notes.io",
    "controlc.com", "hastebin.com", "dpaste.org", "pastelink.net", "pasteio.com",
    "ideone.com", "codepen.io", "jsfiddle.net", "paste.org", "textbin.net",
    "anotepad.com", "notepin.co", "telescript.denniskubes.com",
}

# ---- Bài đăng nhanh (Telegraph & tương tự) ----
QUICK_POSTS = {"telegra.ph", "te.legra.ph", "graph.org"}

# Pad tự host (HedgeDoc / CodiMD / Etherpad): pad.stuve.de, md.chaosdorf.de...
_PAD_HOST_RE = re.compile(r"^(?:pad|md|hedgedoc|codimd|etherpad|demo\.hedgedoc)\.", re.I)
_PAD_PATH_RE = re.compile(r"^/(?:s|p)/[A-Za-z0-9_-]{6,}$")

LABELS = {
    "short": "🔗 link rút gọn",
    "bio": "🪪 trang link-in-bio",
    "paste": "📋 paste/ghi chú công khai",
    "pad": "📝 pad công khai (HedgeDoc/CodiMD)",
    "post": "📰 bài đăng nhanh (Telegraph...)",
    "custom": "⏭️ do bạn khai báo",
}


def _host_path(url: str) -> tuple[str, str]:
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", (url or "").strip(), flags=re.I)
    host, _, rest = s.partition("/")
    return host.lower().split("@")[-1].split(":")[0], "/" + rest.split("?")[0].split("#")[0]


def classify(url: str, extra=()) -> tuple[str, str]:
    """Trả về (mã_loại, nhãn) nếu URL thuộc dịch vụ nên bỏ qua; ('', '') nếu không.

    extra: danh sách domain người dùng tự nhập thêm (mỗi phần tử là domain/URL).
    """
    if not url:
        return "", ""
    host, path = _host_path(url)
    root = root_domain(host)
    if not root:
        return "", ""

    disp = re.sub(r"^www\.", "", host)            # tên hiện trong nhãn
    extra_roots = {norm_domain(d) for d in extra if str(d).strip()} - {""}
    if root in extra_roots:
        return "custom", f'{LABELS["custom"]} ({disp})'
    if root in SHORTENERS:
        return "short", f'{LABELS["short"]} ({disp})'
    if root in BIO_LINKS:
        return "bio", f'{LABELS["bio"]} ({disp})'
    if root in PASTES:
        return "paste", f'{LABELS["paste"]} ({disp})'
    if root in QUICK_POSTS or host in QUICK_POSTS:
        return "post", f'{LABELS["post"]} ({disp})'
    if _PAD_HOST_RE.match(host) and _PAD_PATH_RE.match(path):
        return "pad", f'{LABELS["pad"]} ({disp})'
    return "", ""
