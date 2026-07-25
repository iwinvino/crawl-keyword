"""Điều phối toàn bộ luồng: ảnh -> domain -> site: -> keyword."""
from concurrent.futures import ThreadPoolExecutor, as_completed

import extractors as ex
from cache import FileCache
from config import DEFAULT_BLACKLIST
from filters import DomainFilter
from models import KeywordRecord
from providers import SerperProvider


class Pipeline:
    def __init__(self, settings, provider=None, domain_filter=None, use_cache=True):
        self.settings = settings
        self.cache = FileCache(settings.cache_dir, enabled=use_cache)
        self.provider = provider or SerperProvider(
            settings.serper_api_keys, settings.serper_gl,
            settings.serper_hl, settings.timeout, self.cache)
        self.filter = domain_filter or DomainFilter(blacklist=DEFAULT_BLACKLIST)

    def run(self, keywords, num_images=20, pages_per_domain=20, do_scrape=True,
            scrape_limit=3, use_yake=True, lang="vi", progress=None,
            crawl_internal=True, internal_link_limit=10,
            vi_only=True, vi_threshold=0.02,
            topic_filter=False, topic_terms=None, anti_bot=True,
            max_keyword_words=6):

        def report(msg, frac=None):
            if progress:
                progress(msg, frac)

        workers = self.settings.max_workers

        # 1) Tìm ảnh theo từng từ khóa
        report("Đang tìm ảnh theo từ khóa...", 0.05)
        image_hits = []
        with ThreadPoolExecutor(max_workers=workers) as exr:
            futs = {exr.submit(self.provider.image_search, kw, num_images): kw
                    for kw in keywords}
            for fut in as_completed(futs):
                kw = futs[fut]
                try:
                    image_hits.extend(ex.parse_image_results(fut.result(), kw))
                except Exception as e:
                    report(f"⚠️ Lỗi image search '{kw}': {e}")
        report(f"Tìm thấy {len(image_hits)} ảnh.", 0.3)

        # 2) Gom domain (dedup + lọc nhiễu)
        domain_pages = {}
        seed_for_domain = {}
        for h in image_hits:
            if not h.domain or not self.filter.allowed(h.domain):
                continue
            domain_pages.setdefault(h.domain, set()).add(h.page_url)
            seed_for_domain.setdefault(h.domain, h.seed_keyword)
        domains = sorted(domain_pages)
        report(f"{len(domains)} domain sau khi lọc.", 0.35)

        # 3) site:domain để lấy trang indexed
        report("Đang lấy trang indexed (site:)...", 0.4)
        all_pages = []
        with ThreadPoolExecutor(max_workers=workers) as exr:
            futs = {exr.submit(self.provider.web_search, f"site:{d}", pages_per_domain): d
                    for d in domains}
            for fut in as_completed(futs):
                d = futs[fut]
                try:
                    all_pages.extend(ex.parse_web_results(fut.result(), d))
                except Exception as e:
                    report(f"⚠️ Lỗi site:{d}: {e}")
        report(f"Thu {len(all_pages)} trang indexed.", 0.6)

        # 4) Trích keyword từ SERP (title + snippet + YAKE)
        records = []
        by_domain = {}
        for p in all_pages:
            by_domain.setdefault(p.domain, []).append(p)

        # 4b) Lọc domain ngoại ngữ: bỏ domain có nội dung không phải tiếng Việt
        if vi_only and by_domain:
            kept = {}
            dropped = []
            for d, pages in by_domain.items():
                text = " ".join(f"{p.title} {p.snippet}" for p in pages)
                if ex.vietnamese_ratio(text) >= vi_threshold:
                    kept[d] = pages
                else:
                    dropped.append(d)
            by_domain = kept
            domains = sorted(by_domain)
            if dropped:
                report(f"Bỏ {len(dropped)} domain ngoại ngữ: {', '.join(dropped)}", None)
            report(f"Còn {len(domains)} domain tiếng Việt.", 0.65)

        for d, pages in by_domain.items():
            records.extend(ex.keywords_from_pages(
                pages, seed_for_domain.get(d, ""), lang=lang, use_yake=use_yake,
                vi_only=vi_only, vi_threshold=vi_threshold))

        # 5) Scrape wave 1: trang gốc (top trang indexed) + thu internal link
        scrape_errors = []
        scraped_urls = set()
        internal_candidates = {}   # domain -> list[(url, anchor, seed)]
        if do_scrape and by_domain:
            report("Đang scrape trang gốc...", 0.7)
            first_targets = []
            for d, pages in by_domain.items():
                for p in pages[:scrape_limit]:
                    if p.link and p.link not in scraped_urls:
                        first_targets.append((d, p.link, seed_for_domain.get(d, "")))
                        scraped_urls.add(p.link)
            with ThreadPoolExecutor(max_workers=workers) as exr:
                futs = {exr.submit(ex.scrape_page, url, self.settings.timeout,
                                   crawl_internal, anti_bot):
                        (d, url, seed) for d, url, seed in first_targets}
                for fut in as_completed(futs):
                    d, url, seed = futs[fut]
                    try:
                        items, links, err = fut.result()
                        if err:
                            scrape_errors.append((url, err))
                        # bỏ trang ngoại ngữ (trang gốc tiếng Ý/Anh của site spam)
                        if vi_only and items and not ex.is_vietnamese(
                                " ".join(t for _o, t in items), vi_threshold):
                            continue
                        records.extend(ex.records_from_scraped(
                            items, d, url, seed, prefix="",
                            use_yake=use_yake, lang=lang))
                        for lurl, anchor in links:
                            if anchor and ex.is_useful_anchor(anchor):
                                records.append(KeywordRecord(
                                    keyword=anchor, domain=d, source_url=lurl,
                                    origin="anchor_text", seed_keyword=seed))
                            internal_candidates.setdefault(d, []).append((lurl, anchor, seed))
                    except Exception as e:
                        scrape_errors.append((url, str(e)))

        # 5b) Scrape wave 2: crawl các internal link để lấy thêm keyword
        if do_scrape and crawl_internal and internal_candidates:
            report("Đang crawl internal link...", 0.82)
            second_targets = []
            for d, cands in internal_candidates.items():
                # ưu tiên link có anchor hữu ích, dedup, giới hạn / domain
                cands_sorted = sorted(
                    cands, key=lambda c: 0 if (c[1] and ex.is_useful_anchor(c[1])) else 1)
                picked = 0
                for lurl, _anchor, seed in cands_sorted:
                    if lurl in scraped_urls:
                        continue
                    scraped_urls.add(lurl)
                    second_targets.append((d, lurl, seed))
                    picked += 1
                    if picked >= internal_link_limit:
                        break
            with ThreadPoolExecutor(max_workers=workers) as exr:
                futs = {exr.submit(ex.scrape_page, url, self.settings.timeout, False, anti_bot):
                        (d, url, seed) for d, url, seed in second_targets}
                for fut in as_completed(futs):
                    d, url, seed = futs[fut]
                    try:
                        items, _links, err = fut.result()
                        if err:
                            scrape_errors.append((url, err))
                        if vi_only and items and not ex.is_vietnamese(
                                " ".join(t for _o, t in items), vi_threshold):
                            continue
                        records.extend(ex.records_from_scraped(
                            items, d, url, seed, prefix="internal_",
                            use_yake=use_yake, lang=lang))
                    except Exception as e:
                        scrape_errors.append((url, str(e)))

        # 6) Tổng hợp + lọc chủ đề
        report("Tổng hợp kết quả...", 0.9)
        rows = ex.aggregate(records, max_keyword_words=max_keyword_words)
        total_before_topic = len(rows)
        if topic_filter:
            rows = ex.filter_by_topic(rows, topic_terms)
            report(f"Lọc chủ đề: giữ {len(rows)}/{total_before_topic} keyword.", None)
        report("Hoàn tất.", 1.0)

        stats = self.provider.stats_summary() if hasattr(self.provider, "stats_summary") else []
        return {
            "rows": rows,
            "records": records,
            "image_hits": image_hits,
            "domains": domains,
            "credits_used": getattr(self.provider, "credits_used", 0),
            "scrape_errors": scrape_errors,
            "total_before_topic": total_before_topic,
            "key_stats": stats,
        }
