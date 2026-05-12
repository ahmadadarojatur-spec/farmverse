"""
FarmVerse Telegram Bot — v3.0 (FULL REWRITE)
=============================================
FIXED:
  1.  Inventory system (tabel + helper)
  2.  Hasil panen masuk inventory
  3.  /sell sekarang nyata: cek & kurangi inventory seller
  4.  /buy memindahkan item ke inventory buyer
  5.  /cancellist mengembalikan item ke inventory seller
  6.  Plot realtime — status dikembalikan lewat sendData JSON
  7.  Harvest validation server-side + nonce dari DB plot
  8.  Clear plot setelah panen (crop_id='', planted_at=0)
  9.  Bot token: pakai load_dotenv(override=True) + validasi awal
 10.  /inventory command
 11.  SQLite WAL mode
 12.  Transaction lock (BEGIN IMMEDIATE)
 13.  XP & Level system (server-side)
 14.  Market history table + endpoint
 15.  Frontend <-> Backend: sendData JSON lengkap untuk sync state

Requirements:
  pip install python-telegram-bot==20.x aiohttp aiosqlite python-dotenv
"""

import asyncio
import json
import os
import sys
import time
import hmac
import hashlib
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=True)          # FIX #9: override=True agar nilai .env selalu menang

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ─── CONFIG ───────────────────────────────
BOT_TOKEN   = os.getenv("8722817188:AAGETX1eo79Y8OWs1hAQDseKN9tVizfJ9oo", "").strip()
GAME_URL    = os.getenv("GAME_URL", "https://farmverse-liart.vercel.app")
ADMIN_ID    = int(os.getenv("5076116827", "0"))
HMAC_SECRET = os.getenv("HMAC_SECRET", "farmverse_secret_2025").encode()
DB_PATH     = "farmverse.db"
MARKET_FEE  = 0.05
MAX_SELL_PER_HOUR   = 10
MAX_HARVEST_PER_MIN = 20

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("farmverse")

# FIX #9: Validasi token di awal, bukan di main()
if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN kosong atau tidak valid! Periksa file .env")
    sys.exit(1)

# ─── XP TABLE ─────────────────────────────
def xp_for_level(lvl: int) -> int:
    return int(1000 * (1.4 ** (lvl - 1)))

# ─── CROP DEFINITIONS (single source of truth) ────────────────────────────
CROPS = {
    'wheat':         {'cost':10,  'cost_type':'gold', 'grow':120,  'reward':50,  'xp':10,  'emoji':'🌾', 'name':'Gandum'},
    'carrot':        {'cost':25,  'cost_type':'gold', 'grow':300,  'reward':120, 'xp':20,  'emoji':'🥕', 'name':'Wortel'},
    'corn':          {'cost':50,  'cost_type':'gold', 'grow':180,  'reward':200, 'xp':30,  'emoji':'🌽', 'name':'Jagung'},
    'tomato':        {'cost':40,  'cost_type':'gold', 'grow':240,  'reward':160, 'xp':25,  'emoji':'🍅', 'name':'Tomat'},
    'pumpkin':       {'cost':70,  'cost_type':'gold', 'grow':360,  'reward':280, 'xp':40,  'emoji':'🎃', 'name':'Labu'},
    'sunflower':     {'cost':100, 'cost_type':'gold', 'grow':480,  'reward':380, 'xp':50,  'emoji':'🌻', 'name':'Bunga Matahari'},
    'mango':         {'cost':130, 'cost_type':'gold', 'grow':600,  'reward':520, 'xp':60,  'emoji':'🥭', 'name':'Mangga'},
    'grape':         {'cost':200, 'cost_type':'gold', 'grow':900,  'reward':850, 'xp':80,  'emoji':'🍇', 'name':'Anggur'},
    'strawberry':    {'cost':15,  'cost_type':'gems', 'grow':180,  'reward':400, 'xp':45,  'emoji':'🍓', 'name':'Stroberi'},
    'blueberry':     {'cost':25,  'cost_type':'gems', 'grow':240,  'reward':600, 'xp':65,  'emoji':'🫐', 'name':'Blueberry'},
    'diamond_apple': {'cost':30,  'cost_type':'gems', 'grow':120,  'reward':300, 'xp':35,  'emoji':'💎', 'name':'Diamond Apple'},
}

# ─── DB SETUP ─────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    """Context manager untuk koneksi dengan WAL + timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # FIX #11
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_exclusive(conn):
    """BEGIN IMMEDIATE untuk operasi write kritis."""  # FIX #12
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        PRAGMA journal_mode=WAL;

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
            planted_at  REAL DEFAULT 0,
            grow_time   INTEGER DEFAULT 0,
            reward      INTEGER DEFAULT 0,
            xp_reward   INTEGER DEFAULT 0,
            locked      INTEGER DEFAULT 0,
            server_nonce TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS inventory (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            item_id     TEXT NOT NULL,
            item_type   TEXT DEFAULT 'crop',
            emoji       TEXT DEFAULT '📦',
            quantity    INTEGER DEFAULT 0,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, item_id),
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS market_listings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id   INTEGER,
            item_type   TEXT,
            item_id     TEXT,
            item_emoji  TEXT,
            item_name   TEXT,
            quantity    INTEGER DEFAULT 1,
            price_fvt   INTEGER,
            status      TEXT DEFAULT 'active',
            buyer_id    INTEGER DEFAULT 0,
            fee_paid    INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            sold_at     TEXT DEFAULT '',
            FOREIGN KEY(seller_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS market_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id  INTEGER,
            seller_id   INTEGER,
            buyer_id    INTEGER,
            item_id     TEXT,
            item_name   TEXT,
            item_emoji  TEXT,
            quantity    INTEGER,
            price_fvt   INTEGER,
            fee_paid    INTEGER,
            sold_at     TEXT DEFAULT CURRENT_TIMESTAMP
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
            nft_id          TEXT,
            token_id        INTEGER DEFAULT 0,
            contract_addr   TEXT DEFAULT '',
            mint_tx         TEXT DEFAULT '',
            is_listed       INTEGER DEFAULT 0,
            list_price_fvt  INTEGER DEFAULT 0,
            acquired_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_id) REFERENCES players(user_id)
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
    logger.info("✅ DB initialized (WAL mode)")


# ─── PLAYER HELPERS ───────────────────────────────────────────────────────

