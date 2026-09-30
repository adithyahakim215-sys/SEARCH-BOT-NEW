import logging
import os
import re
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
    AIORateLimiter,
)

# PENTING: token di bawah ini sudah pernah ke-post di chat sebelumnya,
# anggap bocor. Revoke semua lewat @BotFather (/token -> Revoke), lalu
# taruh yang baru di environment variable BOT_TOKENS (dipisah koma),
# JANGAN hardcode lagi di source code / yang di-commit ke Git.
BOT_TOKENS = [
    t.strip() for t in os.environ.get("BOT_TOKENS", "").split(",") if t.strip()
] or [
    "8968358581:AAF_Tzv-Jq0deycd-Smlfk_7F1hYVfHGWtA",
    "8678751369:AAFRTjmgzE611ks93Ow_MXlydHYOogJ_p8Q",
    "8777059892:AAGpG_FaKc-FIQgzBjdhdM9THEZiB4hn3Ik",
    "8734862200:AAELc3snau0soMROR1T7P8-cXDaRNVinRCk",
    "8482729373:AAHmVJdcpCePk0Ya7vKuq0gWsK5MFbL4jE8",
    "7635103221:AAG1FpAOxMQAgQHpr1eqvFX0gQGdAX6Yduc",
    "8975747868:AAFI4rQQMN9YIt0tXc8Wl2bj9bWqzpN4Wpk",
    "8864620575:AAH9E4ykhoq67Znf6m-26mNCDqIpgcJT1Ww",
    "8624912748:AAFI8O98AfAhgdWj3GCA99NCbVMS0h_8GnM",
    "8585143118:AAH4P62b-SYC0ak6htrwHeSunWRqfW2nwnw",
    "8760692687:AAFtTb9b889th15Mp2lrPLI-ec6mPVOqcz8",
    "8767917166:AAHZ2b9xCYf_ezL0V22TA0K4HN_Qnw_X77k",
    "8640289531:AAE9qWy3pD5y1e2DKBgiz2mxnzAxHGn3HBQ",
    "8980988853:AAF0mlVb1_8NXYvzHTYCTw4c-gP0QEw6XhA",
    "8712178357:AAG9P5Y-E6QI9VQIyfklb8W_w7tB0SgKIqE",
    "8633728800:AAE7cGLTMi4d2LyqsZM7LFgEF1YW1ZCClfw",
]

logging.basicConfig(level=logging.INFO)


# ===================== FRAGMENT CHECKER =====================

# Judul-judul halaman "default"/homepage Fragment yang kadang muncul
# padahal kita minta halaman /username/xxx. Kalau og:title ketemu salah
# satu dari ini (atau mengandung frasa generik ini), berarti response-nya
# BUKAN halaman username yang diminta -> harus dianggap gagal & di-retry,
# bukan langsung dicap "Unknown".
_GENERIC_TITLES = (
    "buy and sell usernames",
    "just a moment",  # tanda Cloudflare challenge
    "fragment",  # og:title kosong/generic banget
)


def _is_generic_title(og_title: str) -> bool:
    t = og_title.strip().lower()
    if not t:
        return True
    return any(t == g or g in t for g in _GENERIC_TITLES)


def check_fragment(username: str) -> dict:
    """Sync version -- dipanggil lewat asyncio.to_thread (lihat
    check_fragment_async) supaya ga nge-block event loop.

    Return dict bisa punya status tambahan "retry" kalau ternyata
    responsenya halaman generic/homepage (bukan halaman username asli),
    biasanya karena kena rate-limit/anti-bot dari sisi Fragment.
    """
    username = username.lstrip("@").lower()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/html+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://fragment.com/"
    }
    try:
        response = requests.get(
            f"https://fragment.com/username/{username}",
            headers=headers, timeout=10, allow_redirects=True
        )
        soup = BeautifulSoup(response.text, "html.parser")
        og_title = ""
        og_title_tag = soup.find("meta", property="og:title")
        if og_title_tag:
            og_title = og_title_tag.get("content", "").strip()

        if _is_generic_title(og_title):
            # Ini bukan halaman username yang kita minta -> minta retry,
            # jangan dianggap "Unknown" beneran.
            return {
                "text": f"❓ *@{username}* — Unknown\n└ og:title: `{og_title}`",
                "status": "retry",
                "og_title": og_title,
            }

        if "auctions for usernames" in og_title.lower():
            return {"text": f"✅ [@{username}](https://fragment.com/username/{username})", "status": "available"}
        elif og_title.lower().startswith("buy @"):
            return {"text": f"🟡 [@{username}](https://fragment.com/username/{username})", "status": "buy"}
        elif "make an offer" in og_title.lower():
            return {"text": f"🔴 [@{username}](https://fragment.com/username/{username})", "status": "taken"}
        else:
            return {"text": f"❓ *@{username}* — Unknown\n└ og:title: `{og_title}`", "status": "unknown"}
    except Exception as e:
        return {"text": f"⚠️ *@{username}* — Error\n└ {str(e)}", "status": "error"}


