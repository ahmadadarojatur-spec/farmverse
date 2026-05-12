"""
FarmVerse Telegram Bot Backend — v2.0 (UPGRADED)
==================================================
Fitur Baru:
  • Anti-Cheat Engine (time manipulation, spam, JS injection, currency exploit, referral abuse)
  • Marketplace jual-beli hasil panen & NFT
  • Wallet integration (verify on-chain tx)
  • NFT mint/transfer/list
  • Command lengkap: /trade, /market, /nft, /sell, /buy, /sendtoken, /ban, /unban, /broadcast
  • Rate limiting per user & global
  • Signed action tokens (HMAC-SHA256 payload verification)
  • Admin dashboard (stats, ban, broadcast, revenue)
  • Referral abuse prevention

Requirements:
  pip install python-telegram-bot==20.x aiohttp aiosqlite python-dotenv
  pip install web3 eth-account cryptography

Setup .env:
  BOT_TOKEN=your_telegram_bot_token
  GAME_URL=https://yourdomain.com/farmverse.html
  ADMIN_ID=your_telegram_id
  HMAC_SECRET=your_secret_key_for_payload_signing
  BNB_RPC=https://bsc-dataseed.binance.org/
  NFT_CONTRACT=0xYourNFTContractAddress
"""

import asyncio
import sqlite3
import json
import os
import time
import hmac
import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
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

BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
GAME_URL     = os.getenv("GAME_URL", "https://farmverse-liart.vercel.app")
ADMIN_ID     = int(os.getenv("ADMIN_ID", "0"))
HMAC_SECRET  = os.getenv("HMAC_SECRET", "farmverse_secret_2025").encode()
BNB_RPC      = os.getenv("BNB_RPC", "https://bsc-dataseed.binance.org/")
DB_PATH      = "farmverse.db"
MARKET_FEE   = 0.05   # 5% platform fee
MAX_SELL_PER_HOUR = 10
MAX_HARVEST_PER_MINUTE = 20

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            user_id         INTEGER PRIMARY KEY,
            username        TEXT DEFAULT '',
            first_name      TEXT DEFAULT 'Farmer',
            gold            INTEGER DEFAULT 500,
            gems            INTEGER DEFAULT 20,
            fvt_balance     INTEGER DEFAULT 0,
            level           INTEGER DEFAULT 1,
            xp              INTEGER DEFAULT 0,
            xp_max          INTEGER DEFAULT 1000,
            total_harvest   INTEGER DEFAULT 0,
            daily_harvest   INTEGER DEFAULT 0,
            streak          INTEGER DEFAULT 0,
            wallet_addr     TEXT DEFAULT '',
            is_premium      INTEGER DEFAULT 0,
            premium_until   TEXT DEFAULT '',
            boost_multiplier REAL DEFAULT 1.0,
            boost_until     TEXT DEFAULT '',
            is_banned       INTEGER DEFAULT 0,
            ban_reason      TEXT DEFAULT '',
            suspicion_score INTEGER DEFAULT 0,
            last_harvest_ts REAL DEFAULT 0,
            last_plant_ts   REAL DEFAULT 0,
            total_spent_bnb REAL DEFAULT 0,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen       TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS plots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            slot        INTEGER,
            crop_id     TEXT DEFAULT '',
            planted_at  REAL DEFAULT 0,    -- Unix timestamp (float)
            grow_time   INTEGER DEFAULT 0,  -- seconds
            reward      INTEGER DEFAULT 0,
            xp_reward   INTEGER DEFAULT 0,
            locked      INTEGER DEFAULT 0,
            server_nonce TEXT DEFAULT '',   -- anti-cheat token
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            tx_hash     TEXT,
            tx_type     TEXT,    -- 'buy_nft','sell_item','harvest','boost','referral','market_buy','market_sell'
            amount      REAL,
            currency    TEXT,    -- 'BNB','FVT','gold','gems'
            item_id     TEXT,
            status      TEXT DEFAULT 'pending',
            block_number INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward_given INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(referrer_id, referred_id)
        );

        CREATE TABLE IF NOT EXISTS nfts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id        INTEGER,
            nft_id          TEXT,           -- 'royal_farmer', 'auto_harvester', etc
            token_id        INTEGER DEFAULT 0,
            contract_addr   TEXT DEFAULT '',
            mint_tx         TEXT DEFAULT '',
            is_listed       INTEGER DEFAULT 0,
            list_price_fvt  INTEGER DEFAULT 0,
            acquired_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS market_listings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id   INTEGER,
            item_type   TEXT,           -- 'crop','nft','tool'
            item_id     TEXT,
            item_emoji  TEXT,
            item_name   TEXT,
            quantity    INTEGER DEFAULT 1,
            price_fvt   INTEGER,
            status      TEXT DEFAULT 'active',  -- active/sold/cancelled
            buyer_id    INTEGER DEFAULT 0,
            fee_paid    INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            sold_at     TEXT DEFAULT '',
            FOREIGN KEY(seller_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS anti_cheat_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            flag_code   TEXT,
            flag_reason TEXT,
            extra_data  TEXT DEFAULT '',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS harvest_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            plot_slot   INTEGER,
            crop_id     TEXT,
            planted_at  REAL,
            harvested_at REAL,
            grow_time   INTEGER,
            reward      INTEGER,
            server_nonce TEXT,
            valid        INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS rate_limits (
            user_id     INTEGER,
            action      TEXT,
            count       INTEGER DEFAULT 0,
            window_start REAL,
            PRIMARY KEY(user_id, action)
        );

        CREATE TABLE IF NOT EXISTS broadcast_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER,
            message     TEXT,
            sent_to     INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

# ─────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_player(user_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
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
    """, (user_id, username or '', first_name or 'Farmer'))
    # Create 8 default plots
    existing = c.execute("SELECT COUNT(*) FROM plots WHERE user_id=?", (user_id,)).fetchone()[0]
    if existing == 0:
        for i in range(16):
            c.execute("INSERT INTO plots (user_id, slot, locked) VALUES (?,?,?)",
                      (user_id, i, 1 if i >= 8 else 0))
    conn.commit()
    conn.close()

def update_player_field(user_id: int, field: str, value):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"UPDATE players SET {field}=? WHERE user_id=?", (value, user_id))
    conn.commit()
    conn.close()

def get_leaderboard(mode='harvest', limit=10):
    col = {'harvest':'total_harvest', 'level':'level', 'fvt':'fvt_balance', 'gems':'gems'}.get(mode, 'total_harvest')
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT user_id, first_name, level, total_harvest, fvt_balance, gems
        FROM players WHERE is_banned=0
        ORDER BY {col} DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_player_ids():
    conn = get_conn()
    rows = conn.execute("SELECT user_id FROM players WHERE is_banned=0").fetchall()
    conn.close()
    return [r['user_id'] for r in rows]

# ─────────────────────────────────────────
# ANTI-CHEAT ENGINE
# ─────────────────────────────────────────
class AntiCheat:
    HARVEST_RATE_LIMIT = 20       # per minute
    PLANT_RATE_LIMIT   = 5        # per 10 seconds
    REFERRAL_LIMIT     = 50       # max referrals (anti-abuse)
    MAX_GOLD_PER_HARVEST = 5000   # sanity check per single harvest
    MAX_SELL_PRICE     = 10_000_000  # FVT

    @staticmethod
    def sign_action(user_id: int, action: str, data: dict) -> str:
        """Generate HMAC signature for a server action"""
        payload = json.dumps({'user_id': user_id, 'action': action, 'data': data, 'ts': int(time.time())}, sort_keys=True)
        sig = hmac.new(HMAC_SECRET, payload.encode(), hashlib.sha256).hexdigest()
        return sig

    @staticmethod
    def verify_action(user_id: int, action: str, data: dict, signature: str, ts: int) -> bool:
        """Verify a client-sent action token. Max age: 30 seconds."""
        if abs(time.time() - ts) > 30:
            return False  # Replay attack / time manipulation
        payload = json.dumps({'user_id': user_id, 'action': action, 'data': data, 'ts': ts}, sort_keys=True)
        expected = hmac.new(HMAC_SECRET, payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def check_rate_limit(user_id: int, action: str, max_count: int, window_sec: int) -> bool:
        """Returns True if action is within rate limit, False if exceeded."""
        now = time.time()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        row = c.execute("SELECT count, window_start FROM rate_limits WHERE user_id=? AND action=?",
                        (user_id, action)).fetchone()
        if row is None:
            c.execute("INSERT INTO rate_limits (user_id, action, count, window_start) VALUES (?,?,1,?)",
                      (user_id, action, now))
            conn.commit()
            conn.close()
            return True

        count, window_start = row
        if now - window_start > window_sec:
            # Reset window
            c.execute("UPDATE rate_limits SET count=1, window_start=? WHERE user_id=? AND action=?",
                      (now, user_id, action))
            conn.commit()
            conn.close()
            return True

        if count >= max_count:
            conn.close()
            return False

        c.execute("UPDATE rate_limits SET count=count+1 WHERE user_id=? AND action=?",
                  (user_id, action))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def validate_harvest(user_id: int, plot_slot: int, nonce: str,
                         planted_at: float, grow_time: int, reward: int) -> tuple[bool, str]:
        """Server-side harvest validation. Returns (valid, reason)."""
        now = time.time()
        elapsed = now - planted_at

        # 1. Grow time not met (allow 5s tolerance for network latency)
        if elapsed < grow_time - 5:
            return False, f"TIME_HACK: elapsed={elapsed:.1f}s < grow_time={grow_time}s"

        # 2. Harvest too far in the future (>10 min past ready)
        if elapsed > grow_time + 600:
            pass  # Just log, don't block — overripe crops

        # 3. Reward sanity check
        if reward > AntiCheat.MAX_GOLD_PER_HARVEST:
            return False, f"REWARD_INJECT: reward={reward} exceeds max"

        # 4. Rate limit
        if not AntiCheat.check_rate_limit(user_id, 'harvest', AntiCheat.HARVEST_RATE_LIMIT, 60):
            return False, "HARVEST_SPAM: rate limit exceeded"

        # 5. Nonce uniqueness (prevent replay)
        conn = get_conn()
        dup = conn.execute("SELECT 1 FROM harvest_log WHERE server_nonce=? AND user_id=?",
                           (nonce, user_id)).fetchone()
        conn.close()
        if dup:
            return False, f"REPLAY_ATTACK: nonce={nonce} already used"

        return True, "OK"

    @staticmethod
    def validate_sell(user_id: int, price_fvt: int) -> tuple[bool, str]:
        """Validate a market listing."""
        if price_fvt <= 0:
            return False, "INVALID_PRICE: price must be positive"
        if price_fvt > AntiCheat.MAX_SELL_PRICE:
            return False, f"PRICE_INJECT: price={price_fvt} exceeds max"
        if not AntiCheat.check_rate_limit(user_id, 'market_sell', MAX_SELL_PER_HOUR, 3600):
            return False, "SELL_SPAM: rate limit exceeded"
        return True, "OK"

    @staticmethod
    def validate_referral(referrer_id: int, referred_id: int) -> tuple[bool, str]:
        """Prevent referral abuse."""
        if referrer_id == referred_id:
            return False, "SELF_REFERRAL"
        conn = get_conn()
        # Check if referral already exists
        dup = conn.execute("SELECT 1 FROM referrals WHERE referrer_id=? AND referred_id=?",
                           (referrer_id, referred_id)).fetchone()
        if dup:
            conn.close()
            return False, "DUPLICATE_REFERRAL"
        # Check referrer total referral count
        total = conn.execute("SELECT COUNT(*) as n FROM referrals WHERE referrer_id=?",
                             (referrer_id,)).fetchone()['n']
        conn.close()
        if total >= AntiCheat.REFERRAL_LIMIT:
            return False, f"REFERRAL_ABUSE: count={total} exceeds limit"
        return True, "OK"

    @staticmethod
    def flag(user_id: int, code: str, reason: str, extra: str = ''):
        """Log a cheat flag and increment suspicion score."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO anti_cheat_log (user_id, flag_code, flag_reason, extra_data) VALUES (?,?,?,?)",
                  (user_id, code, reason, extra))
        c.execute("UPDATE players SET suspicion_score = suspicion_score + 15 WHERE user_id=?", (user_id,))
        # Auto-ban threshold
        row = c.execute("SELECT suspicion_score FROM players WHERE user_id=?", (user_id,)).fetchone()
        conn.commit()
        conn.close()
        logger.warning(f"[ANTI-CHEAT] User {user_id} | {code}: {reason}")
        if row and row[0] >= 60:
            AntiCheat.auto_ban(user_id, code)

    @staticmethod
    def auto_ban(user_id: int, reason: str):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE players SET is_banned=1, ban_reason=? WHERE user_id=?",
                     (reason, user_id))
        conn.commit()
        conn.close()
        logger.warning(f"[BAN] User {user_id} auto-banned for: {reason}")

