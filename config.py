"""Cấu hình & nạp biến môi trường."""
import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# Các domain nhiễu mặc định (ảnh stock, mạng xã hội, marketplace...)
DEFAULT_BLACKLIST = [
    "pinterest.", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "reddit.com", "wikipedia.org", "wikimedia.org",
    "shutterstock.com", "istockphoto.com", "gettyimages.", "alamy.com",
    "dreamstime.com", "123rf.com", "depositphotos.com", "freepik.com",
    "vecteezy.com", "unsplash.com", "pexels.com", "pixabay.com",
    "amazon.", "ebay.", "aliexpress.", "linkedin.com", "fandom.com",
    "quora.com", "slideshare.net",
]

# Từ khóa chủ đề iGaming (mặc định) — keyword phải chứa ít nhất 1 cụm này.
# Ưu tiên cụm dài/đặc trưng để tránh khớp nhầm (substring, không phân biệt hoa thường).
DEFAULT_IGAMING_TERMS = [
    "nhà cái", "nha cai", "cá cược", "ca cuoc", "cá độ", "ca do", "cược", "cuoc",
    "kèo", "keo nha cai", "soi kèo", "soi keo", "tỷ lệ kèo", "ty le keo",
    "casino", "sòng bài", "song bai", "nổ hũ", "no hu", "xóc đĩa", "xoc dia",
    "tài xỉu", "tai xiu", "bắn cá", "ban ca", "game bài", "game bai",
    "đánh bài", "danh bai", "slot", "quay hũ", "quay hu", "jackpot",
    "lô đề", "lo de", "xổ số", "xo so", "baccarat", "roulette", "poker",
    "blackjack", "sicbo", "đá gà", "da ga", "trực tiếp bóng đá", "truc tiep bong da",
    "world cup", "esport", "e-sport", "thể thao trực tuyến", "the thao truc tuyen",
    "khuyến mãi", "khuyen mai", "nạp tiền", "nap tien", "rút tiền", "rut tien",
    "đổi thưởng", "doi thuong", "tặng thưởng", "tien thuong", "tiền thưởng",
    "link vào", "link vao", "đăng ký", "dang ky nhan", "uy tín", "uy tin",
    "888", "789", "8xbet", "fun88", "w88", "v9bet", "12bet", "bk8", "mu88",
    "kubet", "ku casino", "sunwin", "go88", "b52", "iwin", "rik",
]


def _parse_keys(raw: str):
    """Tách nhiều key từ chuỗi (phân tách bởi , ; hoặc xuống dòng)."""
    parts = re.split(r"[,;\n\r]+", raw or "")
    return [p.strip() for p in parts if p.strip()]


@dataclass
class Settings:
    serper_api_keys: list = field(
        default_factory=lambda: _parse_keys(os.getenv("SERPER_API_KEY", "")))
    serper_gl: str = os.getenv("SERPER_GL", "vn")          # quốc gia (site tiếng Việt)
    serper_hl: str = os.getenv("SERPER_HL", "vi")          # ngôn ngữ
    timeout: int = int(os.getenv("HTTP_TIMEOUT", "20"))
    max_workers: int = int(os.getenv("MAX_WORKERS", "8"))
    cache_dir: str = os.getenv("CACHE_DIR", ".cache")


settings = Settings()
