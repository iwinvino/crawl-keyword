"""Giao diện Streamlit: Crawl Keyword đối thủ + Check Index hàng loạt (Serper)."""
from datetime import datetime

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

# ---------------- Quản lý API key (lưu lâu dài) ----------------
if "keys" not in st.session_state:
    loaded = key_store.load_keys()
    if not loaded and settings.serper_api_keys:   # lần đầu: nạp từ .env vào store
        loaded = [{"label": "từ .env", "key": k, "dead": False, "status": "chưa test"}
                  for k in settings.serper_api_keys]
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
    """Khối quản lý key trên sidebar: thêm/xóa/đặt tên/test."""
    keys = st.session_state["keys"]
    alive = sum(1 for e in keys if not e["dead"])
    st.header("🔑 Quản lý Serper Key")
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
    """Khối quản lý Service Account trên sidebar: thêm (JSON) / xóa / xem quota."""
    sas = st.session_state["sas"]
    alive = sum(1 for a in sas if not a["dead"])
    remain = index_pusher.remaining_quota(sas)
    st.header("🔐 Service Account (Google)")
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

# ---------------- Sidebar: chọn chế độ + cấu hình dùng chung ----------------
with st.sidebar:
    mode = st.radio(
        "🔧 Chế độ",
        ["🎰 Crawl Keyword", "🔎 Check Index", "🩺 Check URL", "🚀 Đẩy Index"],
        help="Crawl Keyword: lấy keyword đối thủ qua ảnh + site:.\n"
             "Check Index: kiểm tra domain/URL đã được Google index chưa.\n"
             "Check URL: kiểm tra URL hợp lệ + sống/chết bằng HTTP (không tốn credit).\n"
             "Đẩy Index: submit hàng loạt URL vào Google (Indexing API + nhiều SA).")
    is_index = mode.startswith("🔎")
    is_push = mode.startswith("🚀")
    is_url = mode.startswith("🩺")

    # Đẩy Index dùng Service Account; Check URL không cần key; còn lại dùng Serper key
    if is_push:
        render_sa_manager()
    elif is_url:
        st.header("🩺 Check URL sống/chết")
        st.caption("Gọi thẳng tới từng URL để biết trang còn truy cập được không. "
                   "**Không cần API key, không tốn credit.**")
    else:
        render_key_manager()

    st.header("⚙️ Cấu hình")
    if is_push:
        c1, c2 = st.columns(2)
        max_workers = c1.number_input("Số luồng song song", min_value=1, max_value=16, value=4, step=1,
                                      help="Indexing API có rate-limit; để 2-6 là hợp lý.")
        timeout = c2.number_input("Timeout (giây)", min_value=5, max_value=120, value=30, step=5)
        notif_type = st.radio(
            "Loại thông báo", ["URL_UPDATED", "URL_DELETED"], horizontal=True,
            help="URL_UPDATED: báo Google crawl lại (đẩy index). URL_DELETED: báo trang đã gỡ.")
    elif is_url:
        c1, c2 = st.columns(2)
        max_workers = c1.number_input("Số luồng song song", min_value=1, max_value=64, value=8, step=1,
                                      help="Check HTTP nhẹ hơn Serper, để 8-20 chạy nhanh.")
        timeout = c2.number_input("Timeout (giây)", min_value=3, max_value=120, value=15, step=1,
                                  help="Quá thời gian này coi như URL lỗi/timeout.")
        retries_url = st.number_input("Số lần thử lại khi lỗi mạng/5xx", min_value=0, max_value=3,
                                      value=1, step=1,
                                      help="Chỉ thử lại khi timeout/đứt kết nối/lỗi server (5xx) — "
                                           "giảm báo '⚠️ lỗi' oan do mạng chập chờn. KHÔNG thử lại "
                                           "404 vì đó đã là kết luận chắc chắn. Để 0 nếu muốn nhanh nhất.")
        anti_bot_url = st.checkbox("🛡️ Vượt anti-bot (curl_cffi + cloudscraper)", value=True,
                                   help="Nhiều site chặn request thường bằng 403 dù trang vẫn sống. "
                                        "Bật để giả lập trình duyệt, giảm báo 'chết' sai.")
        follow_redirect = st.checkbox("Theo redirect (3xx → coi trang đích là sống)", value=True,
                                      help="Tắt để thấy rõ URL nào bị chuyển hướng (301/302) và đi đâu.")
        detect_soft404 = st.checkbox("🕵️ Đọc nội dung trang (soft 404 + trang chặn)", value=True,
                                     help="Nhiều site trả HTTP 200 dù trang đã bị xóa/khóa "
                                          "('Account Suspended', profile bị gỡ) → bắt thành ❌ chết. "
                                          "Đồng thời nhận diện trang chống bot ('Just a moment', "
                                          "'Access Denied') → xếp 🔒 chặn thay vì báo chết oan. "
                                          "Chậm hơn chút vì phải tải nội dung.")
    else:
        c1, c2 = st.columns(2)
        gl = c1.text_input("Country (gl)", settings.serper_gl)
        hl = c2.text_input("Language (hl)", settings.serper_hl)

        c5, c6 = st.columns(2)
        max_workers = c5.number_input("Số luồng song song", min_value=1, max_value=32, value=4, step=1,
                                      help="Free Serper rate-limit chặt -> để 2-4. Có nhiều key thì tăng được.")
        timeout = c6.number_input("Timeout (giây)", min_value=5, max_value=120, value=20, step=5)
        use_cache = st.checkbox("Dùng cache (tiết kiệm credit)", value=True)

    # ----- Cấu hình riêng cho chế độ Check Index -----
    if is_index:
        pages_index = st.number_input(
            "Số trang kiểm tra / domain (site:)", min_value=1, max_value=50, value=10, step=1,
            help="Dùng để ước lượng số page đã index của domain. URL cụ thể chỉ cần 10.")

    # ----- Cấu hình riêng cho chế độ Crawl Keyword -----
    if not is_index and not is_push and not is_url:
        c3, c4 = st.columns(2)
        num_images = c3.number_input("Số ảnh / từ khóa", min_value=1, max_value=100, value=20, step=1)
        pages_per_domain = c4.number_input(
            "Số trang / domain (site:)", min_value=1, max_value=100, value=10, step=1,
            help="Free Serper chỉ trả 10 kết quả/request; >10 sẽ tự phân trang "
                 "(mỗi 10 trang = 1 credit/domain).")

        do_scrape = st.checkbox("Scrape trang bổ sung (meta/H1-H3/P/tag)", value=True)
        anti_bot = st.checkbox("🛡️ Vượt anti-bot (curl_cffi + cloudscraper)", value=True,
                               disabled=not do_scrape,
                               help="Giả lập TLS trình duyệt để vượt Cloudflare/anti-bot. Chậm hơn chút.")
        c7, c8 = st.columns(2)
        scrape_limit = c7.number_input("Số trang gốc scrape / domain", min_value=1, max_value=50,
                                       value=3, step=1, disabled=not do_scrape)
        internal_link_limit = c8.number_input("Số internal link / domain", min_value=0, max_value=100,
                                               value=10, step=1,
                                               disabled=not do_scrape)
        crawl_internal = st.checkbox(
            "Crawl internal link (anchor + trang con + seed keywords)", value=True, disabled=not do_scrape)
        use_yake = st.checkbox("Tách keyphrase bằng YAKE", value=True)
        vi_only = st.checkbox("Chỉ giữ domain tiếng Việt", value=True,
                              help="Loại domain nội dung ngoại ngữ (Anh/Ý...) dựa trên tỉ lệ ký tự tiếng Việt")
        max_keyword_words = st.number_input(
            "Độ dài tối đa của keyword (số từ)", min_value=1, max_value=15, value=6, step=1,
            help="Cụm ≤ số từ này = 'keyword'; dài hơn xếp vào 'title/heading' (tab riêng).")

        with st.expander("🚫 Blacklist domain"):
            blacklist_text = st.text_area(
                "Mỗi dòng 1 chuỗi domain cần loại",
                "\n".join(DEFAULT_BLACKLIST), height=180)
        with st.expander("✅ Whitelist domain (tùy chọn)"):
            whitelist_text = st.text_area(
                "Nếu điền: CHỈ giữ domain khớp các chuỗi này", "", height=100)

        st.divider()
        topic_filter = st.checkbox("🎯 Lọc chủ đề iGaming", value=True,
                                   help="Chỉ giữ keyword chứa ít nhất 1 cụm từ ngành bên dưới")
        with st.expander("Danh sách từ ngành iGaming", expanded=False):
            topic_terms_text = st.text_area(
                "Mỗi dòng 1 cụm (khớp chứa, không phân biệt hoa thường)",
                "\n".join(DEFAULT_IGAMING_TERMS), height=200, disabled=not topic_filter)