def get_player(user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def upsert_player(user_id: int, username: str, first_name: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO players (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_seen=CURRENT_TIMESTAMP
        """, (user_id, username or '', first_name or 'Farmer'))
        existing = conn.execute(
            "SELECT COUNT(*) FROM plots WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        if existing == 0:
            for i in range(16):
                conn.execute(
                    "INSERT INTO plots (user_id, slot, locked) VALUES (?,?,?)",
                    (user_id, i, 1 if i >= 8 else 0)
                )


def get_leaderboard(mode='harvest', limit=10):
    col = {'harvest': 'total_harvest', 'level': 'level', 'fvt': 'fvt_balance'}.get(mode, 'total_harvest')
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT user_id, first_name, level, total_harvest, fvt_balance
            FROM players WHERE is_banned=0
            ORDER BY {col} DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_all_player_ids():
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM players WHERE is_banned=0").fetchall()
    return [r['user_id'] for r in rows]


# ─── INVENTORY HELPERS ─────────────────────────────────────────────────── FIX #1

def inv_add(conn, user_id: int, item_id: str, emoji: str, quantity: int, item_type: str = 'crop'):
    """Tambah (atau buat) item di inventory dalam koneksi yang sama."""
    conn.execute("""
        INSERT INTO inventory (user_id, item_id, item_type, emoji, quantity, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, item_id) DO UPDATE SET
            quantity = quantity + excluded.quantity,
            updated_at = CURRENT_TIMESTAMP
    """, (user_id, item_id, item_type, emoji, quantity))


def inv_take(conn, user_id: int, item_id: str, quantity: int) -> bool:
    """Kurangi quantity item. Return False jika tidak cukup."""
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE user_id=? AND item_id=?",
        (user_id, item_id)
    ).fetchone()
    if not row or row['quantity'] < quantity:
        return False
    new_qty = row['quantity'] - quantity
    if new_qty == 0:
        conn.execute(
            "DELETE FROM inventory WHERE user_id=? AND item_id=?", (user_id, item_id)
        )
    else:
        conn.execute(
            "UPDATE inventory SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND item_id=?",
            (new_qty, user_id, item_id)
        )
    return True


def get_inventory(user_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM inventory WHERE user_id=? AND quantity > 0 ORDER BY item_type, item_id",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── RATE LIMITER ─────────────────────────────────────────────────────────

def check_rate_limit(user_id: int, action: str, max_count: int, window_sec: int) -> bool:
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT count, window_start FROM rate_limits WHERE user_id=? AND action=?",
            (user_id, action)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO rate_limits (user_id, action, count, window_start) VALUES (?,?,1,?)",
                (user_id, action, now)
            )
            return True
        count, window_start = row['count'], row['window_start']
        if now - window_start > window_sec:
            conn.execute(
                "UPDATE rate_limits SET count=1, window_start=? WHERE user_id=? AND action=?",
                (now, user_id, action)
            )
            return True
        if count >= max_count:
            return False
        conn.execute(
            "UPDATE rate_limits SET count=count+1 WHERE user_id=? AND action=?",
            (user_id, action)
        )
    return True


# ─── ANTI-CHEAT ───────────────────────────────────────────────────────────

class AntiCheat:
    MAX_GOLD_PER_HARVEST = 5000
    MAX_SELL_PRICE       = 10_000_000

    @staticmethod
    def flag(user_id: int, code: str, reason: str, extra: str = ''):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO anti_cheat_log (user_id, flag_code, flag_reason, extra_data) VALUES (?,?,?,?)",
                (user_id, code, reason, extra)
            )
            conn.execute(
                "UPDATE players SET suspicion_score = suspicion_score + 15 WHERE user_id=?", (user_id,)
            )
            row = conn.execute(
                "SELECT suspicion_score FROM players WHERE user_id=?", (user_id,)
            ).fetchone()
        logger.warning(f"[AC] {user_id} | {code}: {reason}")
        if row and row['suspicion_score'] >= 60:
            AntiCheat.auto_ban(user_id, code)

    @staticmethod
    def auto_ban(user_id: int, reason: str):
        with get_conn() as conn:
            conn.execute(
                "UPDATE players SET is_banned=1, ban_reason=? WHERE user_id=?", (reason, user_id)
            )
        logger.warning(f"[BAN] {user_id} auto-banned: {reason}")

    @staticmethod
    def validate_harvest(user_id: int, plot_slot: int, nonce: str,
                         planted_at: float, grow_time: int, reward: int) -> tuple:
        now = time.time()
        elapsed = now - planted_at
        if elapsed < grow_time - 5:
            return False, f"TIME_HACK: elapsed={elapsed:.1f}s < {grow_time}s"
        if reward > AntiCheat.MAX_GOLD_PER_HARVEST:
            return False, f"REWARD_INJECT: reward={reward}"
        if not check_rate_limit(user_id, 'harvest', MAX_HARVEST_PER_MIN, 60):
            return False, "HARVEST_SPAM"
        with get_conn() as conn:
            dup = conn.execute(
                "SELECT 1 FROM harvest_log WHERE server_nonce=? AND user_id=?", (nonce, user_id)
            ).fetchone()
        if dup:
            return False, f"REPLAY: nonce={nonce}"
        return True, "OK"

    @staticmethod
    def validate_sell(user_id: int, price_fvt: int) -> tuple:
        if price_fvt <= 0:
            return False, "PRICE<=0"
        if price_fvt > AntiCheat.MAX_SELL_PRICE:
            return False, f"PRICE_INJECT: {price_fvt}"
        if not check_rate_limit(user_id, 'market_sell', MAX_SELL_PER_HOUR, 3600):
            return False, "SELL_SPAM"
        return True, "OK"


# ─── XP / LEVEL ──────────────────────────────────────────────────────────  FIX #13

def add_xp(conn, user_id: int, xp_amount: int) -> dict:
    """Tambah XP dan naik level jika perlu. Return info level-up."""
    row = conn.execute(
        "SELECT xp, xp_max, level FROM players WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row:
        return {}
    xp, xp_max, level = row['xp'] + xp_amount, row['xp_max'], row['level']
    leveled_up = False
    new_level = level
    while xp >= xp_max:
        xp -= xp_max
        new_level += 1
        xp_max = xp_for_level(new_level + 1)
        leveled_up = True
    conn.execute(
        "UPDATE players SET xp=?, xp_max=?, level=? WHERE user_id=?",
        (xp, xp_max, new_level, user_id)
    )
    return {'leveled_up': leveled_up, 'new_level': new_level, 'xp': xp, 'xp_max': xp_max}


# ─── STATE BUILDER (kirim ke frontend) ───────────────────────────────────

def build_state_payload(user_id: int) -> dict:
    """Bangun payload state lengkap untuk dikirim ke frontend."""
    player = get_player(user_id)
    if not player:
        return {}
    with get_conn() as conn:
        plots = conn.execute(
            "SELECT * FROM plots WHERE user_id=? ORDER BY slot", (user_id,)
        ).fetchall()
        inv = conn.execute(
            "SELECT item_id, emoji, quantity, item_type FROM inventory WHERE user_id=? AND quantity>0",
            (user_id,)
        ).fetchall()
        nfts = conn.execute(
            "SELECT nft_id FROM nfts WHERE owner_id=?", (user_id,)
        ).fetchall()

    plots_data = []
    for p in plots:
        plots_data.append({
            'slot': p['slot'],
            'locked': bool(p['locked']),
            'cropId': p['crop_id'],
            'plantedAt': int(p['planted_at'] * 1000) if p['planted_at'] else 0,  # ms
            'growTime': p['grow_time'],
            'reward': p['reward'],
            'xpReward': p['xp_reward'],
            'nonce': p['server_nonce'],
        })

    return {
        'type': 'STATE_SYNC',
        'gold': player['gold'],
        'gems': player['gems'],
        'fvtBalance': player['fvt_balance'],
        'level': player['level'],
        'xp': player['xp'],
        'xpMax': player['xp_max'],
        'totalHarvest': player['total_harvest'],
        'streak': player['streak'],
        'boostMultiplier': player['boost_multiplier'],
        'plots': plots_data,
        'inventory': [dict(i) for i in inv],
        'ownedNFTs': [n['nft_id'] for n in nfts],
    }


# ─── WEBAPP DATA HANDLER ─────────────────────────────────────────────────

async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    raw = update.effective_message.web_app_data.data if update.effective_message.web_app_data else None
    if not raw:
        return

    try:
        payload = json.loads(raw)
    except Exception:
        AntiCheat.flag(user.id, "INVALID_PAYLOAD", "Non-JSON WebApp data")
        return

    action = payload.get('action')
    data   = payload.get('data', {})

    player = get_player(user.id)
    if not player:
        upsert_player(user.id, user.username or '', user.first_name)
        player = get_player(user.id)
    if player['is_banned']:
        return

    # ── GET_STATE (frontend minta sync awal) ──────────────────────────────
    if action == 'get_state':
        state = build_state_payload(user.id)
        await update.effective_message.reply_text(
            json.dumps(state),
            # Frontend akan menerima ini via onMessage di Telegram Web App
        )
        # Cara benar: kirim lewat bot ke chat game
        try:
            await ctx.bot.send_message(user.id, json.dumps(state))
        except Exception:
            pass

    # ── PLANT ─────────────────────────────────────────────────────────────
    elif action == 'plant':
        crop_id  = data.get('cropId')
        plot_idx = data.get('plotIdx')

        if crop_id not in CROPS:
            return
        if not check_rate_limit(user.id, 'plant', 30, 60):
            AntiCheat.flag(user.id, 'PLANT_SPAM', 'Too many plants/min')
            return

        c = CROPS[crop_id]
        now_ts = time.time()
        nonce  = f"{user.id}_{plot_idx}_{int(now_ts)}"

        with get_conn() as conn:
            with get_exclusive(conn):  # FIX #12
                p = get_player(user.id)
                # Cek saldo
                if c['cost_type'] == 'gold':
                    if p['gold'] < c['cost']:
                        return
                    conn.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (c['cost'], user.id))
                else:
                    if p['gems'] < c['cost']:
                        return
                    conn.execute("UPDATE players SET gems=gems-? WHERE user_id=?", (c['cost'], user.id))

                # Cek plot kosong & tidak locked
                plot = conn.execute(
                    "SELECT * FROM plots WHERE user_id=? AND slot=?", (user.id, plot_idx)
                ).fetchone()
                if not plot or plot['locked'] or plot['crop_id']:
                    return  # plot sedang ditanami / locked

                conn.execute("""
                    UPDATE plots SET crop_id=?, planted_at=?, grow_time=?, reward=?,
                                     xp_reward=?, server_nonce=?
                    WHERE user_id=? AND slot=?
                """, (crop_id, now_ts, c['grow'], c['reward'], c['xp'], nonce,
                      user.id, plot_idx))

        # Kirim state terbaru FIX #6
        state = build_state_payload(user.id)
        try:
            await ctx.bot.send_message(user.id, json.dumps({'type': 'PLOT_UPDATE', 'plots': state['plots'],
                                                            'gold': state['gold'], 'gems': state['gems']}))
        except Exception:
            pass

    # ── HARVEST ───────────────────────────────────────────────────────────
    elif action == 'harvest':
        plot_idx  = data.get('plotIdx')
        nonce     = data.get('nonce', '')

        with get_conn() as conn:
            with get_exclusive(conn):  # FIX #12
                plot = conn.execute(
                    "SELECT * FROM plots WHERE user_id=? AND slot=?", (user.id, plot_idx)
                ).fetchone()
                if not plot or not plot['crop_id']:
                    return

                # FIX #7: validasi menggunakan data DB, bukan data dari client
                valid, reason = AntiCheat.validate_harvest(
                    user.id, plot_idx,
                    plot['server_nonce'],   # nonce dari DB
                    plot['planted_at'],
                    plot['grow_time'],
                    plot['reward']
                )
                if not valid:
                    AntiCheat.flag(user.id, 'HARVEST_CHEAT', reason, json.dumps(data))
                    return

                crop_id = plot['crop_id']
                reward  = plot['reward']
                xp_rw   = plot['xp_reward']
                crop    = CROPS.get(crop_id, {})
                emoji   = crop.get('emoji', '🌾')

                # FIX #2: hasil panen masuk inventory
                inv_add(conn, user.id, crop_id, emoji, 1, 'crop')

                # Update gold + harvest count
                conn.execute(
                    "UPDATE players SET gold=gold+?, total_harvest=total_harvest+1 WHERE user_id=?",
                    (reward, user.id)
                )

                # FIX #13: XP server-side
                lvl_info = add_xp(conn, user.id, xp_rw)

                # FIX #8: clear plot setelah panen
                conn.execute("""
                    UPDATE plots SET crop_id='', planted_at=0, grow_time=0,
                                     reward=0, xp_reward=0, server_nonce=''
                    WHERE user_id=? AND slot=?
                """, (user.id, plot_idx))

                # Log harvest
                conn.execute("""
                    INSERT INTO harvest_log
                    (user_id, plot_slot, crop_id, planted_at, harvested_at, grow_time, reward, server_nonce)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (user.id, plot_idx, crop_id, plot['planted_at'], time.time(),
                      plot['grow_time'], reward, plot['server_nonce']))

        # Kirim state FIX #6
        state = build_state_payload(user.id)
        resp = {
            'type': 'HARVEST_RESULT',
            'plotIdx': plot_idx,
            'reward': reward,
            'xpEarned': xp_rw,
            'leveledUp': lvl_info.get('leveled_up', False),
            'newLevel': lvl_info.get('new_level', player['level']),
            'plots': state['plots'],
            'gold': state['gold'],
            'inventory': state['inventory'],
        }
        try:
            await ctx.bot.send_message(user.id, json.dumps(resp))
        except Exception:
            pass

    # ── HARVEST_ALL ───────────────────────────────────────────────────────
    elif action == 'harvest_all':
        total_gold = 0
        total_xp   = 0
        harvested  = []

        with get_conn() as conn:
            with get_exclusive(conn):
                plots = conn.execute(
                    "SELECT * FROM plots WHERE user_id=? AND crop_id!='' AND locked=0", (user.id,)
                ).fetchall()
                now = time.time()
                for plot in plots:
                    elapsed = now - plot['planted_at']
                    if elapsed < plot['grow_time'] - 5:
                        continue
                    valid, reason = AntiCheat.validate_harvest(
                        user.id, plot['slot'], plot['server_nonce'],
                        plot['planted_at'], plot['grow_time'], plot['reward']
                    )
                    if not valid:
                        continue
                    crop = CROPS.get(plot['crop_id'], {})
                    inv_add(conn, user.id, plot['crop_id'], crop.get('emoji','🌾'), 1, 'crop')
                    total_gold += plot['reward']
                    total_xp   += plot['xp_reward']
                    harvested.append(plot['slot'])
                    conn.execute("""
                        UPDATE plots SET crop_id='', planted_at=0, grow_time=0,
                                         reward=0, xp_reward=0, server_nonce=''
                        WHERE user_id=? AND slot=?
                    """, (user.id, plot['slot']))
                    conn.execute("""
                        INSERT INTO harvest_log
                        (user_id, plot_slot, crop_id, planted_at, harvested_at, grow_time, reward, server_nonce)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (user.id, plot['slot'], plot['crop_id'], plot['planted_at'],
                          now, plot['grow_time'], plot['reward'], plot['server_nonce']))
                if total_gold > 0:
                    conn.execute(
                        "UPDATE players SET gold=gold+?, total_harvest=total_harvest+? WHERE user_id=?",
                        (total_gold, len(harvested), user.id)
                    )
                    lvl_info = add_xp(conn, user.id, total_xp)

        if harvested:
            state = build_state_payload(user.id)
            try:
                await ctx.bot.send_message(user.id, json.dumps({
                    'type': 'HARVEST_ALL_RESULT',
                    'harvestedSlots': harvested,
                    'totalGold': total_gold,
                    'totalXP': total_xp,
                    'plots': state['plots'],
                    'gold': state['gold'],
                    'inventory': state['inventory'],
                }))
            except Exception:
                pass

    # ── MARKET_LIST (dari frontend) ────────────────────────────────────────
    elif action == 'market_list':
        item_id  = data.get('itemId', '')
        quantity = int(data.get('quantity', 1))
        price    = int(data.get('price', 0))

        valid, reason = AntiCheat.validate_sell(user.id, price)
        if not valid:
            AntiCheat.flag(user.id, 'SELL_CHEAT', reason, json.dumps(data))
            return

        crop = CROPS.get(item_id)
        if not crop:
            return

        with get_conn() as conn:
            with get_exclusive(conn):
                # FIX #3: cek & kurangi inventory sebelum listing
                ok = inv_take(conn, user.id, item_id, quantity)
                if not ok:
                    try:
                        await ctx.bot.send_message(user.id, json.dumps({
                            'type': 'ERROR', 'msg': f'Inventory {crop["name"]} tidak cukup!'
                        }))
                    except Exception:
                        pass
                    return
                fee = int(price * MARKET_FEE)
                conn.execute("""
                    INSERT INTO market_listings
                    (seller_id, item_type, item_id, item_emoji, item_name, quantity, price_fvt, fee_paid)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (user.id, 'crop', item_id, crop['emoji'],
                      f"{crop['name']} x{quantity}", quantity, price, fee))

    # ── UNLOCK PLOT ────────────────────────────────────────────────────────
    elif action == 'unlock_plot':
        plot_idx = data.get('plotIdx', 0)
        cost     = data.get('cost', 0)
        with get_conn() as conn:
            with get_exclusive(conn):
                p = conn.execute("SELECT gold FROM players WHERE user_id=?", (user.id,)).fetchone()
                if not p or p['gold'] < cost:
                    AntiCheat.flag(user.id, 'UNLOCK_CHEAT', f"cost={cost},gold={p['gold'] if p else 0}")
                    return
                conn.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (cost, user.id))
                conn.execute(
                    "UPDATE plots SET locked=0 WHERE user_id=? AND slot=?", (user.id, plot_idx)
                )
        state = build_state_payload(user.id)
        try:
            await ctx.bot.send_message(user.id, json.dumps({
                'type': 'PLOT_UNLOCKED', 'slot': plot_idx, 'gold': state['gold'], 'plots': state['plots']
            }))
        except Exception:
            pass

    # ── WALLET CONNECT ────────────────────────────────────────────────────
    elif action == 'wallet_connect':
        addr = data.get('addr', '')
        if addr and re.match(r'^0x[0-9a-fA-F]{40}$', addr):
            with get_conn() as conn:
                conn.execute("UPDATE players SET wallet_addr=? WHERE user_id=?", (addr, user.id))
        else:
            AntiCheat.flag(user.id, 'INVALID_WALLET', f"addr={addr}")

    # ── ANTI-CHEAT FLAG (dari client) ─────────────────────────────────────
    elif action == 'anti_cheat_flag':
        code   = data.get('code', 'UNKNOWN')
        reason = data.get('reason', '')
        AntiCheat.flag(user.id, code, f"[CLIENT] {reason}")


# ─── GAME COMMANDS ────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_player(user.id, user.username or '', user.first_name)

    if ctx.args:
        ref_id = ctx.args[0]
        if ref_id.isdigit():
            referrer_id = int(ref_id)
            if referrer_id != user.id:
                with get_conn() as conn:
                    dup = conn.execute(
                        "SELECT 1 FROM referrals WHERE referrer_id=? AND referred_id=?",
                        (referrer_id, user.id)
                    ).fetchone()
                    if not dup:
                        conn.execute(
                            "INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?,?)",
                            (referrer_id, user.id)
                        )
                        conn.execute("UPDATE players SET gold=gold+200, gems=gems+10 WHERE user_id=?", (referrer_id,))
                        conn.execute("UPDATE players SET gold=gold+100 WHERE user_id=?", (user.id,))
                try:
                    await ctx.bot.send_message(
                        referrer_id,
                        "🎉 Teman kamu bergabung via referral!\n+200 koin & +10 gems!"
                    )
                except Exception:
                    pass

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Buka FarmVerse!", web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton("📊 Profil", callback_data="profile"),
         InlineKeyboardButton("🎒 Inventory", callback_data="inventory")],
        [InlineKeyboardButton("🏆 Ranking", callback_data="leaderboard"),
         InlineKeyboardButton("🔄 Marketplace", callback_data="market")],
        [InlineKeyboardButton("🔗 Referral (+200🪙)", callback_data="referral"),
         InlineKeyboardButton("❓ Bantuan", callback_data="help")],
    ])
    await update.message.reply_text(
        f"🌾 *Selamat datang di FarmVerse v3, {user.first_name}!*\n\n"
        "🚀 *Fitur v3:*\n"
        "• 🎒 Inventory system — hasil panen tersimpan\n"
        "• 🔄 Marketplace real — item beneran berpindah\n"
        "• ⭐ XP & Level system\n"
        "• 🛡️ Server-authoritative farming\n"
        "• ⚡ SQLite WAL + transaction lock\n\n"
        "Tap tombol di bawah untuk bermain! 👇",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = query.from_user if query else update.effective_user
    if query:
        await query.answer()
    if not get_player(user.id):
        upsert_player(user.id, user.username or '', user.first_name)
    player = get_player(user.id)

    with get_conn() as conn:
        nft_count = conn.execute("SELECT COUNT(*) FROM nfts WHERE owner_id=?", (user.id,)).fetchone()[0]
        ref_count = conn.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user.id,)).fetchone()[0]
        inv_count = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=?", (user.id,)
        ).fetchone()[0]

    xp_pct = int(player['xp'] / max(player['xp_max'], 1) * 100)
    text = (
        f"👨‍🌾 *{player['first_name']}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⭐ Level: *{player['level']}*  •  XP: *{player['xp']:,}/{player['xp_max']:,}* ({xp_pct}%)\n"
        f"🪙 Koin: *{player['gold']:,}*\n"
        f"💎 Gems: *{player['gems']:,}*\n"
        f"🔵 FVT: *{player['fvt_balance']:,}*\n"
        f"🌾 Total Panen: *{player['total_harvest']:,}*\n"
        f"🎒 Inventory: *{inv_count} item*\n"
        f"🔥 Streak: *{player['streak']} hari*\n"
        f"💎 NFT: *{nft_count}*\n"
        f"👥 Referral: *{ref_count}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 Bergabung: {player['created_at'][:10]}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌾 Main", web_app=WebAppInfo(url=GAME_URL))]])
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