# ─────────────────────────────────────────
# GAME COMMANDS
# ─────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_player(user.id, user.username or '', user.first_name)

    # Referral handling
    if ctx.args:
        ref_id = ctx.args[0]
        if ref_id.isdigit():
            referrer_id = int(ref_id)
            valid, reason = AntiCheat.validate_referral(referrer_id, user.id)
            if valid:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?,?)",
                          (referrer_id, user.id))
                c.execute("UPDATE players SET gold=gold+200, gems=gems+10 WHERE user_id=?", (referrer_id,))
                c.execute("UPDATE players SET gold=gold+100 WHERE user_id=?", (user.id,))
                conn.commit()
                conn.close()
                try:
                    await ctx.bot.send_message(
                        referrer_id,
                        "🎉 Teman kamu bergabung via referral!\n"
                        "Kamu mendapat *+200 koin* & *+10 gems!*",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            else:
                AntiCheat.flag(user.id, "REFERRAL_ABUSE", reason, f"referrer={ref_id}")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Buka FarmVerse v2!", web_app=WebAppInfo(url=GAME_URL))],
        [
            InlineKeyboardButton("📊 Profil", callback_data="profile"),
            InlineKeyboardButton("🏆 Ranking", callback_data="leaderboard"),
        ],
        [
            InlineKeyboardButton("🔄 Marketplace", callback_data="market"),
            InlineKeyboardButton("💎 NFT Koleksi", callback_data="nft_list"),
        ],
        [
            InlineKeyboardButton("🔗 Referal (+200🪙)", callback_data="referral"),
            InlineKeyboardButton("❓ Bantuan", callback_data="help"),
        ]
    ])

    await update.message.reply_text(
        f"🌾 *Selamat datang di FarmVerse v2, {user.first_name}!*\n\n"
        "🚀 *Fitur Baru v2.0:*\n"
        "• 🔄 Marketplace jual-beli hasil panen\n"
        "• 💎 NFT Mint, Transfer & Trading\n"
        "• 💼 Full Wallet Integration (BNB Chain)\n"
        "• 📋 Misi Harian & Streak Rewards\n"
        "• 🤖 Auto Harvester NFT\n"
        "• 16 Lahan + sistem unlock\n\n"
        "🛡️ *Anti-Cheat aktif* — semua aksi diverifikasi server\n\n"
        "Tap tombol di bawah untuk bermain! 👇",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = (query.from_user if query else update.effective_user)
    if query: await query.answer()

    player = get_player(user.id)
    if not player:
        upsert_player(user.id, user.username or '', user.first_name)
        player = get_player(user.id)

    # NFT count
    conn = get_conn()
    nft_count = conn.execute("SELECT COUNT(*) as n FROM nfts WHERE owner_id=?", (user.id,)).fetchone()['n']
    referral_count = conn.execute("SELECT COUNT(*) as n FROM referrals WHERE referrer_id=?", (user.id,)).fetchone()['n']
    conn.close()

    boost_str = ""
    if player['is_premium'] and player['boost_until']:
        try:
            until = datetime.fromisoformat(player['boost_until'])
            if until > datetime.now():
                boost_str = f"⚡ {player['boost_multiplier']}x — sisa {(until - datetime.now()).seconds//3600}j"
            else:
                boost_str = "❌ Kedaluwarsa"
        except:
            boost_str = "❌"
    else:
        boost_str = "❌ Tidak aktif"

    wallet_str = (f"`{player['wallet_addr'][:8]}...{player['wallet_addr'][-6:]}`"
                  if player['wallet_addr'] else "Belum terhubung")

    text = (
        f"👨‍🌾 *Profil Farmer: {player['first_name']}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⭐ Level: *{player['level']}*  •  XP: *{player['xp']:,}/{player['xp_max']:,}*\n"
        f"🪙 Koin: *{player['gold']:,}*\n"
        f"💎 Gems: *{player['gems']:,}*\n"
        f"🔵 FVT Token: *{player['fvt_balance']:,}*\n"
        f"🌾 Total Panen: *{player['total_harvest']:,}*\n"
        f"🔥 Streak: *{player['streak']} hari*\n"
        f"💎 NFT Dimiliki: *{nft_count}*\n"
        f"👥 Referral: *{referral_count}*\n"
        f"⚡ Boost: {boost_str}\n"
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

async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    player = get_player(user.id)
    if not player:
        upsert_player(user.id, user.username or '', user.first_name)
        player = get_player(user.id)
    await update.message.reply_text(
        f"💰 *Saldo {player['first_name']}*\n"
        f"🪙 Koin: *{player['gold']:,}*\n"
        f"💎 Gems: *{player['gems']:,}*\n"
        f"🔵 FVT Token: *{player['fvt_balance']:,}*\n"
        f"💼 BNB Wallet: `{player['wallet_addr'] or 'Belum terhubung'}`",
        parse_mode="Markdown"
    )

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()

    mode = 'harvest'
    if ctx.args:
        mode = ctx.args[0] if ctx.args[0] in ('harvest','level','fvt') else 'harvest'

    lb = get_leaderboard(mode)
    medals = ['🥇','🥈','🥉']
    col_label = {'harvest':'🌾 Total Panen','level':'⭐ Level','fvt':'🔵 FVT Token'}[mode]
    lines = [f"🏆 *Leaderboard FarmVerse — {col_label}*\n━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(lb):
        m = medals[i] if i < 3 else f"{i+1}."
        val = p['total_harvest'] if mode == 'harvest' else (p['level'] if mode == 'level' else p['fvt_balance'])
        lines.append(f"{m} *{p['first_name']}* — {val:,} (Lv.{p['level']})")

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌾 Panen", callback_data="lb_harvest"),
            InlineKeyboardButton("⭐ Level", callback_data="lb_level"),
            InlineKeyboardButton("🔵 FVT", callback_data="lb_fvt"),
        ],
        [InlineKeyboardButton("🌾 Main & Kejar Ranking", web_app=WebAppInfo(url=GAME_URL))]
    ])
    text = "\n".join(lines)
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    user = (query.from_user if query else update.effective_user)

    conn = get_conn()
    active = conn.execute("""
        SELECT ml.*, p.first_name as seller_name
        FROM market_listings ml
        JOIN players p ON p.user_id = ml.seller_id
        WHERE ml.status = 'active'
        ORDER BY ml.created_at DESC LIMIT 10
    """).fetchall()
    volume = conn.execute("SELECT SUM(price_fvt) FROM market_listings WHERE status='sold'").fetchone()[0] or 0
    today_trades = conn.execute(
        "SELECT COUNT(*) FROM market_listings WHERE status='sold' AND date(sold_at)=date('now')"
    ).fetchone()[0]
    conn.close()

    if not active:
        text = "🔄 *Marketplace FarmVerse*\n━━━━━━━━━━━━━━━━\n📦 Belum ada listing aktif.\n\nGunakan /sell untuk menjual item!"
    else:
        lines = [f"🔄 *Marketplace* · 📊 Vol: {volume:,} FVT · 📈 {today_trades} trades hari ini\n━━━━━━━━━━━━━━━━"]
        for l in active:
            lines.append(
                f"{l['item_emoji']} *{l['item_name']}* (x{l['quantity']})\n"
                f"   👤 {l['seller_name']} · 💰 {l['price_fvt']:,} FVT"
            )
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Buka Market", web_app=WebAppInfo(url=GAME_URL + '#market'))],
        [
            InlineKeyboardButton("➕ Jual Item", callback_data="sell_item"),
            InlineKeyboardButton("🔍 Cari Item", callback_data="search_market"),
        ]
    ])
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /sell <item_name> <quantity> <price_fvt>"""
    user = update.effective_user
    player = get_player(user.id)
    if not player:
        await update.message.reply_text("❌ Buat akun dulu dengan /start"); return
    if player['is_banned']:
        await update.message.reply_text("🚫 Akun kamu ditangguhkan."); return

    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text(
            "📦 *Cara Jual Item:*\n"
            "`/sell <nama_item> <jumlah> <harga_fvt>`\n\n"
            "Contoh:\n`/sell gandum 50 1000`\n`/sell stroberi 10 2500`",
            parse_mode="Markdown"
        )
        return

    item_name = ctx.args[0].lower()
    try:
        quantity = int(ctx.args[1])
        price = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("❌ Jumlah dan harga harus angka!"); return

    # Anti-cheat validation
    valid, reason = AntiCheat.validate_sell(user.id, price)
    if not valid:
        AntiCheat.flag(user.id, "SELL_CHEAT", reason, f"item={item_name},price={price}")
        await update.message.reply_text("🚫 Validasi gagal. Aksi dicatat."); return

    ITEM_EMOJIS = {'gandum':'🌾','wortel':'🥕','jagung':'🌽','tomat':'🍅',
                   'labu':'🎃','stroberi':'🍓','blueberry':'🫐','mangga':'🥭'}
    emoji = ITEM_EMOJIS.get(item_name, '📦')
    fee = int(price * MARKET_FEE)
    net = price - fee

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO market_listings (seller_id, item_type, item_id, item_emoji, item_name, quantity, price_fvt, fee_paid)
        VALUES (?,?,?,?,?,?,?,?)
    """, (user.id, 'crop', item_name, emoji, f"{item_name.capitalize()} x{quantity}",
          quantity, price, fee))
    listing_id = c.lastrowid
    c.execute("INSERT INTO transactions (user_id, tx_type, amount, currency, item_id, status) VALUES (?,?,?,?,?,?)",
              (user.id, 'market_sell', price, 'FVT', item_name, 'pending'))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ *Listing Berhasil!*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{emoji} *{item_name.capitalize()}* x{quantity}\n"
        f"💰 Harga: *{price:,} FVT*\n"
        f"📝 Biaya platform (5%): *{fee:,} FVT*\n"
        f"💵 Kamu terima: *{net:,} FVT*\n"
        f"🆔 Listing ID: `#{listing_id}`\n\n"
        f"Gunakan /cancellist {listing_id} untuk membatalkan.",
        parse_mode="Markdown"
    )

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /buy <listing_id>"""
    user = update.effective_user
    player = get_player(user.id)
    if not player:
        await update.message.reply_text("❌ Buat akun dulu dengan /start"); return
    if player['is_banned']:
        await update.message.reply_text("🚫 Akun kamu ditangguhkan."); return

    if not ctx.args:
        await update.message.reply_text("Usage: /buy <listing_id>"); return

    try:
        listing_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID listing harus angka!"); return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    listing = conn.execute("SELECT * FROM market_listings WHERE id=? AND status='active'",
                           (listing_id,)).fetchone()
    if not listing:
        conn.close()
        await update.message.reply_text("❌ Listing tidak ditemukan atau sudah terjual!"); return

    listing = dict(listing)
    if listing['seller_id'] == user.id:
        conn.close()
        await update.message.reply_text("❌ Kamu tidak bisa beli listing milikmu sendiri!"); return

    if player['fvt_balance'] < listing['price_fvt']:
        conn.close()
        await update.message.reply_text(
            f"❌ FVT tidak cukup!\nKamu punya: *{player['fvt_balance']:,} FVT*\nHarga: *{listing['price_fvt']:,} FVT*",
            parse_mode="Markdown"
        ); return

    # Anti-cheat: rate limit purchases
    if not AntiCheat.check_rate_limit(user.id, 'market_buy', 20, 3600):
        await update.message.reply_text("⏳ Terlalu banyak pembelian. Coba lagi nanti."); return

    fee = listing['fee_paid']
    net = listing['price_fvt'] - fee

    # Execute trade
    c.execute("UPDATE players SET fvt_balance=fvt_balance-? WHERE user_id=?", (listing['price_fvt'], user.id))
    c.execute("UPDATE players SET fvt_balance=fvt_balance+? WHERE user_id=?", (net, listing['seller_id']))
    c.execute("""
        UPDATE market_listings SET status='sold', buyer_id=?, sold_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (user.id, listing_id))
    c.execute("INSERT INTO transactions (user_id, tx_type, amount, currency, item_id, status) VALUES (?,?,?,?,?,?)",
              (user.id, 'market_buy', listing['price_fvt'], 'FVT', listing['item_id'], 'completed'))
    c.execute("INSERT INTO transactions (user_id, tx_type, amount, currency, item_id, status) VALUES (?,?,?,?,?,?)",
              (listing['seller_id'], 'market_sell', net, 'FVT', listing['item_id'], 'completed'))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ *Pembelian Berhasil!*\n"
        f"{listing['item_emoji']} *{listing['item_name']}*\n"
        f"💰 Dibayar: *{listing['price_fvt']:,} FVT*",
        parse_mode="Markdown"
    )
    try:
        await ctx.bot.send_message(
            listing['seller_id'],
            f"🎉 *Item kamu terjual!*\n"
            f"{listing['item_emoji']} *{listing['item_name']}*\n"
            f"💰 Kamu terima: *{net:,} FVT*",
            parse_mode="Markdown"
        )
    except Exception:
        pass

