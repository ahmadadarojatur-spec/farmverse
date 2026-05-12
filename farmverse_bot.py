"""
FarmVerse Telegram Bot Backend
===============================
Requirements:
  pip install python-telegram-bot==20.x aiohttp aiosqlite python-dotenv

Setup .env:
  BOT_TOKEN=your_telegram_bot_token
  GAME_URL=https://yourdomain.com/farmverse.html
  ADMIN_ID=your_telegram_id
"""

import asyncio
import sqlite3
import json
import os
from datetime import datetime
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo, MenuButtonWebApp
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8722817188:AAGETX1eo79Y8OWs1hAQDseKN9tVizfJ9oo")
GAME_URL   = os.getenv("GAME_URL",  "https://farmverse-liart.vercel.app")
ADMIN_ID   = int(os.getenv("5076116827", "0"))

DB_PATH = "farmverse.db"

# ─────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            gold        INTEGER DEFAULT 500,
            gems        INTEGER DEFAULT 20,
            level       INTEGER DEFAULT 1,
            xp          INTEGER DEFAULT 0,
            total_harvest INTEGER DEFAULT 0,
            wallet_addr TEXT DEFAULT '',
            is_premium  INTEGER DEFAULT 0,
            premium_until TEXT DEFAULT '',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS plots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            slot        INTEGER,
            crop_id     TEXT DEFAULT '',
            planted_at  TEXT DEFAULT '',
            grow_time   INTEGER DEFAULT 0,
            reward      INTEGER DEFAULT 0,
            locked      INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            tx_hash     TEXT,
            tx_type     TEXT,
            amount      REAL,
            currency    TEXT,
            item_id     TEXT,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward_given INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