# FIX #10: /inventory command
async def cmd_inventory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = query.from_user if query else update.effective_user
    if query:
        await query.answer()
    if not get_player(user.id):
        upsert_player(user.id, user.username or '', user.first_name)

    items = get_inventory(user.id)
    if not items:
        text = "🎒 *Inventory Kamu*\n━━━━━━━━━━━━━━━━\n📦 Inventory kosong.\n\nPanen tanaman untuk mengisi inventory!"
    else:
        lines = ["🎒 *Inventory Kamu*\n━━━━━━━━━━━━━━━━"]
        crop_items = [i for i in items if i['item_type'] == 'crop']
        other_items = [i for i in items if i['item_type'] != 'crop']
        if crop_items:
            lines.append("\n🌾 *Hasil Panen:*")
            for item in crop_items:
                crop_info = CROPS.get(item['item_id'], {})
                name = crop_info.get('name', item['item_id'].capitalize())
                lines.append(f"  {item['emoji']} {name} — *x{item['quantity']}*")
        if other_items:
            lines.append("\n📦 *Item Lain:*")
            for item in other_items:
                lines.append(f"  {item['emoji']} {item['item_id']} — *x{item['quantity']}*")
        lines.append(f"\n━━━━━━━━━━━━━━━━\nTotal: *{sum(i['quantity'] for i in items)} item*")
        lines.append("Gunakan `/sell <item> <qty> <harga>` untuk menjual.")
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌾 Main", web_app=WebAppInfo(url=GAME_URL))]])
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not get_player(user.id):
        upsert_player(user.id, user.username or '', user.first_name)
    player = get_player(user.id)
    await update.message.reply_text(
        f"💰 *Saldo {player['first_name']}*\n"
        f"🪙 Koin: *{player['gold']:,}*\n"
        f"💎 Gems: *{player['gems']:,}*\n"
        f"🔵 FVT: *{player['fvt_balance']:,}*\n"
        f"⭐ Level: *{player['level']}*\n"
        f"💼 Wallet: `{player['wallet_addr'] or 'Belum terhubung'}`",
        parse_mode="Markdown"
    )


