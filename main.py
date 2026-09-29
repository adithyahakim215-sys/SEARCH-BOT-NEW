import asyncio
import os
import re
import random
from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from pyrogram.raw.functions.contacts import ResolveUsername
from pyrogram.errors import (
    UsernameNotOccupied,
    UsernameInvalid,
    FloodWait,
    RPCError,
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PasswordHashInvalid
)

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

app = Client(
    "sniper_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

checkers = {}     
keepers = {}      
wordings = {}     
user_states = {}  

async def set_default_commands():
    commands = [
        BotCommand("start", "Tampilkan menu utama."),
        BotCommand("login", "Tambah akun checker (/login 1)"),
        BotCommand("keeper", "Tambah akun keeper"),
        BotCommand("pause", "Pause akun checker (/pause 1)"),
        BotCommand("active", "Aktifkan akun checker (/active 1)"),
        BotCommand("clear", "Logout akun checker (/clear 1)"),
        BotCommand("addcp", "Set wording jualan"),
        BotCommand("check", "Mulai auto-sniper"),
        BotCommand("keep", "Claim manual username."),
        BotCommand("stop", "Hentikan proses checker.")
    ]
    await app.set_bot_commands(commands)

# ----------------------------------------------------
# Generator Variasi Username
# ----------------------------------------------------
def gen_tamdal(base: str) -> list:
    res = []
    for i in range(1, len(base)):
        for c in "abcdefghijklmnopqrstuvwxyz":
            res.append(base[:i] + c + base[i:])
    return res

def gen_tamping(base: str) -> list:
    res = []
    for c in "abcdefghijklmnopqrstuvwxyz":
        res.append(c + base)
        res.append(base + c)
    return res

def gen_ganhur(base: str) -> list:
    res = []
    for i in range(len(base)):
        for c in "abcdefghijklmnopqrstuvwxyz":
            if c != base[i]:
                res.append(base[:i] + c + base[i+1:])
    return res

def gen_kurhur(base: str) -> list:
    res = []
    for i in range(len(base)):
        res.append(base[:i] + base[i+1:])
    return res

def gen_sop(base: str) -> list:
    res = []
    for i in range(len(base)):
        res.append(base[:i] + base[i] + base[i:])
    return res

def generate_usernames(category: str, base: str) -> list:
    category = category.lower().strip()
    base = base.lower().strip()
    
    variations = []
    if category == "tamping":
        variations = gen_tamping(base)
    elif category == "ganhur":
        variations = gen_ganhur(base)
    elif category == "mulchar":
        variations = gen_tamdal(base)
    elif category == "sop":
        variations = gen_sop(base)
    elif category == "idol":
        variations = (
            gen_tamdal(base) + 
            gen_tamping(base) + 
            gen_kurhur(base) + 
            gen_ganhur(base) + 
            gen_sop(base)
        )
    
    valid_usns = []
    for u in set(variations):
        if len(u) >= 5 and re.match(r"^[a-zA-Z0-9_]+$", u):
            valid_usns.append(u)
            
    return valid_usns

# ----------------------------------------------------
# Safe Worker Loop dengan Rotasi Akun Real-time
# ----------------------------------------------------
async def run_checker_loop(user_id: int, message: Message, targets: list, mode: str):
    keeper_client = keepers.get(user_id)
    current_idx = 0
    
    while user_states.get(user_id, {}).get("active", False):
        for usn in targets:
            if not user_states.get(user_id, {}).get("active", False):
                break

            # Dapatkan daftar akun checker yang SEDANG AKTIF saja
            active_checkers = [
                (code, acc["client"]) 
                for code, acc in checkers.get(user_id, {}).items() 
                if acc["active"]
            ]

            if not active_checkers:
                await message.reply_text("⚠️ Semua akun checker sedang di-pause atau kena limit Telegram!")
                user_states[user_id]["active"] = False
                break

            # Rotasi ke akun checker berikutnya (Round Robin)
            code, current_checker = active_checkers[current_idx % len(active_checkers)]
            current_idx += 1

            try:
                # MTProto resolve peer check
                await current_checker.invoke(ResolveUsername(username=usn))
            except UsernameNotOccupied:
                # Username AVAILABLE!
                await message.reply_text(f"🎯 **USERNAME AVAILABLE:** @{usn}")
                if keeper_client:
                    try:
                        await keeper_client.set_username(usn)
                        await message.reply_text(f"🔥 **SUCCESSFULLY CLAIMED:** @{usn} via Keeper!")
                    except RPCError as claim_err:
                        await message.reply_text(f"⚠️ Gagal claim @{usn}: {claim_err}")
            except UsernameInvalid:
                pass
            except FloodWait as e:
                # Pause otomatis akun yang terkena limit agar tidak mengirim log berulang
                checkers[user_id][code]["active"] = False
                await message.reply_text(
                    f"⚠️ Akun Checker kode **{code}** kena limit Telegram! Istirahat **{e.value}** detik.\n"
                    f"🔄 Otomatis mengalihkan ke akun checker lain yang tersedia..."
                )
            except Exception:
                pass

            # Delay acak cepat untuk mencegah rate limit dadakan
            await asyncio.sleep(random.uniform(1.2, 2.5))

        if mode == "1":
            break

    user_states.pop(user_id, None)
    await message.reply_text("🏁 **Pengecekan Selesai.**")

# ----------------------------------------------------
# Handlers Command Utama
# ----------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "👋 **Welcome to Telegram Username Checker & Sniper Bot**\n\n"
        "Available Commands:\n"
        "📱 `/login [kode]` - Tambah Akun Checker\n"
        "🛡 `/keeper` - Tambah Akun Keeper\n"
        "⏸ `/pause [kode]` - Pause akun checker\n"
        "▶️ `/active [kode]` - Aktifkan akun checker\n"
        "🗑 `/clear [kode]` - Logout akun checker\n"
        "📝 `/addcp [Teks]` - Set Wording Jualan\n"
        "🚀 `/check` - Start Auto-Sniper\n"
        "🎯 `/keep [usn]` - Manual Claim via Keeper\n"
        "🛑 `/stop` - Stop Checker"
    )

