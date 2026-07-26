"""Xuất kết quả ra Markdown / CSV / Excel."""
import io

import pandas as pd


def _esc(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def to_markdown(rows, limit=None) -> str:
    lines = [
        "# Danh sách từ khóa (SEO iGaming)",
        "",
        f"Tổng cộng: **{len(rows)}** từ khóa.",
        "",
        "| # | Từ khóa | Tần suất | Số domain | Nguồn | Domains |",
        "|---|---------|:--------:|:---------:|-------|---------|",
    ]
    data = rows if not limit else rows[:limit]
    for i, r in enumerate(data, 1):
        lines.append(
            f"| {i} | {_esc(r['keyword'])} | {r['frequency']} | "
            f"{r['domain_count']} | {_esc(r['origins'])} | {_esc(r['domains'])} |"
        )
    if limit and len(rows) > limit:
        lines.append("")
        lines.append(f"_... còn {len(rows) - limit} từ khóa nữa (tải file để xem đầy đủ)._")
    return "\n".join(lines)


def to_csv_bytes(rows) -> bytes:
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode("utf-8-sig")  # utf-8-sig để Excel mở tiếng Việt đúng


def push_to_excel_bytes(rows, submitted_at: str = "") -> bytes:
    """Báo cáo Excel cho chế độ Đẩy Index. rows: dict hiển thị (URL, Trạng thái, ...)."""
    df = pd.DataFrame(rows)
    ok = df[df["Trạng thái"] == "✅"] if not df.empty else df
    fail = df[df["Trạng thái"] == "❌"] if not df.empty else df
    summary = pd.DataFrame([
        {"Chỉ số": "Thời điểm đẩy", "Giá trị": submitted_at or "-"},
        {"Chỉ số": "Tổng URL", "Giá trị": len(df)},
        {"Chỉ số": "Đẩy thành công ✅", "Giá trị": len(ok)},
        {"Chỉ số": "Thất bại ❌", "Giá trị": len(fail)},
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Tổng quan", index=False)
        df.to_excel(writer, sheet_name="Tất cả", index=False)
        if not fail.empty:
            fail.to_excel(writer, sheet_name="Thất bại", index=False)
    buf.seek(0)
    return buf.getvalue()


def index_to_excel_bytes(rows, checked_at: str = "", group_rows=None) -> bytes:
    """Báo cáo Excel cho chế độ Check Index.

    rows: list dict đã hiển thị (STT, Input, Domain gốc, Loại, Index, ...).
    group_rows: thống kê gom nhóm theo domain gốc (tùy chọn).
    Sheets: Tổng quan / Theo domain / Tất cả / Chưa index / Lỗi.
    """
    df = pd.DataFrame(rows)
    indexed = df[df["Index"] == "✅"] if not df.empty else df
    not_indexed = df[df["Index"] == "❌"] if not df.empty else df
    errors = df[df["Index"] == "⚠️ lỗi"] if not df.empty else df

    summary = pd.DataFrame([
        {"Chỉ số": "Thời điểm check", "Giá trị": checked_at or "-"},
        {"Chỉ số": "Tổng kiểm tra", "Giá trị": len(df)},
        {"Chỉ số": "Đã index ✅", "Giá trị": len(indexed)},
        {"Chỉ số": "Chưa index ❌", "Giá trị": len(not_indexed)},
        {"Chỉ số": "Lỗi ⚠️", "Giá trị": len(errors)},
        {"Chỉ số": "Tỉ lệ index",
         "Giá trị": f"{(len(indexed) / len(df) * 100):.1f}%" if len(df) else "-"},
    ])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Tổng quan", index=False)
        if group_rows:
            pd.DataFrame(group_rows).to_excel(writer, sheet_name="Theo domain", index=False)
        df.to_excel(writer, sheet_name="Tất cả", index=False)
        if not not_indexed.empty:
            not_indexed.to_excel(writer, sheet_name="Chưa index", index=False)
        if not errors.empty:
            errors.to_excel(writer, sheet_name="Lỗi", index=False)
    buf.seek(0)
    return buf.getvalue()


def url_to_excel_bytes(rows, checked_at: str = "", group_rows=None,
                       link_rows=None, page_stat_rows=None) -> bytes:
    """Báo cáo Excel cho chế độ Check URL (sống/chết).

    rows: list dict đã hiển thị (STT, URL, Trạng thái, Mã HTTP, ...).
    group_rows: thống kê gom nhóm theo domain (tùy chọn).
    link_rows: danh sách link trang trỏ ra (tùy chọn) -> thêm 2 sheet link.
    page_stat_rows: thống kê link theo TỪNG trang nguồn (mọi URL đã nhập).
    Sheets: Tổng quan / Theo domain / Tất cả / Chết / Chặn / Lỗi /
            Link theo trang nguồn / Link trỏ ra / Domain đích.
    """
    df = pd.DataFrame(rows)
    alive = df[df["Trạng thái"] == "✅ sống"] if not df.empty else df
    blocked = df[df["Trạng thái"] == "🔒 chặn"] if not df.empty else df
    dead = df[df["Trạng thái"] == "❌ chết"] if not df.empty else df
    errors = df[df["Trạng thái"] == "⚠️ lỗi"] if not df.empty else df
    skipped = df[df["Trạng thái"] == "⏭️ bỏ qua"] if not df.empty else df

    ldf = pd.DataFrame(link_rows or [])
    ext = ldf[ldf["Loại"] == "ra ngoài"] if not ldf.empty else ldf

    stats = [
        {"Chỉ số": "Thời điểm check", "Giá trị": checked_at or "-"},
        {"Chỉ số": "Tổng URL", "Giá trị": len(df)},
        {"Chỉ số": "Sống ✅", "Giá trị": len(alive)},
        {"Chỉ số": "Chặn 🔒 (site sống, chặn bot)", "Giá trị": len(blocked)},
        {"Chỉ số": "Chết ❌", "Giá trị": len(dead)},
        {"Chỉ số": "Lỗi ⚠️", "Giá trị": len(errors)},
        {"Chỉ số": "Bỏ qua ⏭️ (rút gọn/bio link/paste)", "Giá trị": len(skipped)},
        {"Chỉ số": "Tỉ lệ sống (trên số URL đã check)",
         "Giá trị": (f"{(len(alive) / (len(df) - len(skipped)) * 100):.1f}%"
                     if len(df) - len(skipped) > 0 else "-")},
    ]
    if not ldf.empty:
        stats += [
            {"Chỉ số": "🔗 Link ra ngoài", "Giá trị": len(ext)},
            {"Chỉ số": "🔗 Domain đích khác nhau",
             "Giá trị": ext["Domain đích"].nunique() if not ext.empty else 0},
            {"Chỉ số": "🔗 Dofollow",
             "Giá trị": int((ext["Follow"] == "dofollow").sum()) if not ext.empty else 0},
            {"Chỉ số": "🔗 URL dạng text (không click được)",
             "Giá trị": int(ldf["Loại"].astype(str).str.startswith("text").sum())},
        ]
    summary = pd.DataFrame(stats)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Tổng quan", index=False)
        if group_rows:
            pd.DataFrame(group_rows).to_excel(writer, sheet_name="Theo domain", index=False)
        df.to_excel(writer, sheet_name="Tất cả", index=False)
        if not dead.empty:
            dead.to_excel(writer, sheet_name="Chết", index=False)
        if not blocked.empty:
            blocked.to_excel(writer, sheet_name="Chặn", index=False)
        if not errors.empty:
            errors.to_excel(writer, sheet_name="Lỗi", index=False)
        if not skipped.empty:
            skipped.to_excel(writer, sheet_name="Bỏ qua", index=False)
        if page_stat_rows:
            pd.DataFrame(page_stat_rows).to_excel(
                writer, sheet_name="Link theo trang nguồn", index=False)
        if not ldf.empty:
            ldf.to_excel(writer, sheet_name="Link trỏ ra", index=False)
            if not ext.empty:
                agg = (ext.groupby("Domain đích")
                       .agg(**{"Số link": ("Link đích", "count"),
                               "Số trang trỏ tới": ("STT trang", "nunique")})
                       .reset_index().sort_values("Số link", ascending=False))
                agg.to_excel(writer, sheet_name="Domain đích", index=False)
    buf.seek(0)
    return buf.getvalue()


def to_excel_bytes(rows, image_hits=None, title_rows=None) -> bytes:
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Keywords", index=False)

        # Sheet title/heading (không phải keyword) để riêng
        if title_rows:
            pd.DataFrame(title_rows).to_excel(writer, sheet_name="Titles", index=False)

        # Sheet tổng hợp theo domain
        if rows:
            dom = {}
            for r in rows:
                for d in str(r["domains"]).split(", "):
                    d = d.strip()
                    if d:
                        dom[d] = dom.get(d, 0) + 1
            dom_df = (pd.DataFrame(
                [{"domain": k, "keyword_count": v} for k, v in dom.items()])
                .sort_values("keyword_count", ascending=False))
            dom_df.to_excel(writer, sheet_name="By Domain", index=False)

        if image_hits:
            img_df = pd.DataFrame([h.__dict__ for h in image_hits])
            img_df.to_excel(writer, sheet_name="Images", index=False)
    buf.seek(0)
    return buf.getvalue()