async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    mode = 'harvest'
    if ctx.args:
        mode = ctx.args[0] if ctx.args[0] in ('harvest', 'level', 'fvt') else 'harvest'
    lb = get_leaderboard(mode)
    medals = ['🥇', '🥈', '🥉']
    col_label = {'harvest': '🌾 Total Panen', 'level': '⭐ Level', 'fvt': '🔵 FVT'}[mode]
    lines = [f"🏆 *Leaderboard — {col_label}*\n━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(lb):
        m   = medals[i] if i < 3 else f"{i+1}."
        val = p['total_harvest'] if mode == 'harvest' else (p['level'] if mode == 'level' else p['fvt_balance'])
        lines.append(f"{m} *{p['first_name']}* — {val:,} (Lv.{p['level']})")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Panen", callback_data="lb_harvest"),
         InlineKeyboardButton("⭐ Level", callback_data="lb_level"),
         InlineKeyboardButton("🔵 FVT", callback_data="lb_fvt")],
        [InlineKeyboardButton("🌾 Main", web_app=WebAppInfo(url=GAME_URL))]
    ])
    text = "\n".join(lines)
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    user = query.from_user if query else update.effective_user

    with get_conn() as conn:
        active  = conn.execute("""
            SELECT ml.*, p.first_name as seller_name
            FROM market_listings ml JOIN players p ON p.user_id = ml.seller_id
            WHERE ml.status='active' ORDER BY ml.created_at DESC LIMIT 10
        """).fetchall()
        volume  = conn.execute("SELECT COALESCE(SUM(price_fvt),0) FROM market_listings WHERE status='sold'").fetchone()[0]
        trades  = conn.execute("SELECT COUNT(*) FROM market_listings WHERE status='sold' AND date(sold_at)=date('now')").fetchone()[0]

    if not active:
        text = "🔄 *Marketplace FarmVerse*\n━━━━━━━━━━━━━━━━\n📦 Belum ada listing.\n\nGunakan /sell untuk menjual!"
    else:
        lines = [f"🔄 *Marketplace* · 📊 Vol: {volume:,} FVT · 📈 {trades} trades hari ini\n━━━━━━━━━━━━━━━━"]
        for l in active:
            lines.append(
                f"{l['item_emoji']} *{l['item_name']}*\n"
                f"   👤 {l['seller_name']} · 💰 {l['price_fvt']:,} FVT · ID: `#{l['id']}`"
            )
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Buka Market", web_app=WebAppInfo(url=GAME_URL + '#market'))],
        [InlineKeyboardButton("➕ Jual Item", callback_data="help_sell"),
         InlineKeyboardButton("📈 Riwayat", callback_data="market_history")],
    ])
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