def _build_provider():
    """Tạo SerperProvider từ các key còn sống trong store, dùng chung cache file."""
    keys = live_keys()
    cache = FileCache(settings.cache_dir, enabled=use_cache)
    return keys, SerperProvider(keys, gl, hl, int(timeout), cache)


# ==================================================================
# CHẾ ĐỘ ĐẨY INDEX (Google Indexing API + xoay Service Account)
# ==================================================================
if is_push:
    st.title("🚀 Đẩy Index hàng loạt — Google Indexing API")
    st.caption("Dán danh sách URL → submit vào Google. Nhiều Service Account tự xoay "
               "(mỗi SA 200 URL/ngày).")

    with st.expander("ℹ️ Điều kiện bắt buộc để đẩy được (đọc 1 lần)", expanded=False):
        st.markdown(
            "1. Tạo **Service Account** trên Google Cloud, tải file **JSON key**.\n"
            "2. Bật **Indexing API** cho project đó.\n"
            "3. Vào **Google Search Console** của domain → *Cài đặt → Người dùng và quyền* "
            "→ thêm email của SA (`...@...iam.gserviceaccount.com`) làm **Owner**.\n"
            "4. Thêm file JSON vào ô Service Account bên trái. Muốn đẩy nhiều hơn 200/ngày "
            "→ thêm nhiều SA (mỗi SA là 1 project, +200/ngày).")

    urls_text = st.text_area(
        "📝 Mỗi dòng 1 URL đầy đủ (http/https)",
        height=220,
        placeholder="https://site.com/bai-viet-1\nhttps://site.com/bai-viet-2\n...")
    run_push = st.button("🚀 Bắt đầu đẩy index", type="primary")

    if run_push:
        sas = st.session_state["sas"]
        urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
        alive = [a for a in sas if not a["dead"]]
        remain = index_pusher.remaining_quota(sas)
        if not alive:
            st.error("Chưa có Service Account nào còn sống. Thêm SA ở sidebar.")
        elif not urls:
            st.error("Nhập ít nhất 1 URL.")
        elif len(urls) > remain:
            st.warning(f"Bạn nhập {len(urls)} URL nhưng quota hôm nay chỉ còn ~{remain}. "
                       f"Sẽ đẩy tối đa {remain} URL, phần còn lại báo hết quota. "
                       f"Thêm SA để đẩy hết.")
        if alive and urls:
            pusher = index_pusher.IndexPusher(sas, notif_type=notif_type, timeout=int(timeout))
            bar = st.progress(0.0, text="Bắt đầu...")
            log = st.empty()

            def progress(done, total, msg):
                bar.progress(done / total if total else 1.0, text=msg)
                log.info(msg)

            try:
                results = pusher.push_bulk(urls, max_workers=int(max_workers), progress=progress)
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

        tab_all, tab_fail = st.tabs([f"Tất cả ({len(results)})", f"❌ Thất bại ({len(fail)})"])
        with tab_all:
            st.dataframe(display, use_container_width=True, height=480, hide_index=True)
        with tab_fail:
            st.caption("URL đẩy lỗi — kiểm tra lại ownership Search Console / URL hợp lệ / quota.")
            st.dataframe([d for d in display if d["Trạng thái"] == "❌"],
                         use_container_width=True, height=400, hide_index=True)

    st.stop()


