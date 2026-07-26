# SEO iGaming Toolkit

Bộ công cụ 3 trong 1 (Streamlit), tối ưu cho **site tiếng Việt** (`gl=vn`, `hl=vi`):

| Chế độ | Làm gì | Cần gì |
|--------|--------|--------|
| 🎰 **Crawl Keyword** | Nghiên cứu keyword đối thủ qua ngõ image SERP | Serper API key |
| 🔎 **Check Index** | Kiểm tra hàng loạt domain/URL đã được Google index chưa | Serper API key |
| 🩺 **Check URL** | Kiểm tra hàng loạt URL hợp lệ + sống/chết bằng HTTP, kèm **đếm & liệt kê link stacking/bio-about** | Không cần gì (miễn phí) |
| 🚀 **Đẩy Index** | Submit hàng loạt URL vào Google (Indexing API) | Google Service Account |

---

## 1. Cài đặt & chạy nhanh

**Cách dễ nhất (Windows):** double-click **`run.bat`**. Lần đầu tự tạo môi trường ảo `.venv` + cài thư viện; các lần sau mở thẳng app.

**Cách thủ công:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

App mở ở `http://localhost:8501`. Chọn chế độ ở góc trên sidebar.

---

## 2. 🎰 Crawl Keyword — nghiên cứu keyword đối thủ

Luồng:

```
Từ khóa → Serper Image Search → trang/site chứa ảnh → site:domain (indexed)
        → trích keyword (SERP title/snippet + YAKE + scrape meta/H1-H3)
        → lấy internal link (anchor) + crawl trang con lấy thêm keyword
        → lọc domain tiếng Việt + lọc chủ đề iGaming → xuất MD / CSV / Excel
```