# Cache sederhana biar username yang sama ga di-request ulang ke fragment.com
# tiap kali. TTL 10 menit.
_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 600

# Semaphore GLOBAL (dipakai bareng oleh ke-16 bot instance dalam 1 proses)
# yang membatasi berapa banyak request ke fragment.com yang boleh jalan
# BERSAMAAN dari total 16 bot. Kecil tapi tidak 1, supaya tetap ada
# sedikit paralelisme (penting kalau ada 1000 username sekaligus).
_FRAGMENT_CONCURRENCY = 3
_fragment_semaphore = asyncio.Semaphore(_FRAGMENT_CONCURRENCY)

# --- Pacing ADAPTIF -------------------------------------------------
# Kita tidak tahu pasti berapa rate limit asli Fragment/Cloudflare-nya,
# jadi daripada nebak angka tetap (bisa kelamaan ATAU masih kena limit),
# jarak antar-request diatur otomatis:
#   - tiap kali kena halaman generic (tanda kena limit) -> jarak
#     diperlebar (mundur/lebih hati-hati).
#   - tiap kali sukses beruntun -> jarak dipersempit sedikit-sedikit
#     (nyoba lebih cepat lagi), tapi tidak pernah di bawah batas minimum.
# Dengan begini sistem "mencari sendiri" kecepatan paling cepat yang
# masih aman, bukan kita tebak dari awal.
_INTERVAL_MIN = 0.35     # detik, secepat-cepatnya dicoba
_INTERVAL_MAX = 6.0      # detik, selambat-lambatnya kalau sering kena limit
_INTERVAL_GROW = 1.8     # dikali segini tiap kena generic (mundur cepat)
_INTERVAL_SHRINK = 0.97  # dikali segini tiap sukses (maju pelan-pelan)
_current_interval = 0.6  # nilai awal, netral (belum tau kondisi Fragment)

_last_request_at = 0.0
_pacing_lock = asyncio.Lock()

_MAX_RETRIES = 6
_RETRY_MAX_DELAY = 15.0  # batas atas delay antar-retry untuk 1 username


async def _paced_check_fragment(username: str) -> dict:
    """Jalankan check_fragment 1 kali, dengan jarak antar-request yang
    menyesuaikan diri otomatis (lihat penjelasan _current_interval di atas),
    dan dibatasi paralelisme lewat semaphore."""
    global _last_request_at, _current_interval

    async with _fragment_semaphore:
        async with _pacing_lock:
            now = asyncio.get_event_loop().time()
            wait = _current_interval - (now - _last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_request_at = asyncio.get_event_loop().time()

        result = await asyncio.to_thread(check_fragment, username)

    # Sesuaikan kecepatan berdasarkan hasil barusan.
    async with _pacing_lock:
        if result["status"] == "retry":
            _current_interval = min(_current_interval * _INTERVAL_GROW, _INTERVAL_MAX)
        else:
            _current_interval = max(_current_interval * _INTERVAL_SHRINK, _INTERVAL_MIN)

    return result


async def check_fragment_async(username: str) -> dict:
    import time
    now = time.time()
    hit = _cache.get(username)
    if hit and now - hit[1] < _CACHE_TTL:
        return hit[0]

    result = None
    for attempt in range(1, _MAX_RETRIES + 1):
        result = await _paced_check_fragment(username)

        if result["status"] != "retry":
            break

        # Kena halaman generic -> selain _current_interval otomatis naik
        # (lihat _paced_check_fragment), tambahkan juga jeda ekstra
        # khusus untuk percobaan ulang username ini.
        if attempt < _MAX_RETRIES:
            delay = min(_current_interval * attempt, _RETRY_MAX_DELAY)
            await asyncio.sleep(delay)

    # Kalau setelah SEMUA retry masih generic, JANGAN dilaporkan sebagai
    # "Unknown" (karena itu bukan hasil valid) -- laporkan sebagai gagal
    # cek, biar jelas beda dengan og:title yang beneran aneh/tidak dikenal.
    if result["status"] == "retry":
        og_title = result.get("og_title", "")
        result = {
            "text": (
                f"⚠️ *@{username}* — Gagal dicek setelah {_MAX_RETRIES}x coba "
                f"(masih kena halaman umum Fragment)\n└ og:title: `{og_title}`"
            ),
            "status": "error",
        }

    _cache[username] = (result, now)
    return result


# ===================== GENERATOR (tidak diubah) =====================

ALPHABET = "abcdefghijklmnopqrstuvwxyz"

def gen_sop(word):
    result = []
    for i, c in enumerate(word):
        new = word[:i] + c + word[i:]
        if new != word:
            result.append(new)
    return list(dict.fromkeys(result))

def gen_tamhur(word):
    result = []
    for i in range(len(word) + 1):
        for c in ALPHABET:
            new = word[:i] + c + word[i:]
            if new != word:
                result.append(new)
    return list(dict.fromkeys(result))

def gen_gahur(word):
    result = []
    for i, orig in enumerate(word):
        for c in ALPHABET:
            if c != orig:
                new = word[:i] + c + word[i+1:]
                result.append(new)
    return list(dict.fromkeys(result))

def gen_tamping(word):
    result = []
    for c in ALPHABET:
        result.append(c + word)
        result.append(word + c)
    return list(dict.fromkeys(result))

def gen_swap(word):
    result = []
    for i in range(len(word) - 1):
        lst = list(word)
        lst[i], lst[i+1] = lst[i+1], lst[i]
        new = "".join(lst)
        if new != word:
            result.append(new)
    return list(dict.fromkeys(result))

def format_list(usernames):
    return "```\n" + " ".join([f"@{u}" for u in usernames]) + "\n```"

def split_message(text, limit=4000):
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)
    return chunks