@app.on_message(filters.command("login") & filters.private)
async def login_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ Format salah! Gunakan: `/login [kode]`\nContoh: `/login 1` atau `/login 2`")
        return
    code = args[1]
    user_states[user_id] = {"step": "LOGIN_PHONE", "type": "checker", "code": code}
    await message.reply_text(f"📱 **Tambah Akun Checker (Kode: {code})**\nKirimkan nomor telepon (`+628xxx`):")

@app.on_message(filters.command("keeper") & filters.private)
async def keeper_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "LOGIN_PHONE", "type": "keeper"}
    await message.reply_text("🛡 **Tambah Akun Keeper**\nKirimkan nomor telepon (`+628xxx`):")

@app.on_message(filters.command("pause") & filters.private)
async def pause_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        return await message.reply_text("❌ Format: `/pause [kode]`")
    code = args[1]
    if user_id in checkers and code in checkers[user_id]:
        checkers[user_id][code]["active"] = False
        await message.reply_text(f"⏸ Akun Checker kode **{code}** berhasil di-pause!")
    else:
        await message.reply_text(f"⚠️ Akun Checker kode **{code}** tidak ditemukan.")

@app.on_message(filters.command("active") & filters.private)
async def active_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        return await message.reply_text("❌ Format: `/active [kode]`")
    code = args[1]
    if user_id in checkers and code in checkers[user_id]:
        checkers[user_id][code]["active"] = True
        await message.reply_text(f"▶️ Akun Checker kode **{code}** aktif kembali!")
    else:
        await message.reply_text(f"⚠️ Akun Checker kode **{code}** tidak ditemukan.")

@app.on_message(filters.command("clear") & filters.private)
async def clear_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        return await message.reply_text("❌ Format: `/clear [kode]`")
    code = args[1]
    if user_id in checkers and code in checkers[user_id]:
        acc = checkers[user_id].pop(code)
        try:
            await acc["client"].log_out()
        except Exception:
            pass
        await message.reply_text(f"🗑 Akun Checker kode **{code}** berhasil di-logout!")
    else:
        await message.reply_text(f"⚠️ Akun Checker kode **{code}** tidak ditemukan.")

@app.on_message(filters.command("check") & filters.private)
async def check_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    active_checkers = [code for code, acc in checkers.get(user_id, {}).items() if acc["active"]]
    if not active_checkers:
        await message.reply_text("⚠️ Tidak ada Akun Checker yang aktif! Tambahkan beberapa akun via `/login 1`, `/login 2`, dst.")
        return

    user_states[user_id] = {"step": "SELECT_MODE"}
    await message.reply_text(
        "🚀 **PILIH MODE AUTO-SNIPER:**\n\n"
        "1️⃣ Sekali Selesai\n"
        "2️⃣ Looping Terus"
    )