def get_player(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_player(user_id: int, username: str, first_name: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO players (user_id, username, first_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_seen=CURRENT_TIMESTAMP
    """, (user_id, username, first_name))
    # Create 8 default plots for new player
    existing_plots = c.execute("SELECT COUNT(*) FROM plots WHERE user_id=?", (user_id,)).fetchone()[0]
    if existing_plots == 0:
        for i in range(12):
            c.execute(
                "INSERT INTO plots (user_id, slot, locked) VALUES (?,?,?)",
                (user_id, i, 1 if i >= 8 else 0)
            )
    conn.commit()
    conn.close()

def get_leaderboard(limit=10):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    rows = c.execute(
        "SELECT user_id, first_name, level, total_harvest FROM players ORDER BY total_harvest DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────
# BOT HANDLERS
# ─────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_player(user.id, user.username or '', user.first_name)

    # Handle referral
    if ctx.args:
        ref_id = ctx.args[0]
        if ref_id.isdigit() and int(ref_id) != user.id:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            exists = c.execute(
                "SELECT 1 FROM referrals WHERE referrer_id=? AND referred_id=?",
                (int(ref_id), user.id)
            ).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO referrals (referrer_id, referred_id) VALUES (?,?)",
                    (int(ref_id), user.id)
                )
                # Bonus ke referrer
                c.execute(
                    "UPDATE players SET gold=gold+200, gems=gems+10 WHERE user_id=?",
                    (int(ref_id),)
                )
                conn.commit()
                try:
                    await ctx.bot.send_message(
                        int(ref_id),
                        "🎉 Teman kamu bergabung via referal!\n"
                        "Kamu mendapat +200 koin & +10 gems!"
                    )
                except Exception:
                    pass
            conn.close()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Buka FarmVerse!", web_app=WebAppInfo(url=GAME_URL))],
        [
            InlineKeyboardButton("📊 Profil Saya", callback_data="profile"),
            InlineKeyboardButton("🏆 Ranking", callback_data="leaderboard"),
        ],
        [
            InlineKeyboardButton("🔗 Ajak Teman (+200🪙)", callback_data="referral"),
            InlineKeyboardButton("❓ Bantuan", callback_data="help"),
        ]
    ])

    await update.message.reply_text(
        f"🌾 *Selamat datang di FarmVerse, {user.first_name}!*\n\n"
        "🌱 Game farming berbasis blockchain di Telegram!\n"
        "💎 Tanam, panen, dan kumpulkan aset NFT di BNB Chain!\n\n"
        "✨ *Fitur Utama:*\n"
        "• 🌾 Farming real-time dengan 6+ jenis tanaman\n"
        "• 💎 NFT items dengan bonus permanen\n"
        "• 🏆 Leaderboard global\n"
        "• 💼 Integrasi Web3 (MetaMask/WalletConnect)\n"
        "• ⚡ Premium P2W bundle dengan BNB\n\n"
        "Klik tombol di bawah untuk mulai bermain! 👇",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
    else:
        user = update.effective_user

    player = get_player(user.id)
    if not player:
        upsert_player(user.id, user.username or '', user.first_name)
        player = get_player(user.id)

    premium_str = "✅ Aktif" if player['is_premium'] else "❌ Tidak aktif"
    wallet_str = f"`{player['wallet_addr'][:10]}...`" if player['wallet_addr'] else "Belum terhubung"

    text = (
        f"👨‍🌾 *Profil Farmer: {player['first_name']}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⭐ Level: *{player['level']}*\n"
        f"🪙 Koin: *{player['gold']:,}*\n"
        f"💎 Gems: *{player['gems']:,}*\n"
        f"🌾 Total Panen: *{player['total_harvest']:,}*\n"
        f"🚀 Premium: {premium_str}\n"
        f"💼 Wallet: {wallet_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 Bergabung: {player['created_at'][:10]}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌾 Main Sekarang", web_app=WebAppInfo(url=GAME_URL))
    ]])
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    lb = get_leaderboard(10)
    medals = ['🥇','🥈','🥉']
    lines = ["🏆 *Top 10 Farmer FarmVerse*\n━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(lb):
        m = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{m} *{p['first_name']}* — {p['total_harvest']:,} panen (Lv.{p['level']})")
    text = "\n".join(lines)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌾 Main & Kejar Ranking", web_app=WebAppInfo(url=GAME_URL))
    ]])
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
    else:
        user = update.effective_user

    ref_link = f"https://t.me/{(await ctx.bot.get_me()).username}?start={user.id}"
    text = (
        f"🔗 *Link Referral Kamu:*\n"
        f"`{ref_link}`\n\n"
        "Bagikan link ini ke teman-temanmu!\n\n"
        "🎁 *Bonus Referral:*\n"
        "• Kamu dapat +200 koin & +10 gems\n"
        "• Temanmu dapat 100 koin starter\n"
        "• Tidak ada batas undangan!"
    )
    if query:
        await query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    text = (
        "❓ *Panduan FarmVerse*\n━━━━━━━━━━━━━━━━\n\n"
        "🌱 *Cara Bermain:*\n"
        "1. Tap lahan kosong → pilih tanaman\n"
        "2. Tunggu tanaman tumbuh\n"
        "3. Tap tanaman siap → panen!\n\n"
        "💰 *Cara Mendapat Koin:*\n"
        "• Panen tanaman\n"
        "• Selesaikan misi harian\n"
        "• Bonus referral\n\n"
        "💎 *Cara Mendapat Gems:*\n"
        "• Beli dengan BNB di Wallet\n"
        "• Level up reward\n\n"
        "🔷 *Web3 / NFT:*\n"
        "• Connect wallet di tab Wallet\n"
        "• Beli NFT di tab NFT\n"
        "• Semua transaksi di BNB Chain\n\n"
        "📞 Bantuan: @farmverse\\_support"
    )
    if query:
        await query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    total_players = c.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    premium_players = c.execute("SELECT COUNT(*) FROM players WHERE is_premium=1").fetchone()[0]
    total_harvest = c.execute("SELECT SUM(total_harvest) FROM players").fetchone()[0] or 0
    total_tx = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()
    await update.message.reply_text(
        f"📊 *Admin Stats FarmVerse*\n"
        f"👥 Total Pemain: {total_players:,}\n"
        f"🚀 Premium: {premium_players:,}\n"
        f"🌾 Total Panen: {total_harvest:,}\n"
        f"💳 Total Transaksi: {total_tx:,}",
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "profile":     await profile(update, ctx)
    elif data == "leaderboard": await leaderboard(update, ctx)
    elif data == "referral":  await referral(update, ctx)
    elif data == "help":      await help_cmd(update, ctx)

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    init_db()
    print("✅ Database initialized.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("profile",     profile))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("referral",    referral))
    app.add_handler(CommandHandler("help",        help_cmd))
    app.add_handler(CommandHandler("admin",       admin_stats))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🌾 FarmVerse Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
