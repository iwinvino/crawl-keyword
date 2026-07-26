"""Giao diện Streamlit: Crawl Keyword đối thủ + Check Index hàng loạt (Serper).

Bố cục: mỗi tính năng 1 TAB trên cùng + tab ⚙️ Cài đặt (quản lý key / Service Account).
Cấu hình của từng tính năng nằm ngay trong tab của nó — KHÔNG dùng sidebar.
"""
import os
import re
from datetime import datetime

import pandas as pd
import streamlit as st

import exporters
import index_checker
import index_pusher
import key_store
import url_checker
from cache import FileCache
from config import DEFAULT_BLACKLIST, DEFAULT_IGAMING_TERMS, settings
from filters import DomainFilter
from pipeline import Pipeline
from providers import SerperProvider

st.set_page_config(page_title="SEO iGaming Toolkit", page_icon="🎰", layout="wide")


def get_secret(name: str):
    """Đọc biến cấu hình khi deploy web: biến môi trường -> st.secrets -> None."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        return st.secrets[name]
    except Exception:
        return None


# ---------------- Khóa mật khẩu truy cập (deploy web) ----------------
# Đặt APP_PASSWORD trong Secrets để chặn người lạ. Bỏ trống = không khóa (chạy local).
_app_pw = get_secret("APP_PASSWORD")
if _app_pw and not st.session_state.get("auth_ok"):
    st.title("🎰 SEO iGaming Toolkit")
    st.text_input("🔒 Mật khẩu truy cập", type="password", key="pw_in")
    if st.button("Đăng nhập", type="primary"):
        if st.session_state.get("pw_in") == str(_app_pw):
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Sai mật khẩu.")
    st.stop()


# ---------------- Quản lý API key (lưu lâu dài + key chung qua Secrets) ----------------
if "keys" not in st.session_state:
    loaded = key_store.load_keys()
    if not loaded:  # lần đầu / server mới: nạp key CHUNG từ .env hoặc Secrets
        seed = list(settings.serper_api_keys)
        if not seed:
            raw = get_secret("SERPER_API_KEY")
            if raw:
                seed = [k.strip() for k in re.split(r"[,;\n\r]+", str(raw)) if k.strip()]
        if seed:
            loaded = [{"label": "key chung", "key": k, "dead": False, "status": "chưa test"}
                      for k in seed]
            key_store.save_keys(loaded)
    st.session_state["keys"] = loaded


def live_keys() -> list[str]:
    """Danh sách chuỗi key còn sống (chưa bị đánh dấu chết) để chạy job."""
    return [e["key"] for e in st.session_state["keys"] if not e["dead"]]


def sync_dead_from_provider(provider) -> None:
    """Sau khi chạy: đánh dấu (KHÔNG xóa) các key provider phát hiện đã chết."""
    state = getattr(provider, "key_state", {})
    changed = False
    for e in st.session_state["keys"]:
        s = state.get(e["key"])
        if s and s.get("dead") and not e["dead"]:
            e["dead"] = True
            e["status"] = s.get("status", "hết credit/lỗi")
            changed = True
    if changed:
        key_store.save_keys(st.session_state["keys"])


def render_key_manager() -> None:
    """Khối quản lý key: thêm/xóa/đặt tên/test."""
    keys = st.session_state["keys"]
    alive = sum(1 for e in keys if not e["dead"])
    st.subheader("🔑 Quản lý Serper Key")
    st.caption(f"{alive}/{len(keys)} key còn sống · key lưu trong `keys.json` (đã gitignore)")

    with st.form("add_key_form", clear_on_submit=True):
        nl = st.text_input("Nhãn (tùy chọn)", placeholder="vd: key chính")
        nk = st.text_input("API key mới", type="password")
        a, b = st.columns(2)
        add = a.form_submit_button("➕ Thêm", use_container_width=True)
        test_all = b.form_submit_button("🧪 Test tất cả", use_container_width=True,
                                        help="Mỗi key tốn 1 credit để kiểm tra.")
    if add:
        if nk.strip():
            keys.append({"label": nl.strip(), "key": nk.strip(),
                         "dead": False, "status": "chưa test"})
            key_store.save_keys(keys)
            st.rerun()
        else:
            st.warning("Chưa nhập key.")
    if test_all and keys:
        with st.spinner("Đang test các key..."):
            for e in keys:
                _, msg, dead = key_store.test_key(e["key"])
                e["status"], e["dead"] = msg, dead
        key_store.save_keys(keys)
        st.rerun()

    if not keys:
        st.info("Chưa có key nào — thêm key ở trên.")
        return

    dead_labels = []
    with st.expander(f"📋 Danh sách key ({len(keys)})", expanded=True):
        for i, e in enumerate(keys):
            badge = "🟢" if not e["dead"] else "🔴"
            name = e["label"] or key_store.mask(e["key"])
            col_info, col_t, col_d = st.columns([6, 1.3, 1.3])
            col_info.markdown(
                f"{badge} **{name}** · `{key_store.mask(e['key'])}`  \n"
                f"<sub>{e['status']}</sub>", unsafe_allow_html=True)
            if col_t.button("🧪", key=f"test_{i}", help="Test key này (tốn 1 credit)"):
                _, msg, dead = key_store.test_key(e["key"])
                e["status"], e["dead"] = msg, dead
                key_store.save_keys(keys)
                st.rerun()
            if col_d.button("🗑️", key=f"del_{i}", help="Xóa key này"):
                keys.pop(i)
                key_store.save_keys(keys)
                st.rerun()
            if e["dead"]:
                dead_labels.append(name)
    if dead_labels:
        st.warning("Key đã chết (nên xóa): " + ", ".join(dead_labels))


# ---------------- Quản lý Service Account (Đẩy Index) ----------------
if "sas" not in st.session_state:
    st.session_state["sas"] = index_pusher.load_accounts()


def render_sa_manager() -> None:
    """Khối quản lý Service Account: thêm (JSON) / xóa / xem quota."""
    sas = st.session_state["sas"]
    alive = sum(1 for a in sas if not a["dead"])
    remain = index_pusher.remaining_quota(sas)
    st.subheader("🔐 Service Account (Google) — cho Đẩy Index")
    st.caption(f"{alive}/{len(sas)} SA sống · còn ~**{remain}** URL đẩy được hôm nay · "
               f"lưu trong `service_accounts.json` (đã gitignore)")

    up = st.file_uploader(
        "Thả file Service Account JSON (chọn nhiều được)",
        type="json", accept_multiple_files=True, key="sa_upload")
    lbl = st.text_input("Nhãn cho SA vừa thêm (tùy chọn)", key="sa_label")
    if st.button("➕ Thêm SA đã chọn", use_container_width=True):
        added, errs = 0, []
        emails = {a["email"] for a in sas}
        for f in up or []:
            try:
                acc = index_pusher.parse_account(f.getvalue(), lbl.strip())
                if acc["email"] in emails:
                    errs.append(f"{acc['email']} (đã có)")
                    continue
                sas.append(acc)
                emails.add(acc["email"])
                added += 1
            except Exception as e:
                errs.append(f"{f.name}: {e}")
        if added:
            index_pusher.save_accounts(sas)
            st.success(f"Đã thêm {added} SA.")
            st.rerun()
        if errs:
            st.warning("Bỏ qua: " + "; ".join(errs))
        if not added and not errs:
            st.info("Chưa chọn file JSON nào.")

    if not sas:
        st.info("Chưa có Service Account. Thả file JSON ở trên để thêm.")
        return

    with st.expander(f"📋 Danh sách SA ({len(sas)})", expanded=True):
        today = index_pusher._today()
        for i, a in enumerate(sas):
            badge = "🟢" if not a["dead"] else "🔴"
            used = a["used_today"] if a["date"] == today else 0
            name = a["label"] or a["email"]
            col, dele = st.columns([7, 1.2])
            col.markdown(
                f"{badge} **{name}**  \n"
                f"<sub>{a['email']} · dùng {used}/{index_pusher.DAILY_QUOTA} hôm nay · "
                f"{a['status']}</sub>", unsafe_allow_html=True)
            if dele.button("🗑️", key=f"delsa_{i}", help="Xóa SA này"):
                sas.pop(i)
                index_pusher.save_accounts(sas)
                st.rerun()


def make_provider(keys, gl, hl, timeout, use_cache):
    """Tạo SerperProvider từ các key còn sống, dùng chung cache file."""
    cache = FileCache(settings.cache_dir, enabled=use_cache)
    return SerperProvider(keys, gl, hl, int(timeout), cache)


st.title("🎰 SEO iGaming Toolkit")
st.caption("Crawl keyword đối thủ · Check index · Check URL sống/chết · Đẩy index — qua Serper & Google API")

tab_crawl, tab_index, tab_url, tab_push, tab_settings = st.tabs(
    ["🎰 Crawl Keyword", "🔎 Check Index", "🩺 Check URL", "🚀 Đẩy Index", "⚙️ Cài đặt"]
)

# ==================================================================
# TAB: CRAWL KEYWORD
# ==================================================================
with tab_crawl:
    st.subheader("Keyword Crawler")
    st.caption("Keyword → ảnh (Serper) → site chứa ảnh → site: indexed → trích keyword đối thủ")

    with st.expander("⚙️ Cấu hình crawl", expanded=False):
        c1, c2 = st.columns(2)
        crawl_gl = c1.text_input("Country (gl)", settings.serper_gl, key="crawl_gl")
        crawl_hl = c2.text_input("Language (hl)", settings.serper_hl, key="crawl_hl")
        c3, c4 = st.columns(2)
        crawl_workers = c3.number_input("Số luồng song song", min_value=1, max_value=32, value=4,
                                        step=1, key="crawl_workers",
                                        help="Free Serper rate-limit chặt -> để 2-4. Có nhiều key thì tăng được.")
        crawl_timeout = c4.number_input("Timeout (giây)", min_value=5, max_value=120, value=20,
                                        step=5, key="crawl_timeout")
        crawl_cache = st.checkbox("Dùng cache (tiết kiệm credit)", value=True, key="crawl_cache")

        c5, c6 = st.columns(2)
        num_images = c5.number_input("Số ảnh / từ khóa", min_value=1, max_value=100, value=20,
                                     step=1, key="crawl_num_images")
        pages_per_domain = c6.number_input(
            "Số trang / domain (site:)", min_value=1, max_value=100, value=10, step=1,
            key="crawl_pages",
            help="Free Serper chỉ trả 10 kết quả/request; >10 sẽ tự phân trang "
                 "(mỗi 10 trang = 1 credit/domain).")

        do_scrape = st.checkbox("Scrape trang bổ sung (meta/H1-H3/P/tag)", value=True, key="crawl_scrape")
        anti_bot = st.checkbox("🛡️ Vượt anti-bot (curl_cffi + cloudscraper)", value=True,
                               disabled=not do_scrape, key="crawl_antibot",
                               help="Giả lập TLS trình duyệt để vượt Cloudflare/anti-bot. Chậm hơn chút.")
        c7, c8 = st.columns(2)
        scrape_limit = c7.number_input("Số trang gốc scrape / domain", min_value=1, max_value=50,
                                       value=3, step=1, disabled=not do_scrape, key="crawl_scrape_limit")
        internal_link_limit = c8.number_input("Số internal link / domain", min_value=0, max_value=100,
                                               value=10, step=1, disabled=not do_scrape,
                                               key="crawl_internal_limit")
        crawl_internal = st.checkbox(
            "Crawl internal link (anchor + trang con + seed keywords)", value=True,
            disabled=not do_scrape, key="crawl_internal")
        use_yake = st.checkbox("Tách keyphrase bằng YAKE", value=True, key="crawl_yake")
        vi_only = st.checkbox("Chỉ giữ domain tiếng Việt", value=True, key="crawl_vi_only",
                              help="Loại domain nội dung ngoại ngữ (Anh/Ý...) dựa trên tỉ lệ ký tự tiếng Việt")
        max_keyword_words = st.number_input(
            "Độ dài tối đa của keyword (số từ)", min_value=1, max_value=15, value=6, step=1,
            key="crawl_max_words",
            help="Cụm ≤ số từ này = 'keyword'; dài hơn xếp vào 'title/heading' (tab riêng).")

        with st.expander("🚫 Blacklist domain"):
            blacklist_text = st.text_area(
                "Mỗi dòng 1 chuỗi domain cần loại",
                "\n".join(DEFAULT_BLACKLIST), height=180, key="crawl_blacklist")
        with st.expander("✅ Whitelist domain (tùy chọn)"):
            whitelist_text = st.text_area(
                "Nếu điền: CHỈ giữ domain khớp các chuỗi này", "", height=100, key="crawl_whitelist")

        topic_filter = st.checkbox("🎯 Lọc chủ đề iGaming", value=True, key="crawl_topic_filter",
                                   help="Chỉ giữ keyword chứa ít nhất 1 cụm từ ngành bên dưới")
        with st.expander("Danh sách từ ngành iGaming", expanded=False):
            topic_terms_text = st.text_area(
                "Mỗi dòng 1 cụm (khớp chứa, không phân biệt hoa thường)",
                "\n".join(DEFAULT_IGAMING_TERMS), height=200, disabled=not topic_filter,
                key="crawl_topic_terms")

    keywords_text = st.text_area(
        "📝 Nhập từ khóa (mỗi dòng 1 từ khóa — hỗ trợ paste bulk)",
        height=160, key="crawl_keywords",
        placeholder="nhà cái uy tín\nkhuyến mãi nạp đầu\ncá cược thể thao\nslot game đổi thưởng\n...")

    run = st.button("🚀 Bắt đầu crawl", type="primary", key="run_crawl")

    if run:
        keywords = [k.strip() for k in keywords_text.splitlines() if k.strip()]
        keys = live_keys()
        if not keys:
            st.error("Chưa nhập Serper API Key nào — mở tab ⚙️ Cài đặt để thêm key.")
        elif not keywords:
            st.error("Nhập ít nhất 1 từ khóa.")
        else:
            settings.serper_api_keys = keys
            settings.serper_gl = crawl_gl
            settings.serper_hl = crawl_hl
            settings.max_workers = int(crawl_workers)
            settings.timeout = int(crawl_timeout)

            dfilter = DomainFilter(
                blacklist=[b for b in blacklist_text.splitlines() if b.strip()],
                whitelist=[w for w in whitelist_text.splitlines() if w.strip()],
            )
            pipe = Pipeline(settings, domain_filter=dfilter, use_cache=crawl_cache)
            pipe.provider = SerperProvider(keys, crawl_gl, crawl_hl, settings.timeout, pipe.cache)
            topic_terms = [t for t in topic_terms_text.splitlines() if t.strip()]

            bar = st.progress(0.0, text="Bắt đầu...")
            log = st.empty()

            def progress(msg, frac):
                if frac is not None:
                    bar.progress(min(max(frac, 0.0), 1.0), text=msg)
                log.info(msg)

            try:
                result = pipe.run(
                    keywords, num_images=int(num_images), pages_per_domain=int(pages_per_domain),
                    do_scrape=do_scrape, scrape_limit=int(scrape_limit),
                    use_yake=use_yake, lang=crawl_hl, progress=progress,
                    crawl_internal=crawl_internal, internal_link_limit=int(internal_link_limit),
                    vi_only=vi_only, topic_filter=topic_filter, topic_terms=topic_terms,
                    anti_bot=anti_bot, max_keyword_words=int(max_keyword_words))
                st.session_state["result"] = result
                sync_dead_from_provider(pipe.provider)
                st.success("Hoàn tất!")
            except Exception as e:
                st.exception(e)

    if "result" in st.session_state:
        result = st.session_state["result"]
        rows = result["rows"]
        kw_rows = [r for r in rows if r.get("type") == "keyword"]
        title_rows = [r for r in rows if r.get("type") == "title"]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Keyword (ngắn)", len(kw_rows),
                  delta=(f"{len(title_rows)} title tách riêng" if title_rows else None),
                  delta_color="off")
        m2.metric("Domain", len(result["domains"]))
        m3.metric("Ảnh", len(result["image_hits"]))
        m4.metric("Serper credits", result["credits_used"])

        key_stats = result.get("key_stats", [])
        if key_stats:
            dead = [k for k in key_stats if k.get("dead")]
            alive = len(key_stats) - len(dead)
            with st.expander(f"🔑 Trạng thái Serper key ({alive}/{len(key_stats)} còn sống)",
                             expanded=bool(dead)):
                st.dataframe(key_stats, use_container_width=True, hide_index=True)
                if dead:
                    st.warning("Nên **xóa các key đã chết** (hết credit/lỗi) ở tab ⚙️ Cài đặt: "
                               + ", ".join(k["key"] for k in dead))

        d1, d2, d3 = st.columns(3)
        md_full = exporters.to_markdown(kw_rows)
        d1.download_button("⬇️ CSV (keyword)", exporters.to_csv_bytes(kw_rows),
                           "keywords.csv", "text/csv")
        d2.download_button("⬇️ Excel (keyword + titles)",
                           exporters.to_excel_bytes(kw_rows, result["image_hits"], title_rows=title_rows),
                           "keywords.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        d3.download_button("⬇️ Markdown (keyword)", md_full.encode("utf-8"),
                           "keywords.md", "text/markdown")

        r_tab1, r_tab2, r_tab3, r_tab4 = st.tabs(
            [f"🔑 Keyword ({len(kw_rows)})", f"📰 Title/Heading ({len(title_rows)})",
             "📄 Markdown", "🌐 Domains & Images"])
        with r_tab1:
            st.caption("Keyword ngắn (≤ số từ đã cấu hình) — dùng cho SEO.")
            st.dataframe(kw_rows, use_container_width=True, height=500)
        with r_tab2:
            st.caption("Tiêu đề/heading dài — là *chủ đề/ý tưởng nội dung*, không phải keyword.")
            st.dataframe(title_rows, use_container_width=True, height=500)
        with r_tab3:
            st.markdown(exporters.to_markdown(kw_rows, limit=300))
        with r_tab4:
            st.subheader(f"Domains ({len(result['domains'])})")
            st.write(result["domains"])
            st.subheader("Ảnh nguồn")
            st.dataframe([h.__dict__ for h in result["image_hits"]], use_container_width=True)

        if result["scrape_errors"]:
            with st.expander(f"⚠️ {len(result['scrape_errors'])} lỗi scrape (thường do anti-bot)"):
                for url, err in result["scrape_errors"]:
                    st.write(f"- `{err}` — {url}")

# ==================================================================
# TAB: CHECK INDEX
# ==================================================================
with tab_index:
    st.subheader("🔎 Check Index hàng loạt — cú pháp site:")
    st.caption("Dán danh sách domain hoặc URL → kiểm tra Google đã index chưa + số page index.")

    with st.expander("⚙️ Cấu hình", expanded=False):
        c1, c2 = st.columns(2)
        index_gl = c1.text_input("Country (gl)", settings.serper_gl, key="index_gl")
        index_hl = c2.text_input("Language (hl)", settings.serper_hl, key="index_hl")
        c3, c4 = st.columns(2)
        index_workers = c3.number_input("Số luồng song song", min_value=1, max_value=32, value=4,
                                        step=1, key="index_workers")
        index_timeout = c4.number_input("Timeout (giây)", min_value=5, max_value=120, value=20,
                                        step=5, key="index_timeout")
        index_cache = st.checkbox("Dùng cache (tiết kiệm credit)", value=True, key="index_cache")
        pages_index = st.number_input(
            "Số trang kiểm tra / domain (site:)", min_value=1, max_value=50, value=10, step=1,
            key="index_pages",
            help="Dùng để ước lượng số page đã index của domain. URL cụ thể chỉ cần 10.")

    sites_text = st.text_area(
        "📝 Mỗi dòng 1 site hoặc URL (hỗ trợ paste bulk)",
        height=200, key="index_sites",
        placeholder="example.com\nhttps://example.com/bai-viet-abc\nsite:another.com\n...")
    run_index = st.button("🚀 Bắt đầu check index", type="primary", key="run_index")

    if run_index:
        keys = live_keys()
        provider = make_provider(keys, index_gl, index_hl, index_timeout, index_cache)
        lines = [s.strip() for s in sites_text.splitlines() if s.strip()]
        if not keys:
            st.error("Chưa nhập Serper API Key nào — mở tab ⚙️ Cài đặt để thêm key.")
        elif not lines:
            st.error("Nhập ít nhất 1 domain/URL.")
        else:
            bar = st.progress(0.0, text="Bắt đầu...")
            log = st.empty()

            def progress(done, total, msg):
                bar.progress(done / total if total else 1.0, text=msg)
                log.info(msg)

            try:
                results = index_checker.check_bulk(
                    lines, provider, pages=int(pages_index),
                    max_workers=int(index_workers), progress=progress)
                st.session_state["index_results"] = [r.__dict__ for r in results]
                st.session_state["index_credits"] = provider.credits_used
                st.session_state["index_keystats"] = provider.stats_summary()
                st.session_state["index_checked_at"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S")
                sync_dead_from_provider(provider)
                st.success("Hoàn tất!")
            except Exception as e:
                st.exception(e)

    if "index_results" in st.session_state:
        rows = st.session_state["index_results"]
        indexed = [r for r in rows if r["indexed"]]
        not_indexed = [r for r in rows if not r["indexed"] and not r["loi"]]
        errors = [r for r in rows if r["loi"]]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Tổng kiểm tra", len(rows))
        m2.metric("✅ Đã index", len(indexed))
        m3.metric("❌ Chưa index", len(not_indexed))
        m4.metric("Serper credits", st.session_state.get("index_credits", 0))

        keystats = st.session_state.get("index_keystats", [])
        if keystats:
            dead = [k for k in keystats if k.get("dead")]
            with st.expander(f"🔑 Trạng thái Serper key ({len(keystats) - len(dead)}/{len(keystats)} còn sống)",
                             expanded=bool(dead)):
                st.dataframe(keystats, use_container_width=True, hide_index=True)

        display = [{
            "STT": r["stt"],
            "Input": r["input"],
            "Domain gốc": r["domain_goc"],
            "Loại": r["loai"],
            "Index": "✅" if r["indexed"] else ("⚠️ lỗi" if r["loi"] else "❌"),
            "Số page": r["so_page"],
            "Link khớp": r["link_khop"],
            "Ghi chú": r["loi"],
        } for r in rows]

        grouped = {}
        for r in rows:
            d = r["domain_goc"] or "(không xác định)"
            g = grouped.setdefault(
                d, {"Domain gốc": d, "Số mục": 0, "Đã index": 0,
                    "Chưa index": 0, "Lỗi": 0, "Tổng page index": 0})
            g["Số mục"] += 1
            g["Tổng page index"] += r["so_page"]
            if r["loi"]:
                g["Lỗi"] += 1
            elif r["indexed"]:
                g["Đã index"] += 1
            else:
                g["Chưa index"] += 1
        group_rows = sorted(grouped.values(),
                            key=lambda g: (-g["Đã index"], g["Domain gốc"]))

        checked_at = st.session_state.get("index_checked_at", "")
        if checked_at:
            st.caption(f"🕒 Thời điểm check: {checked_at}")
        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇️ CSV kết quả", exporters.to_csv_bytes(display),
            "index_check.csv", "text/csv")
        d2.download_button(
            "⬇️ Excel báo cáo (Tổng quan + Chưa index + Lỗi)",
            exporters.index_to_excel_bytes(display, checked_at, group_rows),
            "index_report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        i_all, i_group, i_no, i_err = st.tabs(
            [f"Tất cả ({len(rows)})", f"🌐 Theo domain ({len(group_rows)})",
             f"❌ Chưa index ({len(not_indexed)})", f"⚠️ Lỗi ({len(errors)})"])
        with i_all:
            st.dataframe(display, use_container_width=True, height=500, hide_index=True)
        with i_group:
            st.caption("Gom nhóm theo domain gốc — tiện so sánh mức độ index giữa các site.")
            st.dataframe(group_rows, use_container_width=True, height=500, hide_index=True)
        with i_no:
            st.caption("Các domain/URL Google chưa index — cần submit/đẩy index.")
            st.dataframe([d for d in display if d["Index"] == "❌"],
                         use_container_width=True, height=400, hide_index=True)
        with i_err:
            st.dataframe([d for d in display if d["Index"] == "⚠️ lỗi"],
                         use_container_width=True, height=300, hide_index=True)

# ==================================================================
# TAB: CHECK URL (HTTP sống/chết — không tốn credit)
# ==================================================================
with tab_url:
    st.subheader("🩺 Check URL — hợp lệ & sống/chết")
    st.caption("Dán danh sách URL → tool **tự dọn ký tự thừa khi copy** (nháy, ngoặc, "
               "dấu phẩy, thiếu/thừa `:` `/`) và **báo URL sai định dạng**, rồi gọi HTTP kiểm tra. "
               "✅ sống (2xx) · ❌ chết (4xx/5xx) · ⚠️ lỗi (sai định dạng / DNS / timeout). "
               "**Không cần API key, không tốn credit.**")

    with st.expander("⚙️ Cấu hình", expanded=False):
        c1, c2 = st.columns(2)
        url_workers = c1.number_input("Số luồng song song", min_value=1, max_value=64, value=8,
                                      step=1, key="url_workers",
                                      help="Check HTTP nhẹ hơn Serper, để 8-20 chạy nhanh.")
        url_timeout = c2.number_input("Timeout (giây)", min_value=3, max_value=120, value=15,
                                      step=1, key="url_timeout",
                                      help="Quá thời gian này coi như URL lỗi/timeout.")
        retries_url = st.number_input("Số lần thử lại khi lỗi mạng/5xx", min_value=0, max_value=3,
                                      value=1, step=1, key="url_retries",
                                      help="Chỉ thử lại khi timeout/đứt kết nối/lỗi server (5xx). "
                                           "KHÔNG thử lại 404. Để 0 nếu muốn nhanh nhất.")
        anti_bot_url = st.checkbox("🛡️ Vượt anti-bot (curl_cffi + cloudscraper)", value=True,
                                   key="url_antibot",
                                   help="Nhiều site chặn request thường bằng 403 dù trang vẫn sống. "
                                        "Bật để giả lập trình duyệt, giảm báo 'chết' sai.")
        follow_redirect = st.checkbox("Theo redirect (3xx → coi trang đích là sống)", value=True,
                                      key="url_follow",
                                      help="Tắt để thấy rõ URL nào bị chuyển hướng (301/302) và đi đâu.")
        detect_soft404 = st.checkbox("🕵️ Đọc nội dung trang (soft 404 + trang chặn)", value=True,
                                     key="url_soft404",
                                     help="Nhiều site trả HTTP 200 dù trang đã bị xóa/khóa "
                                          "('Account Suspended') → bắt thành ❌ chết. Đồng thời nhận diện "
                                          "trang chống bot ('Just a moment', 'Access Denied') → xếp 🔒 chặn. "
                                          "Chậm hơn chút vì phải tải nội dung.")
        scan_links = st.checkbox(
            "🔗 Đếm & liệt kê link stacking / bio-about mà trang trỏ ra",
            value=False, key="url_scan_links",
            help="Chỉ quét thẻ <a href> TRỎ RA NGOÀI nằm trong khối bio/about/profile "
                 "hoặc nội dung. ĐÃ LOẠI: link nội bộ cùng domain, link ở "
                 "header/menu/footer/sidebar/comment, link quảng cáo, iframe/js nhúng, "
                 "mailto/tel. URL viết dạng chữ (không click được) chỉ đếm riêng. "
                 "Link trỏ về domain của bạn hoặc về URL đang check thì luôn được giữ.")
        if scan_links:
            l1, l2 = st.columns(2)
            body_kb = l1.number_input(
                "Giới hạn tải nội dung mỗi trang (KB)", min_value=50, max_value=5000,
                value=400, step=50, key="url_body_kb",
                help="Trang nhiều link cần đọc nhiều hơn. Quá nhỏ sẽ hụt link ở cuối trang; "
                     "quá lớn thì chậm và tốn băng thông.")
            max_links_page = l2.number_input(
                "Số link lưu tối đa mỗi trang", min_value=20, max_value=5000,
                value=500, step=50, key="url_max_links",
                help="Giới hạn lưu để không phình bộ nhớ. Giao diện luôn chỉ hiện 10 link "
                     "đầu mỗi trang; muốn xem hết thì tải Excel/CSV. Các con số đếm vẫn "
                     "tính đủ, phần bị cắt được báo rõ.")
            my_domains_text = st.text_area(
                "🎯 Domain của bạn — mỗi dòng 1 (tùy chọn)", height=80,
                key="url_my_domains_input", placeholder="mysite.com\nblog.mysite.net",
                help="Nhập để tool báo ngay trang đó CÓ link về bạn hay không, và link "
                     "đó là thẻ <a> thật hay chỉ là chữ / bị nofollow. Link trỏ về các "
                     "domain này luôn được giữ, kể cả khi nằm ở footer/quảng cáo.")
        else:
            body_kb, max_links_page, my_domains_text = 400, 500, ""

        skip_services = st.checkbox(
            "⏭️ Bỏ qua domain rút gọn / link-in-bio / paste (chỉ ghi nhận là dạng gì)",
            value=True, key="url_skip_services",
            help="bit.ly, zzb.bz, linktr.ee, bio.site, many.link, allmylinks, "
                 "justpaste.it, telegra.ph, pad.stuve.de, md.chaosdorf.de... là dịch vụ "
                 "trung gian chỉ để chứa/redirect link — check sống chết hay đếm link "
                 "trỏ ra không có ý nghĩa. Bật để KHÔNG gửi request, chỉ gắn nhãn "
                 "⏭️ bỏ qua kèm loại. Tắt nếu vẫn muốn check chúng như URL thường.")
        skip_extra_text = st.text_area(
            "⏭️ Domain bỏ qua thêm — mỗi dòng 1 (tùy chọn)", height=68,
            key="url_skip_extra", placeholder="short.vn\nmylinks.co",
            help="Domain của bạn muốn bỏ qua mà danh sách sẵn có chưa liệt kê. "
                 "Nhập kèm scheme hay không đều được.") if skip_services else ""

    urls_text = st.text_area(
        "📝 Mỗi dòng 1 URL hoặc domain (thiếu http sẽ tự thêm https://)",
        height=200, key="url_urls",
        placeholder="https://example.com/bai-viet-1\nexample.com\n"
                    "https://site.com/trang-loi-404\n...")
    run_url = st.button("🚀 Bắt đầu check URL", type="primary", key="run_url")

    if run_url:
        lines = [u.strip() for u in urls_text.splitlines() if u.strip()]
        if not lines:
            st.error("Nhập ít nhất 1 URL/domain.")
        else:
            bar = st.progress(0.0, text="Bắt đầu...")
            log = st.empty()

            def progress(done, total, msg):
                bar.progress(done / total if total else 1.0, text=msg)
                log.info(msg)

            my_domains = [d.strip().lower() for d in my_domains_text.splitlines()
                          if d.strip()]
            try:
                results = url_checker.check_bulk(
                    lines, timeout=int(url_timeout), anti_bot=anti_bot_url,
                    follow=follow_redirect, max_workers=int(url_workers),
                    progress=progress, soft404=detect_soft404,
                    retries=int(retries_url), scan_links=scan_links,
                    my_domains=my_domains, body_limit=int(body_kb) * 1000,
                    max_links=int(max_links_page), skip_services=skip_services,
                    extra_skip=[d.strip() for d in (skip_extra_text or "").splitlines()
                                if d.strip()])
                st.session_state["url_results"] = [r.__dict__ for r in results]
                st.session_state["url_scanned_links"] = scan_links
                st.session_state["url_my_domains_ran"] = my_domains
                st.session_state["url_checked_at"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S")
                st.success("Hoàn tất!")
            except Exception as e:
                st.exception(e)

    if "url_results" in st.session_state:
        rows = st.session_state["url_results"]
        alive = [r for r in rows if r["category"] == "sống"]
        redir = [r for r in rows if r["category"] == "redirect"]
        blocked = [r for r in rows if r["category"] == "chặn"]
        dead = [r for r in rows if r["category"] == "chết"]
        errors = [r for r in rows if r["category"] == "lỗi"]
        skipped = [r for r in rows if r["category"] == "bỏ qua"]

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Tổng URL", len(rows))
        m2.metric("✅ Sống", len(alive) + len(redir))
        m3.metric("🔒 Chặn", len(blocked),
                  help="Server có phản hồi nhưng chặn/giới hạn tool (401/403/429/503...). "
                       "Chưa kết luận được là chết — kiểm tra lại bằng trình duyệt.")
        m4.metric("❌ Chết", len(dead))
        m5.metric("⚠️ Lỗi", len(errors))
        m6.metric("⏭️ Bỏ qua", len(skipped),
                  help="Domain rút gọn / link-in-bio / paste / pad / Telegraph — "
                       "chỉ ghi nhận loại, không gửi request. Tắt tùy chọn trong "
                       "⚙️ Cấu hình nếu muốn check cả chúng.")

        if skipped:
            kinds = {}
            for r in skipped:
                k = (r.get("skip_type") or "").split(" (")[0]
                kinds[k] = kinds.get(k, 0) + 1
            st.info("⏭️ Đã bỏ qua **" + str(len(skipped)) + "** URL dịch vụ trung gian: "
                    + " · ".join(f"{k} ×{n}" for k, n in sorted(kinds.items(),
                                                               key=lambda x: -x[1]))
                    + ". Xem tab **⏭️ Bỏ qua** để biết từng URL thuộc dạng gì.")

        if blocked:
            st.info(f"🔒 {len(blocked)} URL trả mã chặn/giới hạn (401/403/429/503...). "
                    "Đây **không tính là trang chết**: server có phản hồi nhưng chặn công cụ tự "
                    "động (vd Vimeo, site Cloudflare) hoặc tạm quá tải. Mở bằng trình duyệt để "
                    "kiểm tra chắc chắn.")

        _emoji = {"sống": "✅ sống", "redirect": "🔗 redirect", "chặn": "🔒 chặn",
                  "chết": "❌ chết", "lỗi": "⚠️ lỗi", "bỏ qua": "⏭️ bỏ qua"}

        def _redir_label(r):
            """301 vĩnh viễn giữ được link juice; 302 tạm thì không → phân biệt rõ."""
            if r.get("meta_refresh"):
                return "🔁 meta refresh"
            if not r["redirected"]:
                return ""
            if r.get("permanent"):
                return f"↪️ 301 vĩnh viễn ({r.get('redirect_chain', '')})"
            return f"↪️ tạm ({r.get('redirect_chain', '') or '30x'})"

        _anchor_label = {True: "✅", False: "❌ thiếu", None: ""}
        scanned = st.session_state.get("url_scanned_links", False)
        my_doms = st.session_state.get("url_my_domains_ran", [])
        UI_LINKS_PER_PAGE = 10   # giao diện chỉ hiện 10 link đầu mỗi trang

        def _my_link_cell(r):
            """URL thật trỏ về domain của bạn, hoặc 'x' nếu không có.

            Ghi rõ khi link không phải thẻ <a> trong HTML thô (chỉ text, hoặc chỉ
            hiện sau khi JS render) — vẫn là link nhưng cần mở trình duyệt xác nhận.
            """
            found = [l for l in (r.get("outbound") or []) if l.get("mine")]
            if not found:
                return "x"
            _rank = {"ra ngoài": 0, "thẻ a trong JSON/JS": 1}
            found.sort(key=lambda l: _rank.get(l["loai"], 2))
            best = found[0]
            extra = f" +{len(found) - 1}" if len(found) > 1 else ""
            if best["loai"] == "ra ngoài":
                return f"{best['url']}{extra}"
            if best["loai"] == "thẻ a trong JSON/JS":
                return f"⚙️ {best['url']} (thẻ a trong JSON/JS){extra}"
            if best["loai"].startswith("text"):
                return f"📄 {best['url']} (chỉ là chữ){extra}"
            return f"📦 {best['url']} (chỉ trong JSON/JS){extra}"

        def _row_display(r):
            d = {
                "STT": r["stt"],
                "URL": r["url"],
                "Domain": r["domain"],
                "Trạng thái": _emoji.get(r["category"], r["category"]),
            }
            if scanned and my_doms:      # đặt ngay sau Trạng thái theo yêu cầu
                d["Link về bạn"] = _my_link_cell(r)
            d.update({
                # Để dạng chuỗi cho cả cột (dòng ⏭️ bỏ qua không có mã) — tránh
                # cột lẫn số với rỗng khiến bảng phải tự chuyển kiểu.
                "Mã HTTP": str(r["status_code"]) if r["status_code"] else "",
                "Thời gian (ms)": r["elapsed_ms"],
                "Content-Type": r["content_type"],
                "Redirect": _redir_label(r),
                "Anchor": _anchor_label.get(r.get("anchor_ok"), "") if r.get("anchor") else "",
            })
            if scanned:
                d["Link stacking/bio"] = r.get("links_external", 0)
                d["Trong bio/about"] = r.get("links_bio", 0)
                d["Trong nội dung"] = r.get("links_content", 0)
                d["Domain đích"] = r.get("links_ext_domains", 0)
                d["Dofollow"] = r.get("links_dofollow", 0)
                d["Nofollow"] = r.get("links_nofollow", 0)
                d["Khớp URL đang check"] = r.get("links_checked_exact", 0)
                d["Khớp URL/biến thể"] = r.get("links_to_checked", 0)
                d["URL dạng text (không click)"] = r.get("links_text_only", 0)
                d["Ẩn trong JSON/JS"] = r.get("links_embedded", 0)
                d["Thẻ a trong JSON/JS"] = r.get("links_embedded_anchor", 0)
                d["Bỏ: nội bộ"] = r.get("drop_internal", 0)
                d["Bỏ: không khai báo"] = r.get("drop_undeclared", 0)
                if my_doms:
                    d["Ghi chú link về bạn"] = r.get("my_links_note", "") or "—"
            d["URL cuối / Ghi chú"] = r["note"] or (r["final_url"] if r["redirected"] else "")
            return d

        display = [_row_display(r) for r in rows]

        # Bảng phẳng: mỗi dòng = 1 link mà 1 trang đang trỏ ra.
        # Lọc trùng CHỈ trong phạm vi 1 trang nguồn — cùng 1 URL đích xuất hiện ở
        # nhiều trang nguồn khác nhau thì vẫn là nhiều dòng (cột "Số lần" là số lần
        # lặp trong CHÍNH trang đó).
        # Thống kê THEO TỪNG TRANG NGUỒN — mọi URL đã nhập đều có 1 dòng, kể cả
        # trang 0 link / không quét được, kèm lý do để không hiểu nhầm là "sạch".
        def _page_note(r):
            if r["category"] == "bỏ qua":
                return f"⏭️ bỏ qua — {r.get('skip_type', 'dịch vụ trung gian')}"
            if r["category"] != "sống":
                return (f"❌ không quét được — {r['category']} "
                        f"(HTTP {r['status_code'] or '-'})")
            if r.get("links_embedded") and not r.get("links_external"):
                return ("⚠️ chỉ tìm thấy link trong JSON/JS/meta — trang render bằng JS, "
                        "cần mở trình duyệt xác nhận link có hiện thành thẻ <a>")
            if not r.get("links_total"):
                return ("⚠️ HTML không có thẻ <a> — trang render bằng JS hoặc trang "
                        "xác minh chống bot; mở trình duyệt để xem link bio")
            if not r.get("links_external"):
                return "chỉ có link nội bộ/menu — không có link ra ngoài ở bio/nội dung"
            if r.get("links_embedded"):
                return "✅ đã quét (có thêm link chỉ nằm trong JSON/JS)"
            return "✅ đã quét"

        NO_LINK = "(không có link)"      # nhãn dòng trang nguồn trắng tay
        link_rows = []
        for r in rows:
            found = r.get("outbound") or []
            for lk in found:
                link_rows.append({
                    "STT trang": r["stt"],
                    "Trang nguồn": r["url"],
                    "Link đích": lk["url"],
                    "Domain đích": lk["domain"],
                    "Loại": lk["loai"],
                    "Anchor text": lk["anchor"],
                    "Follow": lk["follow"],
                    "Kiểu": lk["kind"],
                    "Vị trí": lk["zone"],
                    "Số lần": lk["count"],
                    "rel": lk["rel"],
                    "Qua trung gian": lk.get("via", ""),
                    "Đích": url_checker.link_extractor.MATCH_LABEL.get(
                        lk.get("match", ""), ""),
                })
            if scanned and not found:
                # Trang nguồn KHÔNG có link nào khớp khai báo -> vẫn phải có 1 dòng
                # để không bị lọt khỏi danh sách; giao diện tô màu để nhận ra ngay.
                link_rows.append({
                    "STT trang": r["stt"],
                    "Trang nguồn": r["url"],
                    "Link đích": "", "Domain đích": "", "Loại": NO_LINK,
                    "Anchor text": "", "Follow": "", "Kiểu": "",
                    "Vị trí": "", "Số lần": 0, "rel": "", "Qua trung gian": "",
                    "Đích": _page_note(r),
                })
        cut = sum(r.get("links_truncated", 0) or 0 for r in rows)

        page_stat_rows = [{
            "STT": r["stt"],
            "Trang nguồn": r["url"],
            "Trạng thái": _emoji.get(r["category"], r["category"]),
            "Link stacking/bio": r.get("links_external", 0),
            "Trong bio/about": r.get("links_bio", 0),
            "Trong nội dung": r.get("links_content", 0),
            "Domain đích": r.get("links_ext_domains", 0),
            "Dofollow": r.get("links_dofollow", 0),
            "Nofollow": r.get("links_nofollow", 0),
            "Khớp URL đang check": r.get("links_checked_exact", 0),
            "Khớp URL/biến thể": r.get("links_to_checked", 0),
            "URL dạng text": r.get("links_text_only", 0),
            "Ẩn trong JSON/JS": r.get("links_embedded", 0),
            "Thẻ a trong JSON/JS": r.get("links_embedded_anchor", 0),
            "Link về bạn": r.get("my_links_note", "") or "—",
            "Bỏ: nội bộ": r.get("drop_internal", 0),
            "Bỏ: không khai báo": r.get("drop_undeclared", 0),
            "Ghi chú": _page_note(r),
        } for r in rows] if scanned else []

        # Một màu duy nhất để đánh dấu dòng "trang nguồn không có link".
        # rgba nên đọc được cả theme sáng và tối.
        _NO_LINK_BG = "background-color: rgba(255, 193, 7, 0.28)"

        def _style_links(items):
            """Trả về Styler tô vàng các dòng trang nguồn không có link nào."""
            df = pd.DataFrame(items)
            if df.empty or "Loại" not in df.columns:
                return df
            return df.style.apply(
                lambda row: [_NO_LINK_BG if row["Loại"] == NO_LINK else ""] * len(row),
                axis=1)

        def _cap_per_page(items, limit=UI_LINKS_PER_PAGE):
            """Giữ tối đa `limit` link đầu MỖI trang nguồn (yêu cầu: UI chỉ hiện 10)."""
            seen, out = {}, []
            for it in items:
                k = it["STT trang"]
                seen[k] = seen.get(k, 0) + 1
                if seen[k] <= limit:
                    out.append(it)
            return out

        grouped = {}
        for r in rows:
            d = r["domain"] or "(không xác định)"
            g = grouped.setdefault(
                d, {"Domain": d, "Số URL": 0, "Sống": 0, "Chặn": 0, "Chết": 0,
                    "Lỗi": 0, "Bỏ qua": 0})
            g["Số URL"] += 1
            if r["category"] in ("sống", "redirect"):
                g["Sống"] += 1
            elif r["category"] == "chặn":
                g["Chặn"] += 1
            elif r["category"] == "chết":
                g["Chết"] += 1
            elif r["category"] == "bỏ qua":
                g["Bỏ qua"] += 1
            else:
                g["Lỗi"] += 1
        group_rows = sorted(grouped.values(),
                            key=lambda g: (-g["Chết"] - g["Lỗi"], g["Domain"]))

        if scanned:
            ext_links = [l for l in link_rows if l["Loại"] == "ra ngoài"]
            text_links = [l for l in link_rows if l["Loại"].startswith("text")]
            mine_pages = [r for r in rows if (r.get("my_links", 0) or 0) > 0]
            drop_undecl = sum(r.get("drop_undeclared", 0) or 0 for r in rows)
            drop_inner = sum(r.get("drop_internal", 0) or 0 for r in rows)
            json_links = [l for l in link_rows if l["Loại"] == "thẻ a trong JSON/JS"]
            code_links = [l for l in link_rows if l["Loại"].startswith("ẩn")]
            st.markdown("**🔗 Link stacking / bio-about — chỉ link ĐÃ KHAI BÁO**")
            k1, k2, k3, k4, k5, k6 = st.columns(6)
            k1.metric("Link hợp lệ (HTML)", len(ext_links),
                      help="Thẻ <a> có ngay trong HTML, trỏ ra ngoài, và đích đến KHỚP "
                           "KHAI BÁO: domain của bạn hoặc URL trong danh sách đang check "
                           "(kể cả biến thể cùng domain + cùng handle). Gộp trùng theo trang.")
            k2.metric("Thẻ a trong JSON/JS", len(json_links),
                      help="Là thẻ <a href> thật nhưng nằm trong JSON khởi tạo, chỉ hiện "
                           "sau khi JS render (Chess.com, Ameba Ownd/shopinfo.jp, "
                           "Gumroad...). Google render JS nên thường vẫn thấy — tool đã "
                           "đọc được cả rel để biết dofollow/nofollow. Nên mở trình duyệt "
                           "xác nhận.")
            k3.metric("Domain đích khác nhau",
                      len({l["Domain đích"] for l in ext_links + json_links
                           if l["Domain đích"]}))
            k4.metric("Dofollow", sum(1 for l in ext_links + json_links
                                      if l["Follow"] == "dofollow"))
            k5.metric("Nofollow/UGC", sum(1 for l in ext_links + json_links
                                          if l["Follow"] not in ("dofollow", "")))
            k6.metric("Text / chỉ trong mã", len(text_links) + len(code_links),
                      help="URL viết thành chữ, hoặc chỉ xuất hiện trong JSON/JS/meta mà "
                           "KHÔNG phải thẻ <a> → không truyền giá trị SEO. Là dấu hiệu "
                           "link có tồn tại nhưng cần mở trình duyệt xác nhận.")
            no_scan = [r for r in rows if r["category"] != "sống"
                       or not r.get("links_total")]
            if no_scan:
                st.warning(f"⚠️ {len(no_scan)}/{len(rows)} trang **không đọc được link** "
                           "(bị chặn, hoặc HTML render bằng JS nên không có thẻ `<a>`). "
                           "Những trang này hiện 0 link — **không có nghĩa là không có "
                           "backlink**. Xem cột *Ghi chú* ở bảng 📊 Theo trang nguồn.")
            st.caption(
                f"🧹 Đã loại **{drop_undecl}** link **không khớp khai báo** (social/nền "
                f"tảng của chính site chủ, quảng cáo, tài khoản người khác trên cùng "
                f"domain...) và **{drop_inner}** link nội bộ cùng domain. Chỉ link trỏ "
                f"về **domain của bạn** hoặc về **URL trong danh sách nhập** mới được "
                f"tính — link khác coi là không hợp lệ. Cột **Đích** cho biết nó khớp "
                f"kiểu nào.")
            if not my_doms:
                st.warning("⚠️ Chưa nhập **🎯 Domain của bạn** — hiện chỉ đối chiếu được "
                           "với các URL trong danh sách nhập. Nhập domain của bạn để "
                           "kiểm tra backlink về site chính.")
            if my_doms:
                st.info(f"🎯 {len(mine_pages)}/{len(rows)} trang có link thật (thẻ a) về "
                        f"domain của bạn — xem cột **Link về bạn** và tab 🔗 Link trỏ ra.")
            if cut:
                st.warning(f"⚠️ Đã cắt {cut} link do vượt 'Số link lưu tối đa mỗi trang'. "
                           f"Các con số đếm vẫn tính đủ — tăng giới hạn trong ⚙️ Cấu hình "
                           f"nếu cần xuất hết.")

        checked_at = st.session_state.get("url_checked_at", "")
        if checked_at:
            st.caption(f"🕒 Thời điểm check: {checked_at}")

        export_links = link_rows
        if link_rows:
            full = st.checkbox(
                f"📤 Xuất **đầy đủ** tất cả {len(link_rows)} link ra file "
                f"(bỏ giới hạn {UI_LINKS_PER_PAGE} link/trang)",
                value=False, key="url_export_full_links",
                help="Không tích: file tải về cũng chỉ chứa 10 link đầu mỗi trang, "
                     "giống giao diện. Tích để lấy trọn danh sách.")
            if not full:
                export_links = _cap_per_page(link_rows)

        d1, d2, d3 = st.columns(3)
        d1.download_button(
            "⬇️ CSV kết quả", exporters.to_csv_bytes(display),
            "url_check.csv", "text/csv")
        d2.download_button(
            "⬇️ Excel báo cáo (Tổng quan + Chết + Chặn + Lỗi)",
            exporters.url_to_excel_bytes(display, checked_at, group_rows, export_links,
                                         page_stat_rows),
            "url_report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if link_rows:
            d3.download_button(
                f"⬇️ CSV link trỏ ra ({len(export_links)})",
                exporters.to_csv_bytes(export_links), "url_outbound_links.csv", "text/csv")

        tab_labels = [f"Tất cả ({len(rows)})", f"🌐 Theo domain ({len(group_rows)})",
                      f"❌ Chết ({len(dead)})", f"🔒 Chặn ({len(blocked)})",
                      f"⚠️ Lỗi ({len(errors)})"]
        if skipped:
            tab_labels.append(f"⏭️ Bỏ qua ({len(skipped)})")
        if scanned:
            tab_labels.append(f"🔗 Link trỏ ra ({len(link_rows)})")
        _tabs = st.tabs(tab_labels)
        u_all, u_group, u_bad, u_block, u_err = _tabs[:5]
        u_skip = _tabs[5] if skipped else None
        u_links = _tabs[-1] if scanned else None
        with u_all:
            st.dataframe(display, use_container_width=True, height=500, hide_index=True)
        with u_group:
            st.caption("Gom nhóm theo domain — soi nhanh site nào nhiều URL chết.")
            st.dataframe(group_rows, use_container_width=True, height=500, hide_index=True)
        with u_bad:
            st.caption("URL trả HTTP 404/410/5xx — trang không tồn tại hoặc server lỗi thật.")
            st.dataframe([d for d in display if d["Trạng thái"] == "❌ chết"],
                         use_container_width=True, height=400, hide_index=True)
        with u_block:
            st.caption("Server có phản hồi nhưng chặn/giới hạn tool (401/403/429/503...). "
                       "**Không tính là trang chết** — mở bằng trình duyệt để kiểm tra chắc chắn.")
            st.dataframe([d for d in display if d["Trạng thái"] == "🔒 chặn"],
                         use_container_width=True, height=400, hide_index=True)
        with u_err:
            st.caption("URL sai định dạng / thừa ký tự / không phân giải DNS / timeout / SSL lỗi.")
            st.dataframe([d for d in display if d["Trạng thái"] == "⚠️ lỗi"],
                         use_container_width=True, height=300, hide_index=True)
        if u_skip is not None:
            with u_skip:
                st.caption("Dịch vụ trung gian (rút gọn link / link-in-bio / paste / pad "
                           "/ Telegraph) — **không gửi request**, chỉ ghi nhận là dạng gì. "
                           "Tắt tùy chọn trong ⚙️ Cấu hình nếu muốn check chúng như URL "
                           "thường.")
                st.dataframe([{"STT": r["stt"], "URL": r["url"], "Domain": r["domain"],
                               "Dạng": r.get("skip_type", "")} for r in skipped],
                             use_container_width=True, height=360, hide_index=True)

        if u_links is not None:
            with u_links:
                st.caption("Chỉ liệt kê link mà **đích đến đã được khai báo**: 🎯 domain "
                           "của bạn · 🔁 URL trong danh sách nhập (khớp đúng URL, hoặc "
                           "biến thể cùng domain + cùng handle như "
                           "`hitclubdtac.tumblr.com` ↔ `tumblr.com/hitclubdtac`). "
                           "Link khác — social/nền tảng của chính site chủ, quảng cáo, "
                           "tài khoản người khác, link nội bộ, iframe/js, mailto/tel — "
                           "đều **không hợp lệ** nên không liệt kê. URL dạng chữ và URL "
                           "chỉ nằm trong JSON/JS được gắn nhãn riêng. Chỉ quét trên "
                           "trang ✅ sống.")
                st.markdown("**📊 Thống kê theo trang nguồn — toàn bộ URL đã nhập**")
                st.caption("Mỗi URL bạn nhập đều có 1 dòng, kể cả trang 0 link hoặc "
                           "không quét được — cột **Ghi chú** nói rõ lý do.")
                st.dataframe(page_stat_rows, use_container_width=True,
                             height=min(420, 80 + 35 * len(page_stat_rows)),
                             hide_index=True)
                st.divider()
                if not link_rows:
                    st.info("Không tìm thấy link nào khớp khai báo (trang không sống, "
                            "không đọc được nội dung, hoặc trang không hề trỏ tới domain "
                            "của bạn / URL nào trong danh sách nhập).")
                else:
                    f1, f2 = st.columns([3, 2])
                    view = f1.radio(
                        "Xem", ["Tất cả", "🎯 Link về domain của bạn",
                                "🔁 Khớp đúng URL đang check", "Trong bio/about",
                                "📄 Text / 📦 ẩn trong JSON-JS"],
                        horizontal=True, key="url_link_view", label_visibility="collapsed")
                    kw = f2.text_input("🔎 Lọc (domain đích / anchor / URL chứa...)",
                                       key="url_link_filter", placeholder="vd: mysite.com")

                    shown = link_rows
                    if view == "🎯 Link về domain của bạn":
                        shown = [l for l in shown if l["Đích"].startswith("🎯")]
                    elif view == "🔁 Khớp đúng URL đang check":
                        shown = [l for l in shown if "khớp URL" in l["Đích"]]
                    elif view == "Trong bio/about":
                        shown = [l for l in shown if "bio/profile" in l["Vị trí"]]
                    elif view == "📄 Text / 📦 ẩn trong JSON-JS":
                        shown = [l for l in shown
                                 if l["Loại"].startswith(("text", "ẩn"))]
                    if kw.strip():
                        k = kw.strip().lower()
                        shown = [l for l in shown
                                 if k in f"{l['Link đích']} {l['Domain đích']} "
                                          f"{l['Anchor text']} {l['Trang nguồn']}".lower()]

                    capped = _cap_per_page(shown)
                    hidden = len(shown) - len(capped)
                    n_nolink = sum(1 for l in capped if l["Loại"] == NO_LINK)
                    st.caption(f"Đang hiện **{len(capped)}** / {len(shown)} dòng "
                               f"(tổng {len(link_rows)}). Giao diện chỉ hiện "
                               f"{UI_LINKS_PER_PAGE} link đầu mỗi trang nguồn" +
                               (f" — còn **{hidden}** link nữa, tích ô "
                                f"*📤 Xuất đầy đủ* ở trên rồi tải Excel/CSV để xem hết."
                                if hidden else ".") +
                               (f" 🟡 **{n_nolink}** dòng tô vàng = trang nguồn "
                                f"**không có link nào** khớp khai báo (cột *Đích* ghi "
                                f"lý do)." if n_nolink else ""))
                    st.dataframe(_style_links(capped), use_container_width=True,
                                 height=480, hide_index=True)
                    per_page = {}
                    for l in shown:
                        per_page[l["Trang nguồn"]] = per_page.get(l["Trang nguồn"], 0) + 1
                    over = {u: n for u, n in per_page.items() if n > UI_LINKS_PER_PAGE}
                    if over:
                        st.caption("📊 Trang có nhiều hơn 10 link (số lượng thật): " +
                                   " · ".join(f"{u} = **{n}**"
                                              for u, n in sorted(over.items(),
                                                                 key=lambda x: -x[1])[:15]))

                    agg = {}
                    for l in link_rows:
                        if l["Loại"] != "ra ngoài" or not l["Domain đích"]:
                            continue
                        a = agg.setdefault(l["Domain đích"], {
                            "Domain đích": l["Domain đích"], "Số link": 0,
                            "Số trang trỏ tới": set(), "Dofollow": 0, "Nofollow": 0})
                        a["Số link"] += 1
                        a["Số trang trỏ tới"].add(l["STT trang"])
                        a["Dofollow" if l["Follow"] == "dofollow" else "Nofollow"] += 1
                    agg_rows = sorted(
                        ({**a, "Số trang trỏ tới": len(a["Số trang trỏ tới"])}
                         for a in agg.values()),
                        key=lambda a: -a["Số link"])
                    with st.expander(f"🌐 Gom theo domain đích ({len(agg_rows)})",
                                     expanded=False):
                        st.caption("Domain nào được các trang này trỏ tới nhiều nhất — "
                                   "nhìn ra ngay trang đang bán/nhồi link cho ai.")
                        st.dataframe(agg_rows, use_container_width=True, height=360,
                                     hide_index=True)

# ==================================================================
# TAB: ĐẨY INDEX (Google Indexing API + xoay Service Account)
# ==================================================================
with tab_push:
    st.subheader("🚀 Đẩy Index hàng loạt — Google Indexing API")
    st.caption("Dán danh sách URL → submit vào Google. Nhiều Service Account tự xoay "
               "(mỗi SA 200 URL/ngày). Thêm Service Account ở tab ⚙️ Cài đặt.")

    with st.expander("ℹ️ Điều kiện bắt buộc để đẩy được (đọc 1 lần)", expanded=False):
        st.markdown(
            "1. Tạo **Service Account** trên Google Cloud, tải file **JSON key**.\n"
            "2. Bật **Indexing API** cho project đó.\n"
            "3. Vào **Google Search Console** của domain → *Cài đặt → Người dùng và quyền* "
            "→ thêm email của SA (`...@...iam.gserviceaccount.com`) làm **Owner**.\n"
            "4. Thêm file JSON ở tab ⚙️ Cài đặt. Muốn đẩy nhiều hơn 200/ngày "
            "→ thêm nhiều SA (mỗi SA là 1 project, +200/ngày).")

    with st.expander("⚙️ Cấu hình", expanded=False):
        c1, c2 = st.columns(2)
        push_workers = c1.number_input("Số luồng song song", min_value=1, max_value=16, value=4,
                                       step=1, key="push_workers",
                                       help="Indexing API có rate-limit; để 2-6 là hợp lý.")
        push_timeout = c2.number_input("Timeout (giây)", min_value=5, max_value=120, value=30,
                                       step=5, key="push_timeout")
        notif_type = st.radio(
            "Loại thông báo", ["URL_UPDATED", "URL_DELETED"], horizontal=True, key="push_notif",
            help="URL_UPDATED: báo Google crawl lại (đẩy index). URL_DELETED: báo trang đã gỡ.")

    urls_text_push = st.text_area(
        "📝 Mỗi dòng 1 URL đầy đủ (http/https)",
        height=220, key="push_urls",
        placeholder="https://site.com/bai-viet-1\nhttps://site.com/bai-viet-2\n...")
    run_push = st.button("🚀 Bắt đầu đẩy index", type="primary", key="run_push")

    if run_push:
        sas = st.session_state["sas"]
        urls = [u.strip() for u in urls_text_push.splitlines() if u.strip()]
        alive = [a for a in sas if not a["dead"]]
        remain = index_pusher.remaining_quota(sas)
        if not alive:
            st.error("Chưa có Service Account nào còn sống. Thêm SA ở tab ⚙️ Cài đặt.")
        elif not urls:
            st.error("Nhập ít nhất 1 URL.")
        elif len(urls) > remain:
            st.warning(f"Bạn nhập {len(urls)} URL nhưng quota hôm nay chỉ còn ~{remain}. "
                       f"Sẽ đẩy tối đa {remain} URL, phần còn lại báo hết quota. "
                       f"Thêm SA để đẩy hết.")
        if alive and urls:
            pusher = index_pusher.IndexPusher(sas, notif_type=notif_type, timeout=int(push_timeout))
            bar = st.progress(0.0, text="Bắt đầu...")
            log = st.empty()

            def progress(done, total, msg):
                bar.progress(done / total if total else 1.0, text=msg)
                log.info(msg)

            try:
                results = pusher.push_bulk(urls, max_workers=int(push_workers), progress=progress)
                index_pusher.save_accounts(sas)  # lưu quota đã dùng
                st.session_state["push_results"] = results
                st.session_state["push_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                st.success("Hoàn tất!")
            except Exception as e:
                st.exception(e)

    if "push_results" in st.session_state:
        results = st.session_state["push_results"]
        ok = [r for r in results if r["ok"]]
        fail = [r for r in results if not r["ok"]]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Tổng URL", len(results))
        m2.metric("✅ Đã đẩy", len(ok))
        m3.metric("❌ Thất bại", len(fail))
        m4.metric("Quota còn lại hôm nay", index_pusher.remaining_quota(st.session_state["sas"]))

        display = [{
            "URL": r["url"],
            "Trạng thái": "✅" if r["ok"] else "❌",
            "Service Account": r["account"],
            "Mã": r["code"],
            "Ghi chú": r["msg"],
        } for r in results]

        at = st.session_state.get("push_at", "")
        if at:
            st.caption(f"🕒 Thời điểm đẩy: {at}")
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ CSV kết quả", exporters.to_csv_bytes(display),
                           "push_index.csv", "text/csv")
        d2.download_button("⬇️ Excel báo cáo",
                           exporters.push_to_excel_bytes(display, at),
                           "push_report.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        p_all, p_fail = st.tabs([f"Tất cả ({len(results)})", f"❌ Thất bại ({len(fail)})"])
        with p_all:
            st.dataframe(display, use_container_width=True, height=480, hide_index=True)
        with p_fail:
            st.caption("URL đẩy lỗi — kiểm tra lại ownership Search Console / URL hợp lệ / quota.")
            st.dataframe([d for d in display if d["Trạng thái"] == "❌"],
                         use_container_width=True, height=400, hide_index=True)

# ==================================================================
# TAB: CÀI ĐẶT (quản lý Serper key + Service Account)
# ==================================================================
with tab_settings:
    st.caption("Nhập key ở đây một lần — các tab tính năng tự dùng. Key/Service Account lưu local, đã gitignore.")
    render_key_manager()
    st.divider()
    render_sa_manager()