# ===================== COMMAND HANDLERS (tidak diubah) =====================

async def cmd_sop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/sop fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    result = gen_sop(word)
    text = format_list(result)
    for chunk in split_message(text):
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def cmd_tamhur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/tamhur fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    result = gen_tamhur(word)
    text = format_list(result)
    for chunk in split_message(text):
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def cmd_gahur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/gahur fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    result = gen_gahur(word)
    text = format_list(result)
    for chunk in split_message(text):
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def cmd_tamping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/tamping fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    result = gen_tamping(word)
    text = format_list(result)
    for chunk in split_message(text):
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def cmd_swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/swap fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    result = gen_swap(word)
    text = format_list(result)
    for chunk in split_message(text):
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def cmd_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/gen fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    all_results = list(dict.fromkeys(
        gen_sop(word) +
        gen_tamhur(word) +
        gen_tamping(word)
    ))
    text = format_list(all_results)
    for chunk in split_message(text):
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Cara Penggunaan Bot*\n\n"
        "*Cek Username:*\n"
        "Kirim `@username` untuk cek status di Fragment\n"
        "Bisa sekaligus banyak: `@a @b @c`\n\n"
        "*Generator Username:*\n"
        "`/sop fikar` — bentukan semi on point\n"
        "`/tamhur fikar` — tambah huruf di semua posisi\n"
        "`/gahur fikar` — ganti huruf dengan a-z\n"
        "`/tamping fikar` — tambah huruf di kiri/kanan\n"
        "`/swap fikar` — tukar huruf berdekatan\n"
        "`/gen fikar` — semua bentukan sekaligus (tanpa gahur)\n\n"
        "Setelah dapat list, salin username yang diinginkan lalu kirim ke bot untuk dicek! ✅"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ===================== HANDLE MESSAGE (logika sama, cuma await versi async) =====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    usernames = re.findall(r"@[\w]+", text)

    if not usernames:
        await update.message.reply_text(
            "⚠️ Tidak ada username yang ditemukan.\nContoh: `@fikar @gemini @grok`",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"🔍 Mengecek {len(usernames)} username, mohon tunggu...")

    summary = []
    for username in usernames:
        # AIORateLimiter di bawah otomatis antre + retry kalau kena flood
        # dari sisi TELEGRAM. Retry ke fragment.com sendiri (kalau kena
        # halaman generic/homepage) sudah ditangani di dalam
        # check_fragment_async, jadi loop ini tetap sama sederhananya.
        result = await check_fragment_async(username)
        await update.message.reply_text(result["text"], parse_mode="Markdown")
        if result["status"] in ("available", "buy"):
            summary.append(username.lstrip("@"))

    if summary:
        summary_text = "📋 *Rangkuman:*\n\n" + " ".join([f"@{u}" for u in summary])
        await update.message.reply_text(summary_text, parse_mode="Markdown")


# ===================== RUN BOT =====================

async def run_bot(token):
    app = (
        ApplicationBuilder()
        .token(token)
        # AIORateLimiter: antre otomatis biar ga ngelanggar limit resmi
        # Telegram (per-chat & global), dan auto-retry sampai 3x kalau
        # tetap kena flood-wait dari server.
        .rate_limiter(AIORateLimiter(max_retries=3))
        .build()
    )

    await app.bot.set_my_commands([
        BotCommand("sop", "Bentukan semi on point"),
        BotCommand("tamhur", "Tambah huruf di semua posisi"),
        BotCommand("gahur", "Ganti huruf dengan a-z"),
        BotCommand("tamping", "Tambah huruf di kiri/kanan"),
        BotCommand("swap", "Tukar huruf berdekatan"),
        BotCommand("gen", "Semua bentukan sekaligus (tanpa gahur)"),
        BotCommand("help", "Cara penggunaan bot"),
    ])

    app.add_handler(CommandHandler("sop", cmd_sop))
    app.add_handler(CommandHandler("tamhur", cmd_tamhur))
    app.add_handler(CommandHandler("gahur", cmd_gahur))
    app.add_handler(CommandHandler("tamping", cmd_tamping))
    app.add_handler(CommandHandler("swap", cmd_swap))
    app.add_handler(CommandHandler("gen", cmd_gen))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    return app


async def main():
    apps = await asyncio.gather(*[run_bot(token) for token in BOT_TOKENS])
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