**Cách dùng:**
1. Sidebar → khối **🔑 Quản lý Serper Key** → thêm key (lấy tại https://serper.dev).
2. Dán danh sách từ khóa (mỗi dòng 1 từ) vào ô lớn.
3. Chỉnh cấu hình: số ảnh/từ khóa, số trang/domain, scrape, blacklist, lọc iGaming...
4. Bấm **🚀 Bắt đầu crawl** → xem tab Keyword / Title / Domains → tải CSV / Excel / Markdown.

**Điểm đáng chú ý:**
- **Lọc chủ đề iGaming**: chỉ giữ keyword chứa từ ngành (nhà cái, nổ hũ, kèo, casino, slot, 8xbet/sunwin...). Sửa mặc định ở `DEFAULT_IGAMING_TERMS` trong [config.py](config.py).
- **Vượt anti-bot**: bật để dùng `curl_cffi` (giả lập TLS Chrome, vượt Cloudflare) → `cloudscraper` → `requests`.
- **Cache**: bật để chạy lại không tốn thêm credit Serper.

**Chi phí credit:** mỗi từ khóa = 1 request ảnh; mỗi domain = 1 request `site:`.

---

## 3. 🔎 Check Index — kiểm tra đã index chưa

Dùng cú pháp `site:...` qua Serper để biết Google đã index domain/URL chưa.

**Cách dùng:**
1. Chọn chế độ **🔎 Check Index**.
2. Dán mỗi dòng 1 mục — lẫn lộn domain và URL đều được:
   ```
   nhacai-abc.com                      ← check cả domain (số page index)
   https://nhacai-abc.com/khuyen-mai   ← check 1 URL cụ thể (đã index chưa)
   site:another.net                    ← tự gõ site: cũng được, tool tự bỏ tiền tố
   ```
3. Bấm **🚀 Bắt đầu check index**.

**Kết quả:**
- Cột **Index**: ✅ đã index / ❌ chưa / ⚠️ lỗi.
- Cột **Số page**: số page ước lượng của domain (Google không trả con số tuyệt đối).
- Cột **Domain gốc** + tab **🌐 Theo domain**: gom nhóm thống kê index theo từng site.
- Export **CSV** + **Excel báo cáo** (Tổng quan / Theo domain / Chưa index / Lỗi).

**Chi phí credit:** mỗi dòng = 1 request `site:`.

---

## 4. 🩺 Check URL — kiểm tra URL hợp lệ & sống/chết

Gọi **HTTP thẳng** tới từng URL để biết trang còn truy cập được không. **Không dùng Serper, không tốn credit.** Khác với Check Index (hỏi Google đã index chưa), chế độ này hỏi thẳng chính trang đó.

**Cách dùng:**
1. Chọn chế độ **🩺 Check URL**.
2. Dán mỗi dòng 1 URL hoặc domain (thiếu `http` sẽ tự thêm `https://`):
   ```
   https://nhacai-abc.com/khuyen-mai   ← check URL cụ thể
   nhacai-abc.com                      ← tự thêm https://
   https://site.com/trang-da-go-404    ← sẽ báo chết (404)
   ```
3. (Tùy chọn) chỉnh **Số luồng**, **Timeout**, **Vượt anti-bot**, **Theo redirect**, **Phát hiện soft 404**.
4. Bấm **🚀 Bắt đầu check URL**.

**🕵️ Phát hiện soft 404 (bật mặc định) — vì sao cần:** rất nhiều site trả về **HTTP 200 OK** dù trang đã chết. Ví dụ điển hình:
- Trang **"Account Suspended"** của cPanel (domain bị host khóa) → vẫn trả 200.
- **Profile bị xóa/đình chỉ** trên mạng xã hội, forum → trả 200 kèm nội dung "User not found".
- Trang bị gỡ nhưng bị **đá về trang chủ**.

Nếu chỉ nhìn mã HTTP thì tool sẽ báo "sống" sai. Khi bật, tool đọc thêm `<title>`/`<h1>` của trang và bắt các dấu hiệu lỗi ở **mọi ngôn ngữ** (Anh, Việt, Trung, Nhật, Hàn, Nga, Tây Ban Nha, Bồ, Pháp, Đức, Ý, Thái, Ả Rập, Thổ, Indonesia, Ba Lan...) → xếp vào **❌ chết** kèm ghi chú `soft 404: HTTP 200 nhưng trang báo lỗi — "<tiêu đề>"` để bạn kiểm chứng ngay. Tắt đi nếu muốn chạy nhanh hơn (chỉ xét mã HTTP).

Cùng lúc đó tool nhận diện **trang chống bot** trả 200 ("Just a moment", "Attention Required | Cloudflare", "Access Denied", "403 Forbidden", captcha...) → xếp **🔒 chặn**, *không* báo chết oan. Đây là site sống, chỉ chặn công cụ tự động.

**Tự dọn & bắt lỗi khi paste:** tool tự bỏ ký tự thừa hay dính lúc copy (nháy `"` `'`, ngoặc `()`, dấu phẩy/chấm ở 2 đầu, `non-breaking space`) và tự sửa scheme thiếu/thừa ký tự (`https:/`, `https//`, `http:://` → `https://`). Nếu còn sai thì **báo lỗi rõ ràng**: khoảng trắng giữa URL (vd `a.co m`), scheme gõ sai (`htp://`, `ftp://`), tên miền `..` thừa, thiếu `.com/.vn`...

**Kết quả — cột Trạng thái:**
- **✅ sống** — HTTP 2xx (trang truy cập được).
- **🔗 redirect** — HTTP 3xx, hiện URL đích (chỉ khi tắt *Theo redirect*).
- **🔒 chặn** — HTTP 401/403/429/451/503... Server **có phản hồi nhưng chặn/giới hạn tool** (anti-bot, cần đăng nhập, rate-limit, hoặc tạm quá tải). **KHÔNG tính là chết** — vd Vimeo, site Cloudflare trả 403 cho công cụ tự động nhưng trình duyệt thật vẫn vào bình thường.
- **❌ chết** — HTTP 404/410/5xx server lỗi thật, **hoặc soft 404** (trả 200 nhưng nội dung là trang lỗi, hoặc bị đá về trang chủ **cùng domain** → profile đã bị gỡ). Chuyển hướng **sang domain khác** thì KHÔNG tính là chết, vì đó là đích đến có chủ đích (link rút gọn `bit.ly`, `tinyurl`... hoạt động đúng là phải nhảy sang site khác).
- **⚠️ lỗi** — URL sai định dạng / thừa ký tự / không phân giải DNS / timeout / SSL lỗi.

Kèm **Mã HTTP**, **thời gian phản hồi (ms)**, **Content-Type**, **URL cuối sau redirect**, tab **🌐 Theo domain** (đếm sống/chặn/chết theo site) và export CSV / Excel báo cáo.

**Cột Redirect — phân biệt 301 và 302 (quan trọng cho backlink):**
- `↪️ 301 vĩnh viễn` — chuyển hướng vĩnh viễn, **giữ được link juice**.
- `↪️ tạm (302)` — chuyển hướng tạm, Google **không truyền** sức mạnh link như 301.
- `🔁 meta refresh` — trang trả 200 rồi tự nhảy bằng `<meta http-equiv="refresh">`. Trình duyệt đi theo còn request thường thì không → nếu bỏ qua sẽ chấm "sống" nhầm cho trang đã bị gỡ. Tool tự đi theo (tối đa 3 bước) rồi mới kết luận.

**Cột Anchor:** nếu URL có `#phần-nào-đó`, tool kiểm tra `id`/`name` đó có thật trên trang không (`✅` có / `❌ thiếu`). Hữu ích khi backlink trỏ vào một mục cụ thể.

**Thử lại khi lỗi mạng/5xx:** chỉnh ở sidebar (mặc định 1 lần). Chỉ thử lại khi timeout/đứt kết nối/lỗi server — **không** thử lại 404 vì đó đã là kết luận chắc chắn. Giúp giảm báo `⚠️ lỗi` oan do mạng chập chờn.

### 4.1. 🔗 Đếm & liệt kê link stacking / bio-about (chỉ link ĐÃ KHAI BÁO)

Bật **🔗 Đếm & liệt kê link stacking / bio-about mà trang trỏ ra** trong ⚙️ Cấu hình của tab Check URL.

**Nguyên tắc cốt lõi:** một link chỉ **hợp lệ** khi đích đến của nó **đã được khai báo**:

| Nhãn cột **Đích** | Khớp thế nào |
|---|---|
| 🎯 **domain của bạn** | root domain nằm trong ô **🎯 Domain của bạn** (bất kể đường dẫn nào) |
| 🔁 **khớp URL đang check** | trùng đúng 1 URL trong danh sách nhập — bỏ qua khác biệt `http/https`, `www`, `/` cuối, chữ hoa/thường |
| 🔁 **biến thể URL đang check** | cùng domain đã khai báo **và** cùng *handle*, vd `hitclubdtac.tumblr.com` ↔ `tumblr.com/hitclubdtac`, `bs.gravatar.com/hitclubdtac` ↔ `gravatar.com/hitclubdtac` |

**Mọi link khác đều KHÔNG hợp lệ → không liệt kê, chỉ đếm:**
- Link nội bộ cùng domain với trang nguồn → cột `Bỏ: nội bộ`.
- Social / nền tảng **của chính site chủ** (vd `instagram.com/zachmoonshinemdpr`, `jamroom.net`, `makewebeasy.com` trên trang forum của họ) → cột `Bỏ: không khai báo`.
- **Cùng domain đã khai báo nhưng khác handle** — vd bạn khai `x.com/HITCLUBDTAC` mà trang trỏ tới `x.com/ZachMoonshine` → cũng loại, vì đó là tài khoản người khác.
- Quảng cáo, iframe/js nhúng, `mailto:` `tel:` `javascript:`, file CSS/JS/ảnh.

*Handle* được lấy tự động từ URL bạn nhập (nhãn subdomain + các đoạn đường dẫn + giá trị query), bỏ các từ chung chung (`user`, `users`, `profile`, `forum`, `about`, `page`...) và token ngắn dưới 5 ký tự. Chuẩn hoá bỏ dấu gạch nên `hitclub-dtac` = `hitclub_dtac` = `hitclub.dtac` = `hitclubdtac`.

**Ví dụ thực tế** (danh sách nhập = file test `example/sample.txt` ở máy, domain của bạn = `hitclubdtac.com`):

| Trang nguồn | Kết quả |
|---|---|
| `gamblingtherapy.org/forum/users/hitclubdtac/` | **20 link hợp lệ** = 1 🎯 + 19 🔁 (behance, bsky, deviantart, flickr, github, bandcamp, issuu, kickstarter, letterboxd, pinterest, plurk, quora, reddit...) · loại 141 nội bộ + 5 không khai báo |
| `metaldevastationradio.com/hitclubdtac` | **1 link hợp lệ** = 🎯 `hitclubdtac.com` · loại 8 link social/nền tảng của chủ site |
| `www.fw-follow.com/forum/topic/.../hitclubdtac` | **1 link hợp lệ** · loại `line.me`, `makewebeasy.com` |

**URL dạng chữ** (không click được) và **URL chỉ nằm trong JSON/JS/meta** (trang render bằng JS như Pinterest, Tumblr, Gumroad) cũng chỉ liệt kê khi khớp khai báo, kèm nhãn riêng `📄 text thường` / `📦 ẩn trong JSON/JS` và **không tính là backlink** — dùng để biết link *có tồn tại* nhưng cần mở trình duyệt xác nhận.

**Các cột thêm vào bảng kết quả:** `Link stacking/bio` (số link hợp lệ) · `Trong bio/about` · `Trong nội dung` · `Domain đích` · `Dofollow` / `Nofollow` · `Khớp URL đang check` · `Khớp URL/biến thể` · `URL dạng text (không click)` · `Ẩn trong JSON/JS` · `Bỏ: nội bộ` · `Bỏ: không khai báo` · `Link về bạn`.

**Ô 🎯 Domain của bạn:** mọi cách viết đều **như nhau** — `https://a.com/`, `www.a.com`, `a.com`, `A.COM:443/abc?x=1` → đều hiểu là `a.com`; subdomain (`blog.a.com`) cũng khớp. Cột **Link về bạn** báo ngay:
- `✅ 2 link (1 dofollow)` — backlink đã lên thật.
- `⚠️ chỉ ở dạng text/không click được` — site chỉ in URL ra chữ → **không tính là backlink**.
- `⚠️ chỉ thấy trong JSON/JS/meta` — trang render bằng JS, cần mở trình duyệt xác nhận.
- `❌ không thấy link về domain của bạn` — link đã bị xóa/chưa duyệt.

Nếu trang khai `<meta robots nofollow>` thì tool ghi chú rõ: **mọi link trên trang đều mất giá trị**.

**📊 Bảng "Theo trang nguồn" — thống kê ĐỦ mọi URL đã nhập:** trong tab 🔗 Link trỏ ra, **mỗi URL bạn nhập đúng 1 dòng**, kể cả trang 0 link hoặc không đọc được, kèm cột **Ghi chú**:
- `✅ đã quét` · `⚠️ chỉ tìm thấy link trong JSON/JS/meta` · `⚠️ HTML không có thẻ <a>` (JS render / trang xác minh chống bot) · `❌ không quét được — chặn/chết (HTTP ...)` · `⏭️ bỏ qua — <dạng>` · `chỉ có link nội bộ/menu`.

Bảng này cũng xuất thành sheet Excel **Link theo trang nguồn**, nên "0 link vì bị chặn" không bị hiểu lẫn với "0 link vì không có backlink".

**Lọc trùng chỉ trong PHẠM VI 1 TRANG NGUỒN:** cùng 1 URL đích xuất hiện trên 5 trang nguồn khác nhau = **5 dòng**. Cột `Số lần` là số lần URL đó lặp *trong chính trang đó* (2 thẻ `<a>` cùng đích trên cùng 1 trang = 1 dòng, `Số lần = 2`).

**Tab 🔗 Link trỏ ra** liệt kê từng link kèm **Anchor text**, **Follow** (dofollow / nofollow / ugc / sponsored), **Vị trí** (bio/profile · nội dung · footer · menu...), **Số lần**, cột **Đích** + bộ lọc nhanh (Tất cả · 🎯 domain của bạn · 🔁 khớp đúng URL · Trong bio/about · 📄 Text / 📦 ẩn JSON-JS) và ô tìm theo chuỗi. Kèm bảng **🌐 Gom theo domain đích**.

**Giới hạn 10 link/trang trên giao diện:** nếu 1 trang có hơn 10 link hợp lệ, bảng chỉ hiện **10 link đầu**, kèm dòng đếm **số lượng thật** (vd `gamblingtherapy.org/... = 20`). Tích ô **📤 Xuất đầy đủ tất cả N link ra file** rồi tải Excel/CSV để lấy trọn danh sách; mặc định file tải về cũng chỉ 10 link/trang.

**Bộ test:** đặt danh sách URL thật vào `example/sample.txt` (thư mục `example/` đã gitignore — không đẩy backlink của bạn lên repo). Bộ test đang dùng: 720 URL. Dán cả file vào ô URL để kiểm thử: chạy hết trong ~2 phút (0.16s/URL, 24 luồng), ra ~11.200 link hợp lệ, loại ~4.600 link không khai báo + ~22.000 link nội bộ.

**Lưu ý:** chỉ quét được trên trang **✅ sống**. Hai ô cấu hình kèm theo: **Giới hạn tải nội dung mỗi trang (KB)** — trang nhiều link cần tăng để không hụt link ở cuối trang; **Số link lưu tối đa mỗi trang** — giới hạn lưu, phần bị cắt được báo rõ.

### 4.2. ⏭️ Bỏ qua domain rút gọn / link-in-bio / paste

Nhiều URL trong danh sách backlink không phải trang nội dung mà là **dịch vụ trung gian**: link rút gọn, trang "link in bio", paste/pad công khai, bài đăng nhanh. Check sống/chết hay đếm link trỏ ra ở đó không có ý nghĩa (chúng vốn sinh ra để chứa link, phần lớn render bằng JS).

Tùy chọn **⏭️ Bỏ qua domain rút gọn / link-in-bio / paste** (bật mặc định) → tool **không gửi request**, chỉ gắn nhãn `⏭️ bỏ qua` kèm **loại**:

| Nhãn | Ví dụ domain |
|------|--------------|
| 🔗 link rút gọn | `bit.ly` `zzb.bz` `urlz.fr` `n9.cl` `tinyurl.com` `cutt.ly` `t.co`... |
| 🪪 trang link-in-bio | `linktr.ee` `bio.site` `beacons.ai` `many.link` `manylink.co` `lit.link` `magic.ly` `linksta.cc` `linkmix.co` `mez.ink` `joy.bio` `jaga.link` `biolinku.co` `allmylinks.com` `album.link`... |
| 📋 paste/ghi chú công khai | `justpaste.it` `pastebin.com` `paste.ee` `rentry.co` `notes.io`... |
| 📝 pad công khai (HedgeDoc/CodiMD) | `pad.stuve.de/s/...` `md.chaosdorf.de/s/...` (nhận theo mẫu `pad.` / `md.` + đường dẫn `/s/...`) |
| 📰 bài đăng nhanh | `telegra.ph` `te.legra.ph` `graph.org` |
| ⏭️ do bạn khai báo | domain bạn tự nhập ở ô **Domain bỏ qua thêm** |

Kết quả: thêm ô số **⏭️ Bỏ qua**, tab **⏭️ Bỏ qua** liệt kê từng URL kèm dạng, cột **Bỏ qua** trong bảng theo domain, sheet Excel **Bỏ qua**, và **tỉ lệ sống tính trên số URL đã check thật** (không tính phần bỏ qua). Muốn check cả chúng thì tắt tùy chọn này.

Danh sách domain nằm trong [url_types.py](url_types.py) — thêm domain mới = thêm 1 dòng vào set tương ứng; hoặc nhập nhanh trong ô **⏭️ Domain bỏ qua thêm** ngay trên giao diện.

**Mẹo:**
- Bật **🛡️ Vượt anti-bot** để nhiều site Cloudflare không bị báo "chặn" oan (chúng trả 403 cho request thường nhưng 200 cho trình duyệt thật).
- URL rơi vào **🔒 chặn** thì mở bằng trình duyệt để kiểm tra chắc chắn — tool không thể phân biệt 100% giữa "chặn bot" và "sập thật" ở các mã như 503.
- Tắt **Theo redirect** để soi URL nào bị chuyển hướng 301/302 và chuyển đi đâu (hữu ích khi kiểm tra link cũ).

---

## 5. 🚀 Đẩy Index — submit URL vào Google (HƯỚNG DẪN CHI TIẾT)

> ⚠️ **Đọc kỹ trước khi dùng.** Đây **không phải** nút bấm là Google index ngay.
> - **Submit ≠ chắc chắn được index.** Indexing API chỉ *yêu cầu Google crawl lại*; Google vẫn tự quyết index hay không.
> - Indexing API **chính thức** dành cho trang **JobPosting / BroadcastEvent (livestream)**. Dùng cho URL thường là kỹ thuật phổ biến trong SEO nhưng **nằm ngoài mục đích Google công bố** — tự cân nhắc rủi ro tài khoản.
> - Mỗi Service Account = **200 URL/ngày**. Muốn đẩy nhiều hơn → thêm nhiều SA (tool tự xoay).

### 5.1. Tạo Service Account trên Google Cloud

1. Vào https://console.cloud.google.com → đăng nhập.
2. Tạo project mới (hoặc chọn project có sẵn): thanh trên cùng → **Select a project** → **New Project** → đặt tên → **Create**.
3. Bật **Indexing API** cho project:
   - Menu (☰) → **APIs & Services** → **Library**.
   - Tìm **"Web Search Indexing API"** (còn gọi Indexing API) → bấm **Enable**.
4. Tạo Service Account:
   - Menu (☰) → **APIs & Services** → **Credentials**.
   - **+ CREATE CREDENTIALS** → **Service account**.
   - Đặt tên (vd `indexer-1`) → **Create and continue** → bỏ qua các bước quyền (Continue → Done).
5. Tải file **JSON key**:
   - Trong danh sách Service Account, bấm vào SA vừa tạo → tab **KEYS**.
   - **ADD KEY** → **Create new key** → chọn **JSON** → **Create**.
   - File `.json` tự tải về máy — **đây là file bạn nạp vào tool**. Giữ bí mật (chứa private key).

### 5.2. Cấp quyền Owner trong Google Search Console

Bước này **bắt buộc**, thiếu là bị lỗi **403** khi đẩy.

1. Mở file JSON vừa tải, copy giá trị **`client_email`** (dạng `indexer-1@ten-project.iam.gserviceaccount.com`).
2. Vào https://search.google.com/search-console → chọn **property (domain)** cần đẩy index.
   - Nếu domain chưa có trong Search Console → phải **thêm & xác minh** property trước.
3. **Cài đặt (Settings)** → **Người dùng và quyền (Users and permissions)**.
4. **Thêm người dùng (Add user)** → dán `client_email` của SA → chọn quyền **Owner (Chủ sở hữu)** → **Thêm**.

> 1 SA có thể làm Owner của **nhiều domain** — chỉ cần thêm email đó vào từng property. Khi đó SA đẩy được URL của tất cả domain nó sở hữu.

### 5.3. Nạp SA vào tool & đẩy

1. Chọn chế độ **🚀 Đẩy Index**.
2. Sidebar → khối **🔐 Service Account** → **thả (các) file JSON** vào ô upload → **➕ Thêm SA đã chọn**.
   - Thấy dòng SA màu 🟢 kèm quota `dùng 0/200 hôm nay` là OK.
3. Dán danh sách URL đầy đủ (mỗi dòng 1 URL, có `https://`) vào ô lớn.
4. Chọn **Loại thông báo**: `URL_UPDATED` (đẩy index / báo crawl lại) hoặc `URL_DELETED` (báo trang đã gỡ).
5. Bấm **🚀 Bắt đầu đẩy index**.

**Kết quả:** bảng ✅/❌ theo từng URL, SA nào đã dùng, mã lỗi, và **quota còn lại hôm nay**. Export CSV / Excel.

### 5.4. Đẩy số lượng lớn (>200 URL/ngày)

- Mỗi SA = +200 URL/ngày. Cần 1.000 URL/ngày → tạo **5 SA** (5 project hoặc 5 SA), thêm tất cả vào tool.
- Tool **tự xoay**: SA hết quota (hoặc lỗi 429) → tự chuyển sang SA kế tiếp.
- Nhớ cấp **Owner** cho **mọi SA** trên các domain tương ứng, nếu không SA đó sẽ báo 403.
- Quota `used_today` được lưu lại và **reset theo ngày** — mở lại app vẫn thấy đã dùng bao nhiêu.

### 5.5. Lỗi thường gặp khi đẩy

| Mã | Nghĩa | Cách xử lý |
|----|-------|-----------|
| **403** | SA chưa là Owner của domain này trong Search Console | Làm lại mục **5.2** cho đúng domain |
| **429** | SA hết quota 200/ngày hoặc bị rate-limit | Thêm SA, hoặc chờ hôm sau; tool tự xoay SA khác |
| **401** | File JSON sai / key bị thu hồi | Tạo lại JSON key, xóa SA cũ, thêm lại |
| **400** | URL sai định dạng | URL phải đầy đủ `https://...` |
| "Hết quota tất cả account" | Mọi SA đã hết 200/ngày | Thêm SA mới |

---

## 6. Quản lý key & Service Account

- **Serper key** lưu trong `keys.json`; **Service Account** lưu trong `service_accounts.json`. Cả hai đã được `.gitignore` — **không bị chia sẻ khi đóng gói**.
- Thêm/xóa/đặt nhãn trực tiếp trên sidebar. Nút **🧪 Test** kiểm tra key Serper còn sống (tốn 1 credit/key).
- Key/SA lỗi được **đánh dấu 🔴** (không tự xóa) để bạn tự quyết xóa.

---

## 7. Kiến trúc

| File | Vai trò |
|------|---------|
| `app.py` | Giao diện Streamlit (4 chế độ) |
| `pipeline.py` | Điều phối luồng crawl + đa luồng + progress |
| `providers.py` | `SerperProvider` — nhiều key + xoay vòng, phân trang `site:` |
| `extractors.py` | Parse SERP, trích keyword, scrape HTML |
| `index_checker.py` | Logic Check Index (`site:`) + gom nhóm domain gốc |
| `url_checker.py` | Logic Check URL (HTTP sống/chết) + gom nhóm domain |
| `link_extractor.py` | Bóc link stacking/bio-about trỏ ra ngoài (lọc nội bộ/menu/footer/ads, dofollow/nofollow, vị trí) |
| `url_types.py` | Nhận diện domain rút gọn / link-in-bio / paste / pad để bỏ qua không check |
| `index_pusher.py` | Logic Đẩy Index (Google Indexing API) + xoay Service Account |
| `key_store.py` | Lưu/test/quản lý Serper key |
| `filters.py` | Blacklist / whitelist domain |
| `cache.py` | Cache file tiết kiệm credit |
| `exporters.py` | Xuất Markdown / CSV / Excel cho cả 4 chế độ |
| `config.py`, `models.py` | Cấu hình & kiểu dữ liệu |

---

## 8. Bảo mật

- File nhạy cảm **không bao giờ commit / chia sẻ**: `.env`, `keys.json`, `service_accounts.json` (đã trong `.gitignore`).
- File Service Account JSON chứa **private key** — rò rỉ = người khác đẩy index thay bạn. Giữ cẩn thận.
- Khi đóng gói chia sẻ tool, chỉ gửi code + `.env.example`, **không** kèm các file trên.