@app.on_message(filters.command("stop") & filters.private)
async def stop_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in user_states:
        user_states[user_id]["active"] = False
        await message.reply_text("🛑 Auto-sniper dihentikan.")
    else:
        await message.reply_text("⚠️ Tidak ada proses running.")

# ----------------------------------------------------
# Interactive Input Handler
# ----------------------------------------------------
@app.on_message(filters.text & filters.private & ~filters.command(["start", "addcp", "check", "stop", "login", "keeper", "keep", "clear", "pause", "active"]))
async def handle_inputs(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_states:
        return

    state_info = user_states[user_id]
    step = state_info.get("step")

    if step == "LOGIN_PHONE":
        phone = message.text.strip().replace(" ", "")
        user_type = state_info.get("type", "checker")
        code = state_info.get("code", "1")
        
        user_client = Client(
            f"user_{user_id}_{code}_{phone.replace('+', '')}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        await user_client.connect()
        try:
            sent_code = await user_client.send_code(phone)
            user_states[user_id] = {
                "step": "LOGIN_OTP",
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
                "client": user_client,
                "type": user_type,
                "code": code
            }
            await message.reply_text("📩 Kirimkan kode OTP:")
        except Exception as e:
            await user_client.disconnect()
            user_states.pop(user_id, None)
            await message.reply_text(f"❌ Gagal mengirim OTP: {e}")

    elif step == "LOGIN_OTP":
        otp = message.text.strip().replace(" ", "")
        user_client = state_info["client"]
        phone = state_info["phone"]
        phone_code_hash = state_info["phone_code_hash"]
        user_type = state_info["type"]
        code = state_info.get("code")

        try:
            await user_client.sign_in(phone, phone_code_hash, otp)
            await finalize_login(user_id, user_client, user_type, code, message)
        except SessionPasswordNeeded:
            user_states[user_id]["step"] = "LOGIN_2FA"
            await message.reply_text("🔐 Kirimkan password 2FA:")
        except Exception as e:
            await user_client.disconnect()
            user_states.pop(user_id, None)
            await message.reply_text(f"❌ Gagal login: {e}")

    elif step == "LOGIN_2FA":
        password = message.text.strip()
        user_client = state_info["client"]
        user_type = state_info["type"]
        code = state_info.get("code")

        try:
            await user_client.check_password(password)
            await finalize_login(user_id, user_client, user_type, code, message)
        except Exception as e:
            await user_client.disconnect()
            user_states.pop(user_id, None)
            await message.reply_text(f"❌ Gagal login: {e}")

    elif step == "SELECT_MODE":
        choice = message.text.strip()
        if choice in ["1", "2"]:
            user_states[user_id]["mode"] = choice
            user_states[user_id]["step"] = "WAIT_LIST"
            await message.reply_text("📝 **Kirim list based-on kamu (Enter per baris):**")
        else:
            await message.reply_text("❌ Input tidak valid. Ketik 1 atau 2.")

    elif step == "WAIT_LIST":
        lines = message.text.strip().split("\n")
        targets = []

        for line in lines:
            parts = line.strip().split(" ", 1)
            if len(parts) == 2:
                cat, base = parts[0], parts[1]
                generated = generate_usernames(cat, base)
                targets.extend(generated)

        targets = list(set(targets))
        if not targets:
            await message.reply_text("❌ Tidak ada username valid (minimal 5 karakter).")
            user_states.pop(user_id, None)
            return

        mode = user_states[user_id]["mode"]
        user_states[user_id]["step"] = "RUNNING"
        user_states[user_id]["active"] = True

        await message.reply_text(
            f"⚡️ **Pengecekan Dimulai!**\n"
            f"🔹 Total Target Variasi: `{len(targets)}` USN\n"
            f"🔹 Mode: {'Sekali Selesai' if mode == '1' else 'Looping Terus'}"
        )

        asyncio.create_task(run_checker_loop(user_id, message, targets, mode))

async def finalize_login(user_id: int, user_client: Client, user_type: str, code: str, message: Message):
    me = await user_client.get_me()
    if user_type == "checker":
        if user_id not in checkers:
            checkers[user_id] = {}
        checkers[user_id][code] = {"client": user_client, "active": True}
        await message.reply_text(f"✅ Akun Checker **{code}** terhubung: **{me.first_name}**!")
    else:
        keepers[user_id] = user_client
        await message.reply_text(f"✅ Akun Keeper terhubung: **{me.first_name}**!")
    
    user_states.pop(user_id, None)

async def main():
    await app.start()
    await set_default_commands()
    print("🤖 Bot dinyalakan!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
