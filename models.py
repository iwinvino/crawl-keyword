"""Các kiểu dữ liệu dùng chung trong pipeline."""
from dataclasses import dataclass


@dataclass
class ImageHit:
    """Một ảnh tìm được từ Serper Image Search."""
    seed_keyword: str   # từ khóa gốc người dùng nhập
    title: str
    image_url: str
    page_url: str       # link trang chứa ảnh
    source: str         # tên site
    domain: str
    position: int


@dataclass
class PageHit:
    """Một trang indexed lấy từ truy vấn site:domain."""
    domain: str
    title: str
    link: str
    snippet: str
    position: int


@dataclass
class KeywordRecord:
    """Một keyword trích xuất được, kèm nguồn gốc."""
    keyword: str
    domain: str
    source_url: str
    origin: str         # serp_title | serp_snippet | yake | meta_keywords | title | h1 ...
    seed_keyword: str