# ==================================================================
# CHẾ ĐỘ CHECK URL (HTTP sống/chết — không tốn credit)
# ==================================================================
if is_url:
    st.title("🩺 Check URL — hợp lệ & sống/chết")
    st.caption("Dán danh sách URL → tool **tự dọn ký tự thừa khi copy** (nháy, ngoặc, "
               "dấu phẩy, thiếu/thừa `:` `/`) và **báo URL sai định dạng** (khoảng trắng "
               "giữa URL, scheme gõ sai...), rồi gọi HTTP kiểm tra. "
               "✅ sống (2xx) · ❌ chết (4xx/5xx) · ⚠️ lỗi (sai định dạng / DNS / timeout).")

    urls_text = st.text_area(
        "📝 Mỗi dòng 1 URL hoặc domain (thiếu http sẽ tự thêm https://)",
        height=200,
        placeholder="https://example.com/bai-viet-1\nexample.com\n"
                    "https://site.com/trang-loi-404\n...")
    run_url = st.button("🚀 Bắt đầu check URL", type="primary")

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

            try:
                results = url_checker.check_bulk(
                    lines, timeout=int(timeout), anti_bot=anti_bot_url,
                    follow=follow_redirect, max_workers=int(max_workers),
                    progress=progress, soft404=detect_soft404,
                    retries=int(retries_url))
                st.session_state["url_results"] = [r.__dict__ for r in results]
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

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Tổng URL", len(rows))
        m2.metric("✅ Sống", len(alive) + len(redir))
        m3.metric("🔒 Chặn", len(blocked),
                  help="Server có phản hồi nhưng chặn/giới hạn tool (401/403/429/503...). "
                       "Chưa kết luận được là chết — kiểm tra lại bằng trình duyệt.")
        m4.metric("❌ Chết", len(dead))
        m5.metric("⚠️ Lỗi", len(errors))

        if blocked:
            st.info(f"🔒 {len(blocked)} URL trả mã chặn/giới hạn (401/403/429/503...). "
                    "Đây **không tính là trang chết**: server có phản hồi nhưng chặn công cụ tự "
                    "động (vd Vimeo, site Cloudflare) hoặc tạm quá tải. Mở bằng trình duyệt để "
                    "kiểm tra chắc chắn.")

        _emoji = {"sống": "✅ sống", "redirect": "🔗 redirect", "chặn": "🔒 chặn",
                  "chết": "❌ chết", "lỗi": "⚠️ lỗi"}
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
        display = [{
            "STT": r["stt"],
            "URL": r["url"],
            "Domain": r["domain"],
            "Trạng thái": _emoji.get(r["category"], r["category"]),
            "Mã HTTP": r["status_code"] or "",
            "Thời gian (ms)": r["elapsed_ms"],
            "Content-Type": r["content_type"],
            "Redirect": _redir_label(r),
            "Anchor": _anchor_label.get(r.get("anchor_ok"), "") if r.get("anchor") else "",
            "URL cuối / Ghi chú": r["note"] or (r["final_url"] if r["redirected"] else ""),
        } for r in rows]

        # Gom nhóm theo domain
        grouped = {}
        for r in rows:
            d = r["domain"] or "(không xác định)"
            g = grouped.setdefault(
                d, {"Domain": d, "Số URL": 0, "Sống": 0, "Chặn": 0, "Chết": 0, "Lỗi": 0})
            g["Số URL"] += 1
            if r["category"] in ("sống", "redirect"):
                g["Sống"] += 1
            elif r["category"] == "chặn":
                g["Chặn"] += 1
            elif r["category"] == "chết":
                g["Chết"] += 1
            else:
                g["Lỗi"] += 1
        group_rows = sorted(grouped.values(),
                            key=lambda g: (-g["Chết"] - g["Lỗi"], g["Domain"]))

        checked_at = st.session_state.get("url_checked_at", "")
        if checked_at:
            st.caption(f"🕒 Thời điểm check: {checked_at}")
        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇️ CSV kết quả", exporters.to_csv_bytes(display),
            "url_check.csv", "text/csv")
        d2.download_button(
            "⬇️ Excel báo cáo (Tổng quan + Chết + Chặn + Lỗi)",
            exporters.url_to_excel_bytes(display, checked_at, group_rows),
            "url_report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        tab_all, tab_group, tab_bad, tab_block, tab_err = st.tabs(
            [f"Tất cả ({len(rows)})", f"🌐 Theo domain ({len(group_rows)})",
             f"❌ Chết ({len(dead)})", f"🔒 Chặn ({len(blocked)})",
             f"⚠️ Lỗi ({len(errors)})"])
        with tab_all:
            st.dataframe(display, use_container_width=True, height=500, hide_index=True)
        with tab_group:
            st.caption("Gom nhóm theo domain — soi nhanh site nào nhiều URL chết.")
            st.dataframe(group_rows, use_container_width=True, height=500, hide_index=True)
        with tab_bad:
            st.caption("URL trả HTTP 404/410/5xx — trang không tồn tại hoặc server lỗi thật.")
            st.dataframe([d for d in display if d["Trạng thái"] == "❌ chết"],
                         use_container_width=True, height=400, hide_index=True)
        with tab_block:
            st.caption("Server có phản hồi nhưng chặn/giới hạn tool (401/403/429/503...). "
                       "**Không tính là trang chết** — mở bằng trình duyệt để kiểm tra chắc chắn.")
            st.dataframe([d for d in display if d["Trạng thái"] == "🔒 chặn"],
                         use_container_width=True, height=400, hide_index=True)
        with tab_err:
            st.caption("URL sai định dạng / thừa ký tự / không phân giải DNS / timeout / SSL lỗi.")
            st.dataframe([d for d in display if d["Trạng thái"] == "⚠️ lỗi"],
                         use_container_width=True, height=300, hide_index=True)

    st.stop()


