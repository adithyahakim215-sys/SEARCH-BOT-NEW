import asyncio
import os
import re
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait, UsernameInvalid, UsernameOccupied

# Environment Variables
API_ID = int(os.environ.get("API_ID", "123456"))  # Replace with your API_ID
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

# Bot Setup
app = Client("sniper_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# In-memory Databases / Session Storage
checkers = {}   # {user_id: [Client_1, Client_2, ...]}
keepers = {}    # {user_id: Client_Keeper}
wordings = {}   # {user_id: text}
user_states = {} # User input state management

# ----------------------------------------------------
# 1. Username Variation Generator Logic
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
    
    # Filter valid telegram usernames (length >= 5, only alphanumeric & underscore)
    valid_usns = []
    for u in set(variations):
        if len(u) >= 5 and re.match(r"^[a-zA-Z0-9_]+$", u):
            valid_usns.append(u)
            
    return valid_usns

# ----------------------------------------------------
# 2. Bot Commands
# ----------------------------------------------------
@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "👋 **Welcome to Telegram Username Checker & Sniper Bot**\n\n"
        "Available Commands:\n"
        "📱 /login - Add Checker Account\n"
        "🛡 /keeper - Add Keeper Account (For Claiming)\n"
        "📝 /addcp [Text] - Set Wording Jualan\n"
        "🗑 /clearnoktel - Clear Phone Numbers\n"
        "🚀 /check - Start Auto-Sniper (Once / Loop)\n"
        "🎯 /keep [usn] - Manual Claim via Keeper\n"
        "🛑 /stop - Pause Checker"
    )

@app.on_message(filters.command("addcp"))
async def addcp_cmd(client: Client, message: Message):
    text = message.text.split(" ", 1)
    if len(text) < 2:
        await message.reply_text("❌ Usage: `/addcp [Your wording text]`")
        return
    wordings[message.from_user.id] = text[1]
    await message.reply_text("✅ Wording jualan successfully saved!")

@app.on_message(filters.command("check"))
async def check_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in checkers or not checkers[user_id]:
        await message.reply_text("⚠️ Please login at least 1 Checker account first using `/login`!")
        return

    user_states[user_id] = {"step": "SELECT_MODE"}
    await message.reply_text(
        "🚀 **PILIH MODE AUTO-SNIPER:**\n\n"
        "Ketik angka pilihanmu:\n"
        "1️⃣ Sekali Selesai (Scan 1 putaran lalu berhenti)\n"
        "2️⃣ Looping Terus (Scan berputar terus menerus sampai /stop)"
    )

@app.on_message(filters.text & filters.private)
async def handle_inputs(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_states:
        return

    state = user_states[user_id].get("step")

    if state == "SELECT_MODE":
        choice = message.text.strip()
        if choice in ["1", "2"]:
            user_states[user_id]["mode"] = choice
            user_states[user_id]["step"] = "WAIT_LIST"
            await message.reply_text(
                "📝 **Kirim list based-on kamu (Enter per baris):**\n\n"
                "Contoh:\n"
                "idol jaehyun\n"
                "mulchar kucing\n"
                "tamping claude"
            )
        else:
            await message.reply_text("❌ Input tidak valid. Ketik 1 atau 2.")

    elif state == "WAIT_LIST":
        lines = message.text.strip().split("\n")
        targets = []

        for line in lines:
            parts = line.strip().split(" ", 1)
            if len(parts) == 2:
                cat, base = parts[0], parts[1]
                generated = generate_usernames(cat, base)
                targets.extend(generated)

        targets = list(set(targets)) # Unique list
        if not targets:
            await message.reply_text("❌ Tidak ada username valid yang berhasil di-generate.")
            user_states.pop(user_id, None)
            return

        mode = user_states[user_id]["mode"]
        user_states[user_id]["step"] = "RUNNING"
        user_states[user_id]["active"] = True

        await message.reply_text(
            f"⚡️ **Pengecekan Dimulai!**\n"
            f"🔹 Total Variasi Target: `{len(targets)}` USN\n"
            f"🔹 Mode: {'Sekali Selesai' if mode == '1' else 'Looping Terus'}"
        )

        # Start Async Checker Process
        asyncio.create_task(run_checker_loop(user_id, message, targets, mode))

@app.on_message(filters.command("stop"))
async def stop_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in user_states:
        user_states[user_id]["active"] = False
        await message.reply_text("🛑 Auto-sniper paused/stopped.")
    else:
        await message.reply_text("⚠️ Tidak ada proses checker yang sedang berjalan.")

# ----------------------------------------------------
# 3. Main Checker & Sniper Worker Loop
# ----------------------------------------------------
async def run_checker_loop(user_id: int, message: Message, targets: list, mode: str):
    user_checkers = checkers.get(user_id, [])
    keeper_client = keepers.get(user_id)
    
    checker_idx = 0
    
    while user_states.get(user_id, {}).get("active", False):
        for usn in targets:
            if not user_states.get(user_id, {}).get("active", False):
                break

            # Round-robin selection of checker accounts to avoid rate limits
            current_checker = user_checkers[checker_idx]
            checker_idx = (checker_idx + 1) % len(user_checkers)

            try:
                # Check username availability using MTProto
                check = await current_checker.check_username(usn)
                if check:
                    await message.reply_text(f"🎯 **USERNAME AVAILABLE:** @{usn}")
                    
                    # Auto-Claim via Keeper if available
                    if keeper_client:
                        try:
                            # Note: Changing channel/account username via MTProto
                            await keeper_client.set_username(usn)
                            await message.reply_text(f"🔥 **SUCCESSFULLY CLAIMED:** @{usn} via Keeper!")
                        except Exception as claim_err:
                            await message.reply_text(f"⚠️ Failed to claim @{usn}: {claim_err}")
                            
            except UsernameOccupied:
                pass # Username is taken
            except UsernameInvalid:
                pass # Invalid username format
            except FloodWait as e:
                # Rate limited on current checker account
                await asyncio.sleep(e.value)
            except Exception as e:
                pass

            # Small delay to prevent rate limit
            await asyncio.sleep(1.5)

        if mode == "1": # Sekali Selesai
            break

    user_states.pop(user_id, None)
    await message.reply_text("🏁 **Pengecekan Selesai.**")

# Start Bot
if __name__ == "__main__":
    app.run()