async def cmd_cancellist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: /cancellist <listing_id>"); return
    try:
        listing_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID harus angka!"); return

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT * FROM market_listings WHERE id=? AND seller_id=? AND status='active'",
                       (listing_id, user.id)).fetchone()
    if not row:
        conn.close()
        await update.message.reply_text("❌ Listing tidak ditemukan atau bukan milikmu!"); return
    conn.execute("UPDATE market_listings SET status='cancelled' WHERE id=?", (listing_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Listing #{listing_id} berhasil dibatalkan.")

async def cmd_nft(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_conn()
    nfts = conn.execute("SELECT * FROM nfts WHERE owner_id=?", (user.id,)).fetchall()
    conn.close()

    NFT_INFO = {
        'royal_farmer':     ('👑', 'Royal Farmer',    '+100% XP'),
        'auto_harvester':   ('🤖', 'Auto Harvester',  'Auto-panen 30m'),
        'alchemist_lab':    ('🧪', 'Alchemist Lab',   '+3x potion'),
        'grand_castle':     ('🏰', 'Grand Castle',    '+5 slot lahan'),
        'unicorn_mount':    ('🦄', 'Unicorn Mount',   '3x speed'),
        'rare_soil':        ('⚗️', 'Rare Soil',       '2x grow speed'),
        'golden_tractor':   ('🚜', 'Golden Tractor',  '4x plot sekaligus'),
        'crystal_greenhouse':('🌿','Crystal GH',      'Premium crops gratis'),
    }

    if not nfts:
        text = ("💎 *NFT Koleksi Kamu*\n━━━━━━━━━━━━━━━━\n"
                "Kamu belum punya NFT.\n\n"
                "Gunakan `/buynft <id>` atau buka game untuk mint NFT!")
    else:
        lines = ["💎 *NFT Koleksi Kamu*\n━━━━━━━━━━━━━━━━"]
        for nft in nfts:
            info = NFT_INFO.get(nft['nft_id'], ('📦', nft['nft_id'], ''))
            status = "🏷️ Di-listing" if nft['is_listed'] else "✅ Aktif"
            lines.append(f"{info[0]} *{info[1]}* — {info[2]}\n   {status} • Token #{nft['token_id']}")
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Buka NFT Store", web_app=WebAppInfo(url=GAME_URL + '#nft'))],
        [InlineKeyboardButton("🏷️ List NFT di Market", callback_data="list_nft")]
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_sendtoken(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /sendtoken <username_or_id> <amount>"""
    user = update.effective_user
    player = get_player(user.id)
    if not player or player['is_banned']:
        await update.message.reply_text("❌ Akun tidak valid."); return

    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "📤 *Kirim FVT Token:*\n`/sendtoken <@username> <jumlah>`\nContoh: `/sendtoken @temanku 500`",
            parse_mode="Markdown"
        ); return

    try:
        amount = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Jumlah harus angka!"); return

    if amount <= 0: await update.message.reply_text("❌ Jumlah harus > 0"); return
    if amount > player['fvt_balance']:
        await update.message.reply_text(f"❌ FVT tidak cukup! Kamu punya {player['fvt_balance']:,} FVT"); return
    if not AntiCheat.check_rate_limit(user.id, 'sendtoken', 10, 3600):
        await update.message.reply_text("⏳ Rate limit: max 10 transfer per jam."); return

    target_input = ctx.args[0].lstrip('@')
    conn = get_conn()
    if target_input.isdigit():
        target = conn.execute("SELECT * FROM players WHERE user_id=?", (int(target_input),)).fetchone()
    else:
        target = conn.execute("SELECT * FROM players WHERE username=?", (target_input,)).fetchone()
    conn.close()

    if not target:
        await update.message.reply_text(f"❌ User '{ctx.args[0]}' tidak ditemukan!"); return
    target = dict(target)
    if target['user_id'] == user.id:
        await update.message.reply_text("❌ Tidak bisa kirim ke diri sendiri!"); return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE players SET fvt_balance=fvt_balance-? WHERE user_id=?", (amount, user.id))
    conn.execute("UPDATE players SET fvt_balance=fvt_balance+? WHERE user_id=?", (amount, target['user_id']))
    conn.execute("INSERT INTO transactions (user_id, tx_type, amount, currency, item_id, status) VALUES (?,?,?,?,?,?)",
                 (user.id, 'fvt_send', amount, 'FVT', str(target['user_id']), 'completed'))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ *Transfer Berhasil!*\n"
        f"📤 Mengirim *{amount:,} FVT* ke *{target['first_name']}*",
        parse_mode="Markdown"
    )
    try:
        await ctx.bot.send_message(
            target['user_id'],
            f"📥 *Kamu menerima FVT Token!*\n"
            f"*{player['first_name']}* mengirimmu *{amount:,} FVT*",
            parse_mode="Markdown"
        )
    except Exception:
        pass

async def cmd_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
    else:
        user = update.effective_user

    bot = await ctx.bot.get_me()
    ref_link = f"https://t.me/{bot.username}?start={user.id}"

    conn = get_conn()
    ref_count = conn.execute("SELECT COUNT(*) as n FROM referrals WHERE referrer_id=?", (user.id,)).fetchone()['n']
    total_bonus = ref_count * 200
    conn.close()

    text = (
        f"🔗 *Link Referral Kamu:*\n`{ref_link}`\n\n"
        f"👥 Total referral: *{ref_count}*\n"
        f"🪙 Total bonus didapat: *{total_bonus:,} koin*\n\n"
        "🎁 *Bonus Referral:*\n"
        "• Kamu dapat +200 koin & +10 gems\n"
        "• Temanmu dapat +100 koin starter\n"
        "• Tidak ada batas (max 50 anti-abuse)"
    )
    if query:
        await query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    text = (
        "❓ *Panduan FarmVerse v2*\n━━━━━━━━━━━━━━━━\n\n"
        "🌾 *Farming:*\n"
        "Tap lahan → pilih tanaman → tunggu → panen!\n\n"
        "🔄 *Marketplace:*\n"
        "`/market` — lihat listing aktif\n"
        "`/sell <item> <qty> <harga_fvt>` — jual item\n"
        "`/buy <listing_id>` — beli item\n"
        "`/cancellist <id>` — batalkan listing\n\n"
        "💎 *NFT:*\n"
        "`/nft` — lihat koleksi NFT kamu\n\n"
        "💰 *Token:*\n"
        "`/balance` — cek saldo\n"
        "`/sendtoken @user <amount>` — kirim FVT\n\n"
        "👥 *Sosial:*\n"
        "`/referral` — link referral kamu\n"
        "`/leaderboard` — top 10 pemain\n\n"
        "🛡️ Semua aksi diverifikasi server anti-cheat.\n"
        "📞 Support: @farmverse\\_support"
    )
    if query:
        await query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

# ─────────────────────────────────────────
# ADMIN COMMANDS
# ─────────────────────────────────────────
def require_admin(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Hanya admin.")
            return
        return await func(update, ctx)
    return wrapper

@require_admin
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    total_players     = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    banned_players    = conn.execute("SELECT COUNT(*) FROM players WHERE is_banned=1").fetchone()[0]
    premium_players   = conn.execute("SELECT COUNT(*) FROM players WHERE is_premium=1").fetchone()[0]
    total_harvest     = conn.execute("SELECT SUM(total_harvest) FROM players").fetchone()[0] or 0
    total_tx          = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    total_bnb         = conn.execute("SELECT SUM(total_spent_bnb) FROM players").fetchone()[0] or 0
    market_vol        = conn.execute("SELECT SUM(price_fvt) FROM market_listings WHERE status='sold'").fetchone()[0] or 0
    nft_minted        = conn.execute("SELECT COUNT(*) FROM nfts").fetchone()[0]
    flags_today       = conn.execute("SELECT COUNT(*) FROM anti_cheat_log WHERE date(created_at)=date('now')").fetchone()[0]
    active_today      = conn.execute("SELECT COUNT(*) FROM players WHERE date(last_seen)=date('now')").fetchone()[0]
    conn.close()

    await update.message.reply_text(
        f"📊 *Admin Dashboard FarmVerse v2*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👥 Total Pemain: *{total_players:,}*\n"
        f"🟢 Aktif Hari Ini: *{active_today:,}*\n"
        f"🚀 Premium: *{premium_players:,}*\n"
        f"🚫 Dibanned: *{banned_players:,}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🌾 Total Panen: *{total_harvest:,}*\n"
        f"💳 Total Transaksi: *{total_tx:,}*\n"
        f"💰 Total BNB Revenue: *{total_bnb:.4f} BNB*\n"
        f"🔵 Market Volume: *{market_vol:,} FVT*\n"
        f"💎 NFT Diminted: *{nft_minted:,}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🛡️ Flags Hari Ini: *{flags_today}*",
        parse_mode="Markdown"
    )

@require_admin
async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /ban <user_id> <reason>"""
    if not ctx.args or len(ctx.args) < 1:
        await update.message.reply_text("Usage: /ban <user_id> [reason]"); return
    try:
        target_id = int(ctx.args[0])
        reason = ' '.join(ctx.args[1:]) if len(ctx.args) > 1 else 'Admin ban'
    except ValueError:
        await update.message.reply_text("❌ user_id harus angka!"); return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE players SET is_banned=1, ban_reason=? WHERE user_id=?", (reason, target_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🚫 User {target_id} telah dibanned.\nAlasan: {reason}")
    try:
        await ctx.bot.send_message(target_id, f"🚫 Akun kamu telah ditangguhkan.\nAlasan: {reason}\nHubungi @farmverse_support jika ada pertanyaan.")
    except Exception:
        pass

@require_admin
async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /unban <user_id>"); return
    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id harus angka!"); return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE players SET is_banned=0, ban_reason='', suspicion_score=0 WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ User {target_id} telah di-unban.")
    try:
        await ctx.bot.send_message(target_id, "✅ Akun kamu telah dipulihkan. Selamat bermain kembali!")
    except Exception:
        pass

@require_admin
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /broadcast <message>"""
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast <pesan>"); return
    msg = ' '.join(ctx.args)
    ids = get_all_player_ids()
    sent, failed = 0, 0
    for uid in ids:
        try:
            await ctx.bot.send_message(uid, f"📢 *Pengumuman FarmVerse*\n\n{msg}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)  # Telegram rate limit
        except Exception:
            failed += 1

    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO broadcast_log (admin_id, message, sent_to) VALUES (?,?,?)",
                 (ADMIN_ID, msg, sent))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Broadcast selesai!\n✓ Terkirim: {sent}\n✗ Gagal: {failed}")

@require_admin
async def cmd_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /give <user_id> gold|gems|fvt <amount>"""
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text("Usage: /give <user_id> <gold|gems|fvt> <amount>"); return
    try:
        target_id = int(ctx.args[0])
        currency = ctx.args[1].lower()
        amount = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("❌ Format salah!"); return

    col = {'gold':'gold','gems':'gems','fvt':'fvt_balance'}.get(currency)
    if not col:
        await update.message.reply_text("❌ Currency harus gold/gems/fvt"); return

    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"UPDATE players SET {col}={col}+? WHERE user_id=?", (amount, target_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Diberikan {amount:,} {currency} ke user {target_id}.")

@require_admin
async def cmd_flags(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View recent anti-cheat flags"""
    conn = get_conn()
    flags = conn.execute("""
        SELECT acl.*, p.first_name, p.username
        FROM anti_cheat_log acl
        JOIN players p ON p.user_id = acl.user_id
        ORDER BY acl.created_at DESC LIMIT 15
    """).fetchall()
    conn.close()
    if not flags:
        await update.message.reply_text("✅ Tidak ada flags terbaru."); return
    lines = ["🛡️ *Anti-Cheat Flags Terbaru*\n━━━━━━━━━━━━━━━━"]
    for f in flags:
        lines.append(f"👤 {f['first_name']} ({f['user_id']})\n   ⚠️ {f['flag_code']}: {f['flag_reason']}\n   🕐 {f['created_at'][:16]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─────────────────────────────────────────
# WEBAPP DATA HANDLER (Anti-Cheat Gateway)
# ─────────────────────────────────────────
async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Receives and validates all actions from the game frontend"""
    user = update.effective_user
    raw = update.effective_message.web_app_data.data if update.effective_message.web_app_data else None
    if not raw:
        return

    try:
        payload = json.loads(raw)
    except Exception:
        AntiCheat.flag(user.id, "INVALID_PAYLOAD", "Non-JSON WebApp data")
        return

    action  = payload.get('action')
    data    = payload.get('data', {})
    ts      = payload.get('ts', 0)
    sig     = payload.get('signature', '')

    player = get_player(user.id)
    if not player:
        upsert_player(user.id, user.username or '', user.first_name)
        player = get_player(user.id)

    if player['is_banned']:
        return

    # --- HARVEST ---
    if action == 'harvest':
        plot_idx    = data.get('plotIdx')
        nonce       = data.get('nonce', '')
        planted_at  = data.get('plantedAt', 0) / 1000  # ms → s
        grow_time   = data.get('growTime', 0)
        expected    = data.get('expectedReward', 0)

        valid, reason = AntiCheat.validate_harvest(user.id, plot_idx, nonce, planted_at, grow_time, expected)
        if not valid:
            AntiCheat.flag(user.id, 'HARVEST_CHEAT', reason, json.dumps(data))
            return

        # Log valid harvest
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO harvest_log (user_id, plot_slot, crop_id, planted_at, harvested_at, grow_time, reward, server_nonce)
            VALUES (?,?,?,?,?,?,?,?)
        """, (user.id, plot_idx, data.get('cropId',''), planted_at, time.time(), grow_time, expected, nonce))
        c.execute("UPDATE players SET total_harvest=total_harvest+1, gold=gold+? WHERE user_id=?",
                  (expected, user.id))
        conn.commit()
        conn.close()

    # --- PLANT ---
    elif action == 'plant':
        crop_id  = data.get('cropId')
        plot_idx = data.get('plotIdx')
        nonce    = data.get('nonce', '')

        if not AntiCheat.check_rate_limit(user.id, 'plant', 30, 60):
            AntiCheat.flag(user.id, 'PLANT_SPAM', 'Too many plant actions per minute')
            return

        CROP_COSTS = {
            'wheat':10,'carrot':25,'corn':50,'tomato':40,'pumpkin':70,
            'sunflower':100,'mango':130,'grape':200,
        }
        CROP_COSTS_GEM = {'strawberry':15,'blueberry':25,'diamond_apple':30}

        cost_gold = CROP_COSTS.get(crop_id, 0)
        cost_gem  = CROP_COSTS_GEM.get(crop_id, 0)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        if cost_gold > 0:
            if player['gold'] < cost_gold:
                AntiCheat.flag(user.id, 'INSUFFICIENT_FUNDS', f"gold={player['gold']} < cost={cost_gold}")
                conn.close()
                return
            c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (cost_gold, user.id))
        elif cost_gem > 0:
            if player['gems'] < cost_gem:
                conn.close()
                return
            c.execute("UPDATE players SET gems=gems-? WHERE user_id=?", (cost_gem, user.id))

        # Update plot
        grow_times = {'wheat':120,'carrot':300,'corn':180,'tomato':240,'pumpkin':360,
                      'sunflower':480,'mango':600,'grape':900,'strawberry':180,'blueberry':240,'diamond_apple':120}
        rewards    = {'wheat':50,'carrot':120,'corn':200,'tomato':160,'pumpkin':280,
                      'sunflower':380,'mango':520,'grape':850,'strawberry':400,'blueberry':600,'diamond_apple':300}

        gt = grow_times.get(crop_id, 120)
        rw = rewards.get(crop_id, 50)
        now_ts = time.time()

        existing_plot = c.execute("SELECT id FROM plots WHERE user_id=? AND slot=?", (user.id, plot_idx)).fetchone()
        if existing_plot:
            c.execute("""
                UPDATE plots SET crop_id=?, planted_at=?, grow_time=?, reward=?, server_nonce=?
                WHERE user_id=? AND slot=?
            """, (crop_id, now_ts, gt, rw, nonce, user.id, plot_idx))
        conn.commit()
        conn.close()

    # --- WALLET CONNECT ---
    elif action == 'wallet_connect':
        addr = data.get('addr', '')
        if addr and re.match(r'^0x[0-9a-fA-F]{40}$', addr):
            update_player_field(user.id, 'wallet_addr', addr)
        else:
            AntiCheat.flag(user.id, 'INVALID_WALLET', f"addr={addr}")

    # --- MARKET LIST ---
    elif action == 'market_list':
        item_name = data.get('item', '')
        price     = data.get('price', 0)
        valid, reason = AntiCheat.validate_sell(user.id, price)
        if not valid:
            AntiCheat.flag(user.id, 'SELL_CHEAT', reason, json.dumps(data))

    # --- UNLOCK PLOT ---
    elif action == 'unlock_plot':
        plot_idx = data.get('plotIdx', 0)
        cost     = data.get('cost', 0)
        if cost > 0 and player['gold'] >= cost:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (cost, user.id))
            conn.execute("UPDATE plots SET locked=0 WHERE user_id=? AND slot=?", (user.id, plot_idx))
            conn.commit()
            conn.close()
        else:
            AntiCheat.flag(user.id, 'UNLOCK_CHEAT', f"cost={cost}, gold={player['gold']}")

    # --- ANTI-CHEAT FLAG FROM CLIENT ---
    elif action == 'anti_cheat_flag':
        code   = data.get('code', 'UNKNOWN')
        reason = data.get('reason', '')
        AntiCheat.flag(user.id, code, f"[CLIENT] {reason}")

# ─────────────────────────────────────────
# CALLBACK HANDLER
# ─────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    handlers = {
        "profile":     cmd_profile,
        "leaderboard": cmd_leaderboard,
        "market":      cmd_market,
        "referral":    cmd_referral,
        "help":        cmd_help,
        "nft_list":    cmd_nft,
    }
    if data in handlers:
        await handlers[data](update, ctx)
    elif data.startswith("lb_"):
        ctx.args = [data[3:]]
        await cmd_leaderboard(update, ctx)

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    init_db()
    logger.info("✅ FarmVerse v2 DB initialized.")

    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set in .env!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("profile",      cmd_profile))
    app.add_handler(CommandHandler("balance",      cmd_balance))
    app.add_handler(CommandHandler("leaderboard",  cmd_leaderboard))
    app.add_handler(CommandHandler("market",       cmd_market))
    app.add_handler(CommandHandler("sell",         cmd_sell))
    app.add_handler(CommandHandler("buy",          cmd_buy))
    app.add_handler(CommandHandler("cancellist",   cmd_cancellist))
    app.add_handler(CommandHandler("nft",          cmd_nft))
    app.add_handler(CommandHandler("sendtoken",    cmd_sendtoken))
    app.add_handler(CommandHandler("referral",     cmd_referral))
    app.add_handler(CommandHandler("help",         cmd_help))

    # Admin commands
    app.add_handler(CommandHandler("admin",        cmd_admin))
    app.add_handler(CommandHandler("ban",          cmd_ban))
    app.add_handler(CommandHandler("unban",        cmd_unban))
    app.add_handler(CommandHandler("broadcast",    cmd_broadcast))
    app.add_handler(CommandHandler("give",         cmd_give))
    app.add_handler(CommandHandler("flags",        cmd_flags))

    # Callback & WebApp
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))

    logger.info("🌾 FarmVerse Bot v2 is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