# ==================================================================
# CHẾ ĐỘ CHECK INDEX
# ==================================================================
if is_index:
    st.title("🔎 Check Index hàng loạt — cú pháp site:")
    st.caption("Dán danh sách domain hoặc URL → kiểm tra Google đã index chưa + số page index.")

    sites_text = st.text_area(
        "📝 Mỗi dòng 1 site hoặc URL (hỗ trợ paste bulk)",
        height=200,
        placeholder="example.com\nhttps://example.com/bai-viet-abc\nsite:another.com\n...")
    run_index = st.button("🚀 Bắt đầu check index", type="primary")

    if run_index:
        keys, provider = _build_provider()
        lines = [s.strip() for s in sites_text.splitlines() if s.strip()]
        if not keys:
            st.error("Chưa nhập Serper API Key nào.")
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
                    max_workers=int(max_workers), progress=progress)
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

        # Bảng hiển thị thân thiện
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

        # Gom nhóm theo domain gốc
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

        tab_all, tab_group, tab_no, tab_err = st.tabs(
            [f"Tất cả ({len(rows)})", f"🌐 Theo domain ({len(group_rows)})",
             f"❌ Chưa index ({len(not_indexed)})", f"⚠️ Lỗi ({len(errors)})"])
        with tab_all:
            st.dataframe(display, use_container_width=True, height=500, hide_index=True)
        with tab_group:
            st.caption("Gom nhóm theo domain gốc — tiện so sánh mức độ index giữa các site.")
            st.dataframe(group_rows, use_container_width=True, height=500, hide_index=True)
        with tab_no:
            st.caption("Các domain/URL Google chưa index — cần submit/đẩy index.")
            st.dataframe([d for d in display if d["Index"] == "❌"],
                         use_container_width=True, height=400, hide_index=True)
        with tab_err:
            st.dataframe([d for d in display if d["Index"] == "⚠️ lỗi"],
                         use_container_width=True, height=300, hide_index=True)

    st.stop()  # không chạy phần crawl bên dưới