# FIX #14: /markethistory command
async def cmd_market_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT mh.*, p_s.first_name as seller_name, p_b.first_name as buyer_name
            FROM market_history mh
            LEFT JOIN players p_s ON p_s.user_id = mh.seller_id
            LEFT JOIN players p_b ON p_b.user_id = mh.buyer_id
            ORDER BY mh.sold_at DESC LIMIT 15
        """).fetchall()
    if not rows:
        text = "📈 *Riwayat Market*\n━━━━━━━━━━━━━━━━\nBelum ada transaksi."
    else:
        lines = ["📈 *Riwayat Market Terbaru*\n━━━━━━━━━━━━━━━━"]
        for r in rows:
            lines.append(
                f"{r['item_emoji']} *{r['item_name']}*\n"
                f"   {r['seller_name']} → {r['buyer_name'] or '?'} · {r['price_fvt']:,} FVT\n"
                f"   🕐 {r['sold_at'][:16]}"
            )
        text = "\n".join(lines)
    if query:
        await query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


# FIX #3: /sell sekarang cek dan kurangi inventory
async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /sell <item_id> <quantity> <price_fvt>"""
    user   = update.effective_user
    player = get_player(user.id)
    if not player:
        upsert_player(user.id, user.username or '', user.first_name)
        player = get_player(user.id)
    if player['is_banned']:
        await update.message.reply_text("🚫 Akun kamu ditangguhkan."); return

    if not ctx.args or len(ctx.args) < 3:
        # Tampilkan inventory dulu
        items = get_inventory(user.id)
        inv_text = ""
        if items:
            inv_text = "\n\n🎒 *Inventory kamu:*\n"
            for it in items[:10]:
                inv_text += f"  • `{it['item_id']}` x{it['quantity']}\n"
        await update.message.reply_text(
            "📦 *Cara Jual Item:*\n"
            "`/sell <item_id> <jumlah> <harga_fvt>`\n\n"
            "Contoh:\n`/sell wheat 5 500`\n`/sell strawberry 2 1200`" + inv_text,
            parse_mode="Markdown"
        )
        return

    item_id = ctx.args[0].lower()
    try:
        quantity = int(ctx.args[1])
        price    = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("❌ Jumlah dan harga harus angka!"); return

    if quantity <= 0 or price <= 0:
        await update.message.reply_text("❌ Jumlah dan harga harus > 0!"); return

    crop = CROPS.get(item_id)
    if not crop:
        await update.message.reply_text(f"❌ Item `{item_id}` tidak dikenal!", parse_mode="Markdown"); return

    valid, reason = AntiCheat.validate_sell(user.id, price)
    if not valid:
        AntiCheat.flag(user.id, "SELL_CHEAT", reason)
        await update.message.reply_text("🚫 Validasi gagal."); return

    with get_conn() as conn:
        with get_exclusive(conn):  # FIX #12
            # FIX #3: cek & kurangi inventory sebelum listing
            ok = inv_take(conn, user.id, item_id, quantity)
            if not ok:
                await update.message.reply_text(
                    f"❌ Inventory *{crop['name']}* tidak cukup!\n"
                    f"Cek `/inventory` untuk melihat stok.",
                    parse_mode="Markdown"
                )
                return
            fee = int(price * MARKET_FEE)
            conn.execute("""
                INSERT INTO market_listings
                (seller_id, item_type, item_id, item_emoji, item_name, quantity, price_fvt, fee_paid)
                VALUES (?,?,?,?,?,?,?,?)
            """, (user.id, 'crop', item_id, crop['emoji'],
                  f"{crop['name']} x{quantity}", quantity, price, fee))
            listing_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    net = price - fee
    await update.message.reply_text(
        f"✅ *Listing Berhasil!*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{crop['emoji']} *{crop['name']}* x{quantity}\n"
        f"💰 Harga: *{price:,} FVT*\n"
        f"📝 Fee (5%): *{fee:,} FVT*\n"
        f"💵 Kamu terima nanti: *{net:,} FVT*\n"
        f"🆔 Listing ID: `#{listing_id}`\n\n"
        f"Gunakan /cancellist {listing_id} untuk membatalkan.",
        parse_mode="Markdown"
    )