# ==================================================================
# CHẾ ĐỘ CRAWL KEYWORD (giữ nguyên luồng cũ)
# ==================================================================
st.title("🎰 SEO iGaming — Keyword Crawler (Serper)")
st.caption("Keyword → ảnh (Serper) → site chứa ảnh → site: indexed → trích keyword đối thủ")

keywords_text = st.text_area(
    "📝 Nhập từ khóa (mỗi dòng 1 từ khóa — hỗ trợ paste bulk)",
    height=160,
    placeholder="nhà cái uy tín\nkhuyến mãi nạp đầu\ncá cược thể thao\nslot game đổi thưởng\n...")

run = st.button("🚀 Bắt đầu crawl", type="primary")

if run:
    keywords = [k.strip() for k in keywords_text.splitlines() if k.strip()]
    keys = live_keys()
    if not keys:
        st.error("Chưa nhập Serper API Key nào.")
    elif not keywords:
        st.error("Nhập ít nhất 1 từ khóa.")
    else:
        settings.serper_api_keys = keys
        settings.serper_gl = gl
        settings.serper_hl = hl
        settings.max_workers = int(max_workers)
        settings.timeout = int(timeout)

        dfilter = DomainFilter(
            blacklist=[b for b in blacklist_text.splitlines() if b.strip()],
            whitelist=[w for w in whitelist_text.splitlines() if w.strip()],
        )
        pipe = Pipeline(settings, domain_filter=dfilter, use_cache=use_cache)
        pipe.provider = SerperProvider(keys, gl, hl, settings.timeout, pipe.cache)
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
                use_yake=use_yake, lang=hl, progress=progress,
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
                st.warning("Nên **xóa các key đã chết** (hết credit/lỗi) khỏi ô Serper API Keys: "
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

    tab1, tab2, tab3, tab4 = st.tabs(
        [f"🔑 Keyword ({len(kw_rows)})", f"📰 Title/Heading ({len(title_rows)})",
         "📄 Markdown", "🌐 Domains & Images"])
    with tab1:
        st.caption("Keyword ngắn (≤ số từ đã cấu hình) — dùng cho SEO.")
        st.dataframe(kw_rows, use_container_width=True, height=500)
    with tab2:
        st.caption("Tiêu đề/heading dài — là *chủ đề/ý tưởng nội dung*, không phải keyword.")
        st.dataframe(title_rows, use_container_width=True, height=500)
    with tab3:
        st.markdown(exporters.to_markdown(kw_rows, limit=300))
    with tab4:
        st.subheader(f"Domains ({len(result['domains'])})")
        st.write(result["domains"])
        st.subheader("Ảnh nguồn")
        st.dataframe([h.__dict__ for h in result["image_hits"]], use_container_width=True)

    if result["scrape_errors"]:
        with st.expander(f"⚠️ {len(result['scrape_errors'])} lỗi scrape (thường do anti-bot)"):
            for url, err in result["scrape_errors"]:
                st.write(f"- `{err}` — {url}")