# FIX #4: /buy memindahkan item ke inventory buyer
async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /buy <listing_id>"""
    user   = update.effective_user
    player = get_player(user.id)
    if not player:
        upsert_player(user.id, user.username or '', user.first_name)
        player = get_player(user.id)
    if player['is_banned']:
        await update.message.reply_text("🚫 Akun ditangguhkan."); return
    if not ctx.args:
        await update.message.reply_text("Usage: /buy <listing_id>"); return

    try:
        listing_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID harus angka!"); return

    with get_conn() as conn:
        with get_exclusive(conn):  # FIX #12
            listing = conn.execute(
                "SELECT * FROM market_listings WHERE id=? AND status='active'", (listing_id,)
            ).fetchone()
            if not listing:
                await update.message.reply_text("❌ Listing tidak ditemukan!"); return
            listing = dict(listing)

            if listing['seller_id'] == user.id:
                await update.message.reply_text("❌ Tidak bisa beli listing sendiri!"); return

            # Reload player dalam transaksi
            p = conn.execute("SELECT fvt_balance FROM players WHERE user_id=?", (user.id,)).fetchone()
            if not p or p['fvt_balance'] < listing['price_fvt']:
                await update.message.reply_text(
                    f"❌ FVT tidak cukup!\nKamu punya: *{p['fvt_balance'] if p else 0:,} FVT*\n"
                    f"Harga: *{listing['price_fvt']:,} FVT*",
                    parse_mode="Markdown"
                )
                return

            if not check_rate_limit(user.id, 'market_buy', 20, 3600):
                await update.message.reply_text("⏳ Terlalu banyak pembelian. Coba lagi."); return

            fee = listing['fee_paid']
            net = listing['price_fvt'] - fee

            # Kurangi FVT buyer, tambah FVT seller (net)
            conn.execute("UPDATE players SET fvt_balance=fvt_balance-? WHERE user_id=?",
                         (listing['price_fvt'], user.id))
            conn.execute("UPDATE players SET fvt_balance=fvt_balance+? WHERE user_id=?",
                         (net, listing['seller_id']))

            # Update listing status
            conn.execute("""
                UPDATE market_listings SET status='sold', buyer_id=?, sold_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (user.id, listing_id))

            # FIX #4: pindahkan item ke inventory buyer
            inv_add(conn, user.id, listing['item_id'], listing['item_emoji'],
                    listing['quantity'], listing['item_type'])

            # Catat ke market_history FIX #14
            conn.execute("""
                INSERT INTO market_history
                (listing_id, seller_id, buyer_id, item_id, item_name, item_emoji,
                 quantity, price_fvt, fee_paid)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (listing_id, listing['seller_id'], user.id, listing['item_id'],
                  listing['item_name'], listing['item_emoji'], listing['quantity'],
                  listing['price_fvt'], fee))

            # Catat transaksi
            conn.execute(
                "INSERT INTO transactions (user_id, tx_type, amount, currency, item_id, status) VALUES (?,?,?,?,?,?)",
                (user.id, 'market_buy', listing['price_fvt'], 'FVT', listing['item_id'], 'completed')
            )
            conn.execute(
                "INSERT INTO transactions (user_id, tx_type, amount, currency, item_id, status) VALUES (?,?,?,?,?,?)",
                (listing['seller_id'], 'market_sell', net, 'FVT', listing['item_id'], 'completed')
            )

    await update.message.reply_text(
        f"✅ *Pembelian Berhasil!*\n"
        f"{listing['item_emoji']} *{listing['item_name']}*\n"
        f"💰 Dibayar: *{listing['price_fvt']:,} FVT*\n"
        f"Item sudah masuk inventory kamu! `/inventory`",
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


# FIX #5: /cancellist mengembalikan item ke inventory seller
async def cmd_cancellist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: /cancellist <listing_id>"); return
    try:
        listing_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID harus angka!"); return

    with get_conn() as conn:
        with get_exclusive(conn):  # FIX #12
            row = conn.execute(
                "SELECT * FROM market_listings WHERE id=? AND seller_id=? AND status='active'",
                (listing_id, user.id)
            ).fetchone()
            if not row:
                await update.message.reply_text("❌ Listing tidak ditemukan atau bukan milikmu!"); return
            row = dict(row)
            conn.execute("UPDATE market_listings SET status='cancelled' WHERE id=?", (listing_id,))
            # FIX #5: kembalikan item ke inventory seller
            inv_add(conn, user.id, row['item_id'], row['item_emoji'],
                    row['quantity'], row['item_type'])

    await update.message.reply_text(
        f"✅ Listing #{listing_id} dibatalkan.\n"
        f"{row['item_emoji']} *{row['item_name']}* dikembalikan ke inventory kamu.",
        parse_mode="Markdown"
    )


async def cmd_nft(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with get_conn() as conn:
        nfts = conn.execute("SELECT * FROM nfts WHERE owner_id=?", (user.id,)).fetchall()
    NFT_INFO = {
        'royal_farmer':      ('👑', 'Royal Farmer',   '+100% XP'),
        'auto_harvester':    ('🤖', 'Auto Harvester', 'Auto-panen 30m'),
        'alchemist_lab':     ('🧪', 'Alchemist Lab',  '+3x potion'),
        'grand_castle':      ('🏰', 'Grand Castle',   '+5 slot lahan'),
        'unicorn_mount':     ('🦄', 'Unicorn Mount',  '3x speed'),
        'rare_soil':         ('⚗️', 'Rare Soil',      '2x grow speed'),
        'golden_tractor':    ('🚜', 'Golden Tractor', '4x plot sekaligus'),
        'crystal_greenhouse':('🌿', 'Crystal GH',     'Premium crops gratis'),
    }
    if not nfts:
        text = "💎 *NFT Koleksi*\n━━━━━━━━━━━━━━━━\nBelum ada NFT.\nGunakan `/buynft <id>` atau buka game."
    else:
        lines = ["💎 *NFT Koleksi Kamu*\n━━━━━━━━━━━━━━━━"]
        for nft in nfts:
            info   = NFT_INFO.get(nft['nft_id'], ('📦', nft['nft_id'], ''))
            status = "🏷️ Di-listing" if nft['is_listed'] else "✅ Aktif"
            lines.append(f"{info[0]} *{info[1]}* — {info[2]}\n   {status} · Token #{nft['token_id']}")
        text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 NFT Store", web_app=WebAppInfo(url=GAME_URL + '#nft'))]
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_sendtoken(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    player = get_player(user.id)
    if not player or player['is_banned']:
        await update.message.reply_text("❌ Akun tidak valid."); return
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "📤 *Kirim FVT:*\n`/sendtoken @username <jumlah>`", parse_mode="Markdown"
        ); return
    try:
        amount = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Jumlah harus angka!"); return
    if amount <= 0 or amount > player['fvt_balance']:
        await update.message.reply_text(f"❌ Jumlah tidak valid. Punya: {player['fvt_balance']:,} FVT"); return
    if not check_rate_limit(user.id, 'sendtoken', 10, 3600):
        await update.message.reply_text("⏳ Max 10 transfer/jam."); return

    target_input = ctx.args[0].lstrip('@')
    with get_conn() as conn:
        target = conn.execute(
            "SELECT * FROM players WHERE user_id=?" if target_input.isdigit() else
            "SELECT * FROM players WHERE username=?",
            (int(target_input) if target_input.isdigit() else target_input,)
        ).fetchone()
    if not target:
        await update.message.reply_text("❌ User tidak ditemukan!"); return
    target = dict(target)
    if target['user_id'] == user.id:
        await update.message.reply_text("❌ Tidak bisa kirim ke diri sendiri!"); return

    with get_conn() as conn:
        conn.execute("UPDATE players SET fvt_balance=fvt_balance-? WHERE user_id=?", (amount, user.id))
        conn.execute("UPDATE players SET fvt_balance=fvt_balance+? WHERE user_id=?", (amount, target['user_id']))
        conn.execute(
            "INSERT INTO transactions (user_id, tx_type, amount, currency, item_id, status) VALUES (?,?,?,?,?,?)",
            (user.id, 'fvt_send', amount, 'FVT', str(target['user_id']), 'completed')
        )

    await update.message.reply_text(f"✅ *{amount:,} FVT* dikirim ke *{target['first_name']}*!", parse_mode="Markdown")
    try:
        await ctx.bot.send_message(
            target['user_id'],
            f"📥 Kamu menerima *{amount:,} FVT* dari *{player['first_name']}*!", parse_mode="Markdown"
        )
    except Exception:
        pass


async def cmd_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    user = query.from_user if query else update.effective_user
    bot  = await ctx.bot.get_me()
    ref_link = f"https://t.me/{bot.username}?start={user.id}"
    with get_conn() as conn:
        ref_count   = conn.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user.id,)).fetchone()[0]
    text = (
        f"🔗 *Link Referral:*\n`{ref_link}`\n\n"
        f"👥 Total referral: *{ref_count}*\n"
        f"🪙 Total bonus: *{ref_count * 200:,} koin*\n\n"
        "🎁 Kamu dapat +200 koin & +10 gems per teman.\n"
        "Temanmu dapat +100 koin starter."
    )
    if query:
        await query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    text = (
        "❓ *Panduan FarmVerse v3*\n━━━━━━━━━━━━━━━━\n\n"
        "🌾 *Farming:*\nTap lahan → pilih tanaman → tunggu → panen!\n\n"
        "🎒 *Inventory:*\n`/inventory` — lihat semua item\n\n"
        "🔄 *Marketplace:*\n"
        "`/market` — listing aktif\n"
        "`/sell <item_id> <qty> <harga>` — jual dari inventory\n"
        "`/buy <id>` — beli item\n"
        "`/cancellist <id>` — batalkan (item kembali)\n"
        "`/markethistory` — riwayat transaksi\n\n"
        "💰 *Token:*\n`/balance` — cek saldo\n`/sendtoken @user <amount>` — kirim FVT\n\n"
        "💎 *NFT:*\n`/nft` — koleksi NFT\n\n"
        "👥 *Sosial:*\n`/referral` — link referral\n`/leaderboard` — top 10\n\n"
        "🛡️ Semua aksi diverifikasi server."
    )
    if query:
        await query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


# ─── ADMIN COMMANDS ───────────────────────────────────────────────────────

def require_admin(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Hanya admin.")
            return
        return await func(update, ctx)
    return wrapper


@require_admin
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with get_conn() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        banned  = conn.execute("SELECT COUNT(*) FROM players WHERE is_banned=1").fetchone()[0]
        harvest = conn.execute("SELECT COALESCE(SUM(total_harvest),0) FROM players").fetchone()[0]
        vol     = conn.execute("SELECT COALESCE(SUM(price_fvt),0) FROM market_listings WHERE status='sold'").fetchone()[0]
        flags   = conn.execute("SELECT COUNT(*) FROM anti_cheat_log WHERE date(created_at)=date('now')").fetchone()[0]
        active  = conn.execute("SELECT COUNT(*) FROM players WHERE date(last_seen)=date('now')").fetchone()[0]
        inv_tot = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory").fetchone()[0]
    await update.message.reply_text(
        f"📊 *Admin Dashboard v3*\n━━━━━━━━━━━━━━━━\n"
        f"👥 Pemain: *{total:,}* · Aktif: *{active:,}* · Ban: *{banned}*\n"
        f"🌾 Total Panen: *{harvest:,}*\n"
        f"🎒 Total Inventory: *{inv_tot:,}*\n"
        f"💰 Market Vol: *{vol:,} FVT*\n"
        f"🛡️ Flags Hari Ini: *{flags}*",
        parse_mode="Markdown"
    )


@require_admin
async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /ban <user_id> [reason]"); return
    try:
        target_id = int(ctx.args[0])
        reason    = ' '.join(ctx.args[1:]) if len(ctx.args) > 1 else 'Admin ban'
    except ValueError:
        await update.message.reply_text("❌ user_id harus angka!"); return
    with get_conn() as conn:
        conn.execute("UPDATE players SET is_banned=1, ban_reason=? WHERE user_id=?", (reason, target_id))
    await update.message.reply_text(f"🚫 User {target_id} dibanned. Alasan: {reason}")
    try:
        await ctx.bot.send_message(target_id, f"🚫 Akun ditangguhkan. Alasan: {reason}")
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
    with get_conn() as conn:
        conn.execute("UPDATE players SET is_banned=0, ban_reason='', suspicion_score=0 WHERE user_id=?", (target_id,))
    await update.message.reply_text(f"✅ User {target_id} di-unban.")
    try:
        await ctx.bot.send_message(target_id, "✅ Akun dipulihkan. Selamat bermain!")
    except Exception:
        pass


@require_admin
async def cmd_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text("Usage: /give <user_id> <gold|gems|fvt|item_id> <amount>"); return
    try:
        target_id = int(ctx.args[0])
        currency  = ctx.args[1].lower()
        amount    = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("❌ Format salah!"); return
    col = {'gold': 'gold', 'gems': 'gems', 'fvt': 'fvt_balance'}.get(currency)
    if col:
        with get_conn() as conn:
            conn.execute(f"UPDATE players SET {col}={col}+? WHERE user_id=?", (amount, target_id))
        await update.message.reply_text(f"✅ +{amount:,} {currency} ke {target_id}.")
    elif currency in CROPS:
        with get_conn() as conn:
            inv_add(conn, target_id, currency, CROPS[currency]['emoji'], amount, 'crop')
        await update.message.reply_text(f"✅ +{amount}x {CROPS[currency]['name']} ke {target_id}.")
    else:
        await update.message.reply_text("❌ Currency harus gold/gems/fvt atau item_id crop.")


@require_admin
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast <pesan>"); return
    msg   = ' '.join(ctx.args)
    ids   = get_all_player_ids()
    sent  = failed = 0
    for uid in ids:
        try:
            await ctx.bot.send_message(uid, f"📢 *Pengumuman FarmVerse*\n\n{msg}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    with get_conn() as conn:
        conn.execute("INSERT INTO broadcast_log (admin_id, message, sent_to) VALUES (?,?,?)",
                     (ADMIN_ID, msg, sent))
    await update.message.reply_text(f"✅ Broadcast selesai!\n✓ Terkirim: {sent}\n✗ Gagal: {failed}")


@require_admin
async def cmd_flags(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with get_conn() as conn:
        flags = conn.execute("""
            SELECT acl.*, p.first_name, p.username
            FROM anti_cheat_log acl JOIN players p ON p.user_id = acl.user_id
            ORDER BY acl.created_at DESC LIMIT 15
        """).fetchall()
    if not flags:
        await update.message.reply_text("✅ Tidak ada flags terbaru."); return
    lines = ["🛡️ *Anti-Cheat Flags Terbaru*\n━━━━━━━━━━━━━━━━"]
    for f in flags:
        lines.append(f"👤 {f['first_name']} ({f['user_id']})\n   ⚠️ {f['flag_code']}: {f['flag_reason']}\n   🕐 {f['created_at'][:16]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── CALLBACK HANDLER ─────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    handlers = {
        "profile":        cmd_profile,
        "inventory":      cmd_inventory,
        "leaderboard":    cmd_leaderboard,
        "market":         cmd_market,
        "market_history": cmd_market_history,
        "referral":       cmd_referral,
        "help":           cmd_help,
    }
    if data in handlers:
        await handlers[data](update, ctx)
    elif data.startswith("lb_"):
        ctx.args = [data[3:]]
        await cmd_leaderboard(update, ctx)
    elif data == "help_sell":
        await cmd_sell(update, ctx)


# ─── MAIN ─────────────────────────────────────────────────────────────────

def main():
    init_db()
    logger.info("✅ FarmVerse v3 DB ready (WAL mode)")
    logger.info(f"🔑 Token prefix: {BOT_TOKEN[:10]}...")

    app = Application.builder().token(BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start",          start))
    app.add_handler(CommandHandler("profile",        cmd_profile))
    app.add_handler(CommandHandler("inventory",      cmd_inventory))    # FIX #10
    app.add_handler(CommandHandler("balance",        cmd_balance))
    app.add_handler(CommandHandler("leaderboard",    cmd_leaderboard))
    app.add_handler(CommandHandler("market",         cmd_market))
    app.add_handler(CommandHandler("markethistory",  cmd_market_history))  # FIX #14
    app.add_handler(CommandHandler("sell",           cmd_sell))         # FIX #3
    app.add_handler(CommandHandler("buy",            cmd_buy))          # FIX #4
    app.add_handler(CommandHandler("cancellist",     cmd_cancellist))   # FIX #5
    app.add_handler(CommandHandler("nft",            cmd_nft))
    app.add_handler(CommandHandler("sendtoken",      cmd_sendtoken))
    app.add_handler(CommandHandler("referral",       cmd_referral))
    app.add_handler(CommandHandler("help",           cmd_help))

    # Admin commands
    app.add_handler(CommandHandler("admin",          cmd_admin))
    app.add_handler(CommandHandler("ban",            cmd_ban))
    app.add_handler(CommandHandler("unban",          cmd_unban))
    app.add_handler(CommandHandler("give",           cmd_give))
    app.add_handler(CommandHandler("broadcast",      cmd_broadcast))
    app.add_handler(CommandHandler("flags",          cmd_flags))

    # Callback & WebApp
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))

    logger.info("🌾 FarmVerse Bot v3 running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
