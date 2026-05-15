"""
FarmVerse Bot — v4.0 PHASE 1 (Backend Authoritative)
=====================================================
PHASE 1 FEATURES:
  ✅ Server-authoritative: semua reward, inventory, XP, ekonomi di server
  ✅ WAL SQLite + BEGIN IMMEDIATE transactions
  ✅ Inventory system (panen → inventory → jual/pakai)
  ✅ XP + Level system (server-side)
  ✅ Energy system (anti-spam farming)
  ✅ Weather system (server-controlled, 4 types)
  ✅ Pest system (random event, butuh pesticide)
  ✅ Fertilizer system (speed up growth, server-validated)
  ✅ Combo harvest bonus (multi-crop rarity multiplier)
  ✅ Crop rarity (common/uncommon/rare/epic/legendary)
  ✅ Farm upgrade (soil, tools, barn, water)
  ✅ Daily quest + streak reward
  ✅ Market (sell/buy nyata, item berpindah)
  ✅ Market history
  ✅ /inventory, /weather, /farm, /market, /sell, /buy, /cancellist
  ✅ Anti-cheat: nonce, replay protection, rate limit, HMAC
  ✅ State sync ke frontend via sendData JSON

Requirements:
  pip install "python-telegram-bot>=20.7" aiosqlite python-dotenv
"""

import asyncio, json, os, sys, time, hmac, hashlib, logging, re, sqlite3, random
from contextlib import contextmanager
from datetime import datetime, date
from typing import Optional

from dotenv import load_dotenv
load_dotenv(override=True)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                           MessageHandler, ContextTypes, filters)

# ─── CONFIG ─────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "").strip()
GAME_URL    = os.getenv("GAME_URL", "https://farmverse-liart.vercel.app")
ADMIN_ID    = int(os.getenv("ADMIN_ID", "0"))
HMAC_SECRET = os.getenv("HMAC_SECRET", "farmverse_secret_2025").encode()
DB_PATH     = "farmverse.db"
MARKET_FEE  = 0.05

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("farmverse")

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN tidak valid! Cek .env"); sys.exit(1)

# ─── GAME CONSTANTS ──────────────────────────────────────────────────────────

# Crop rarity → multiplier bonus
RARITY_MULT = {'common':1.0, 'uncommon':1.2, 'rare':1.5, 'epic':2.0, 'legendary':3.0}
RARITY_EMOJI = {'common':'⚪', 'uncommon':'🟢', 'rare':'🔵', 'epic':'🟣', 'legendary':'🟡'}

# Crops: single source of truth
CROPS = {
    # id: cost, cost_type, grow(s), reward(gold), xp, fvt, emoji, name, level, rarity, combo_tag
    'wheat':        dict(cost=10,  ct='gold', grow=120,  reward=50,  xp=8,   fvt=5,   emoji='🌾', name='Gandum',        lv=1, rarity='common',    combo='grain'),
    'carrot':       dict(cost=25,  ct='gold', grow=300,  reward=120, xp=15,  fvt=12,  emoji='🥕', name='Wortel',         lv=1, rarity='common',    combo='veggie'),
    'corn':         dict(cost=50,  ct='gold', grow=180,  reward=200, xp=22,  fvt=20,  emoji='🌽', name='Jagung',         lv=2, rarity='uncommon',  combo='grain'),
    'tomato':       dict(cost=40,  ct='gold', grow=240,  reward=160, xp=18,  fvt=16,  emoji='🍅', name='Tomat',          lv=2, rarity='uncommon',  combo='veggie'),
    'pumpkin':      dict(cost=70,  ct='gold', grow=360,  reward=280, xp=30,  fvt=28,  emoji='🎃', name='Labu',           lv=3, rarity='uncommon',  combo='veggie'),
    'sunflower':    dict(cost=100, ct='gold', grow=480,  reward=380, xp=42,  fvt=38,  emoji='🌻', name='Bunga Matahari', lv=4, rarity='rare',      combo='flower'),
    'rose':         dict(cost=120, ct='gold', grow=420,  reward=340, xp=38,  fvt=34,  emoji='🌹', name='Mawar',          lv=4, rarity='rare',      combo='flower'),
    'mango':        dict(cost=130, ct='gold', grow=600,  reward=520, xp=55,  fvt=52,  emoji='🥭', name='Mangga',         lv=5, rarity='rare',      combo='fruit'),
    'grape':        dict(cost=200, ct='gold', grow=900,  reward=850, xp=80,  fvt=85,  emoji='🍇', name='Anggur',         lv=6, rarity='epic',      combo='fruit'),
    'dragon_fruit': dict(cost=250, ct='gold', grow=1200, reward=1200,xp=110, fvt=120, emoji='🐉', name='Buah Naga',      lv=8, rarity='epic',      combo='fruit'),
    'strawberry':   dict(cost=15,  ct='gems', grow=180,  reward=400, xp=45,  fvt=80,  emoji='🍓', name='Stroberi',       lv=3, rarity='rare',      combo='fruit', premium=True),
    'blueberry':    dict(cost=25,  ct='gems', grow=240,  reward=600, xp=60,  fvt=120, emoji='🫐', name='Blueberry',      lv=4, rarity='epic',      combo='fruit', premium=True),
    'golden_wheat': dict(cost=40,  ct='gems', grow=300,  reward=900, xp=90,  fvt=180, emoji='✨', name='Gandum Emas',    lv=7, rarity='legendary', combo='grain', premium=True),
    'crystal_rose': dict(cost=50,  ct='gems', grow=360,  reward=1100,xp=100, fvt=220, emoji='💎', name='Mawar Kristal',  lv=9, rarity='legendary', combo='flower', premium=True),
}

# Combo tags → bonus if 3+ same tag planted simultaneously
COMBO_BONUS = {
    'grain':  dict(gold_pct=15, fvt_pct=10, label='🌾 Grain Combo!'),
    'veggie': dict(gold_pct=12, fvt_pct=8,  label='🥕 Veggie Combo!'),
    'fruit':  dict(gold_pct=20, fvt_pct=15, label='🍓 Fruit Combo!'),
    'flower': dict(gold_pct=18, fvt_pct=12, label='🌸 Flower Combo!'),
}

# Weather types (rotates every 4h, server-controlled)
WEATHER = {
    'sunny':  dict(emoji='☀️',  label='Cerah',         grow_mult=1.0, reward_mult=1.0,  pest_chance=0.05),
    'rainy':  dict(emoji='🌧️', label='Hujan',          grow_mult=0.85,reward_mult=1.15, pest_chance=0.02),
    'windy':  dict(emoji='💨',  label='Berangin',       grow_mult=1.1, reward_mult=0.9,  pest_chance=0.08),
    'stormy': dict(emoji='⛈️', label='Badai',          grow_mult=0.7, reward_mult=0.8,  pest_chance=0.15),
    'golden': dict(emoji='🌟',  label='Cuaca Emas!',    grow_mult=1.3, reward_mult=1.5,  pest_chance=0.0),  # rare event
}

# Fertilizer types (items in inventory/shop)
FERTILIZERS = {
    'basic_fert':   dict(emoji='🌱', name='Pupuk Dasar',   grow_boost=0.25, reward_boost=0,    uses=1),
    'super_fert':   dict(emoji='⚡', name='Super Pupuk',   grow_boost=0.5,  reward_boost=0.1,  uses=1),
    'golden_fert':  dict(emoji='✨', name='Pupuk Emas',    grow_boost=0,    reward_boost=0.3,  uses=1),  # instant harvest bonus
    'mega_fert':    dict(emoji='💥', name='Mega Pupuk',    grow_boost=0.75, reward_boost=0.2,  uses=1),  # instant-ish
}

# Farm upgrades (permanent, stored in player_upgrades table)
UPGRADES = {
    'soil_1':   dict(cost=500,  ct='gold', req_lv=1,  effect='grow_speed', val=0.05, label='🌱 Tanah Dasar I',      desc='+5% grow speed semua lahan'),
    'soil_2':   dict(cost=1500, ct='gold', req_lv=3,  effect='grow_speed', val=0.10, label='🌱 Tanah Subur II',     desc='+10% grow speed semua lahan'),
    'soil_3':   dict(cost=4000, ct='gold', req_lv=6,  effect='grow_speed', val=0.20, label='🌿 Tanah Premium III',  desc='+20% grow speed semua lahan'),
    'barn_1':   dict(cost=800,  ct='gold', req_lv=2,  effect='inventory',  val=20,   label='🏚️ Gudang Kecil I',     desc='+20 slot inventory'),
    'barn_2':   dict(cost=2500, ct='gold', req_lv=5,  effect='inventory',  val=50,   label='🏠 Gudang Sedang II',   desc='+50 slot inventory'),
    'water_1':  dict(cost=600,  ct='gold', req_lv=2,  effect='pest_resist',val=0.1,  label='💧 Irigasi Dasar I',   desc='+10% ketahanan hama'),
    'water_2':  dict(cost=2000, ct='gold', req_lv=4,  effect='pest_resist',val=0.25, label='🚿 Irigasi Otomatis II',desc='+25% ketahanan hama'),
    'tool_1':   dict(cost=1000, ct='gold', req_lv=3,  effect='harvest_xp', val=0.1,  label='🔧 Cangkul Besi I',    desc='+10% XP dari panen'),
    'tool_2':   dict(cost=3000, ct='gold', req_lv=7,  effect='harvest_xp', val=0.25, label='⚙️ Mesin Panen II',    desc='+25% XP dari panen'),
    'energy_1': dict(cost=700,  ct='gold', req_lv=2,  effect='energy_max', val=10,   label='⚡ Baterai I',          desc='+10 energy max'),
    'energy_2': dict(cost=2200, ct='gold', req_lv=5,  effect='energy_max', val=25,   label='🔋 Generator II',       desc='+25 energy max'),
}

# Shop items
SHOP_ITEMS = {
    'basic_fert':   dict(cat='tool',   emoji='🌱', name='Pupuk Dasar',    desc='Percepat 25% 1 lahan',  price=30,  cur='gold', badge='basic'),
    'super_fert':   dict(cat='tool',   emoji='⚡', name='Super Pupuk',    desc='Percepat 50% + bonus',  price=80,  cur='gold', badge='hot'),
    'pesticide':    dict(cat='tool',   emoji='🧴', name='Pestisida',      desc='Basmi hama 1 lahan',    price=40,  cur='gold', badge='basic'),
    'mass_pest':    dict(cat='tool',   emoji='💊', name='Pestisida Massal',desc='Basmi semua hama',      price=15,  cur='gems', badge='hot'),
    'golden_fert':  dict(cat='tool',   emoji='✨', name='Pupuk Emas',     desc='+30% reward panen',     price=20,  cur='gems', badge='p2w'),
    'mega_fert':    dict(cat='tool',   emoji='💥', name='Mega Pupuk',     desc='Percepat 75% + bonus',  price=30,  cur='gems', badge='p2w'),
    'energy_pot':   dict(cat='tool',   emoji='🧃', name='Energy Potion',  desc='Isi +30 energy',        price=50,  cur='gold', badge='basic'),
    'plot_key':     dict(cat='land',   emoji='🔑', name='Kunci Lahan',    desc='Buka 1 lahan baru',     price=500, cur='gold', badge='p2w'),
    'seed_wheat':   dict(cat='seed',   emoji='🌾', name='Benih Gandum x5', desc='5 benih gandum',       price=40,  cur='gold', badge='basic'),
    'seed_corn':    dict(cat='seed',   emoji='🌽', name='Benih Jagung x3', desc='3 benih jagung · Lv2', price=120, cur='gold', badge='basic'),
    'seed_straw':   dict(cat='seed',   emoji='🍓', name='Benih Stroberi', desc='Hasil premium · Lv3',   price=12,  cur='gems', badge='nft'),
    'start_bundle': dict(cat='bundle', emoji='🎁', name='Starter Bundle', desc='2000🪙 +50💎 +500 FVT',price='0.005',cur='bnb',badge='p2w'),
}

# Energy: cost per action
ENERGY_COST = {'plant': 5, 'harvest': 2, 'fertilize': 0, 'pesticide': 1}
ENERGY_REGEN_PER_MIN = 2
ENERGY_BASE = 50

def xp_for_level(lv: int) -> int:
    return int(1000 * (1.45 ** (lv - 1)))

def weather_for_time(ts: float) -> str:
    """Deterministic weather rotation: changes every 4h. Golden is rare."""
    hour_block = int(ts // (4 * 3600))
    r = random.Random(hour_block)
    pool = ['sunny','sunny','rainy','rainy','windy','stormy','sunny','rainy','windy']
    if r.random() < 0.04:  # 4% chance golden hour
        return 'golden'
    return r.choice(pool)

# ─── DATABASE ────────────────────────────────────────────────────────────────

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=12000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextmanager
def db_exclusive():
    """Exclusive write transaction."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=12000")
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS players (
            user_id         INTEGER PRIMARY KEY,
            username        TEXT DEFAULT '',
            first_name      TEXT DEFAULT 'Farmer',
            gold            INTEGER DEFAULT 500,
            gems            INTEGER DEFAULT 20,
            fvt             INTEGER DEFAULT 0,
            level           INTEGER DEFAULT 1,
            xp              INTEGER DEFAULT 0,
            xp_max          INTEGER DEFAULT 1000,
            energy          INTEGER DEFAULT 50,
            energy_max      INTEGER DEFAULT 50,
            last_energy_ts  REAL    DEFAULT 0,
            total_harvest   INTEGER DEFAULT 0,
            total_combo     INTEGER DEFAULT 0,
            streak          INTEGER DEFAULT 0,
            last_streak_day TEXT    DEFAULT '',
            quest_harvest   INTEGER DEFAULT 0,
            quest_plant     INTEGER DEFAULT 0,
            quest_done      INTEGER DEFAULT 0,
            quest_date      TEXT    DEFAULT '',
            boost_mult      REAL    DEFAULT 1.0,
            boost_until     REAL    DEFAULT 0,
            wallet_addr     TEXT    DEFAULT '',
            is_banned       INTEGER DEFAULT 0,
            ban_reason      TEXT    DEFAULT '',
            suspicion       INTEGER DEFAULT 0,
            created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
            last_seen       TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS plots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            slot         INTEGER NOT NULL,
            crop_id      TEXT    DEFAULT '',
            planted_at   REAL    DEFAULT 0,
            grow_time    INTEGER DEFAULT 0,
            base_reward  INTEGER DEFAULT 0,
            base_xp      INTEGER DEFAULT 0,
            base_fvt     INTEGER DEFAULT 0,
            nonce        TEXT    DEFAULT '',
            has_pest     INTEGER DEFAULT 0,
            fertilized   TEXT    DEFAULT '',  -- fertilizer id or ''
            fert_boost   REAL    DEFAULT 0,
            locked       INTEGER DEFAULT 0,
            UNIQUE(user_id, slot),
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS inventory (
            user_id    INTEGER NOT NULL,
            item_id    TEXT    NOT NULL,
            emoji      TEXT    DEFAULT '📦',
            item_name  TEXT    DEFAULT '',
            item_type  TEXT    DEFAULT 'misc',
            quantity   INTEGER DEFAULT 0,
            fvt_value  INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, item_id),
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS upgrades (
            user_id    INTEGER NOT NULL,
            upgrade_id TEXT    NOT NULL,
            bought_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, upgrade_id)
        );

        CREATE TABLE IF NOT EXISTS market_listings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id  INTEGER,
            item_id    TEXT,
            item_name  TEXT,
            emoji      TEXT    DEFAULT '📦',
            item_type  TEXT    DEFAULT 'crop',
            quantity   INTEGER DEFAULT 1,
            price_fvt  INTEGER,
            fee        INTEGER DEFAULT 0,
            status     TEXT    DEFAULT 'active',
            buyer_id   INTEGER DEFAULT 0,
            created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
            sold_at    TEXT    DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS market_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER,
            item_id    TEXT,
            item_name  TEXT,
            emoji      TEXT,
            quantity   INTEGER,
            price_fvt  INTEGER,
            seller_id  INTEGER,
            buyer_id   INTEGER,
            sold_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS harvest_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            slot         INTEGER,
            crop_id      TEXT,
            nonce        TEXT,
            gold_earned  INTEGER,
            fvt_earned   INTEGER,
            xp_earned    INTEGER,
            combo_bonus  INTEGER DEFAULT 0,
            weather      TEXT    DEFAULT 'sunny',
            harvested_at REAL
        );

        CREATE TABLE IF NOT EXISTS rate_limits (
            user_id      INTEGER,
            action       TEXT,
            count        INTEGER DEFAULT 0,
            window_start REAL,
            PRIMARY KEY(user_id, action)
        );

        CREATE TABLE IF NOT EXISTS anticheat_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            code       TEXT,
            reason     TEXT,
            extra      TEXT    DEFAULT '',
            ts         TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        """)
    logger.info("✅ DB ready (WAL)")

# ─── DB HELPERS ──────────────────────────────────────────────────────────────

def get_player(user_id: int) -> Optional[dict]:
    with db() as c:
        row = c.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None

def upsert_player(user_id: int, username: str, first_name: str):
    with db() as c:
        c.execute("""
            INSERT INTO players(user_id, username, first_name)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username, first_name=excluded.first_name,
                last_seen=CURRENT_TIMESTAMP
        """, (user_id, username or '', first_name or 'Farmer'))
        existing = c.execute("SELECT COUNT(*) FROM plots WHERE user_id=?", (user_id,)).fetchone()[0]
        if existing == 0:
            for i in range(16):
                c.execute("INSERT OR IGNORE INTO plots(user_id,slot,locked) VALUES(?,?,?)",
                           (user_id, i, 0 if i < 6 else 1))

def inv_add(c, user_id: int, item_id: str, emoji: str, name: str,
            item_type: str, qty: int, fvt_value: int = 0):
    c.execute("""
        INSERT INTO inventory(user_id,item_id,emoji,item_name,item_type,quantity,fvt_value)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(user_id,item_id) DO UPDATE SET
            quantity=quantity+excluded.quantity
    """, (user_id, item_id, emoji, name, item_type, qty, fvt_value))

def inv_take(c, user_id: int, item_id: str, qty: int) -> bool:
    row = c.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?",
                    (user_id, item_id)).fetchone()
    if not row or row['quantity'] < qty:
        return False
    new_qty = row['quantity'] - qty
    if new_qty == 0:
        c.execute("DELETE FROM inventory WHERE user_id=? AND item_id=?", (user_id, item_id))
    else:
        c.execute("UPDATE inventory SET quantity=? WHERE user_id=? AND item_id=?",
                  (new_qty, user_id, item_id))
    return True

def get_upgrades(user_id: int) -> list:
    with db() as c:
        rows = c.execute("SELECT upgrade_id FROM upgrades WHERE user_id=?", (user_id,)).fetchall()
    return [r['upgrade_id'] for r in rows]

def calc_upgrade_effects(user_id: int) -> dict:
    owned = get_upgrades(user_id)
    eff = dict(grow_speed=0.0, inventory=0, pest_resist=0.0, harvest_xp=0.0, energy_max=0)
    for uid in owned:
        u = UPGRADES.get(uid, {})
        e = u.get('effect', '')
        v = u.get('val', 0)
        if e in eff:
            eff[e] += v
    return eff

def regen_energy(c, player: dict) -> int:
    now = time.time()
    last = player['last_energy_ts'] or now
    elapsed_min = (now - last) / 60
    regen = int(elapsed_min * ENERGY_REGEN_PER_MIN)
    if regen > 0:
        new_energy = min(player['energy'] + regen, player['energy_max'])
        c.execute("UPDATE players SET energy=?, last_energy_ts=? WHERE user_id=?",
                  (new_energy, now, player['user_id']))
        player['energy'] = new_energy
    return player['energy']

def rate_limit(user_id: int, action: str, max_n: int, window_s: int) -> bool:
    now = time.time()
    with db() as c:
        row = c.execute("SELECT count, window_start FROM rate_limits WHERE user_id=? AND action=?",
                        (user_id, action)).fetchone()
        if not row:
            c.execute("INSERT INTO rate_limits VALUES(?,?,1,?)", (user_id, action, now))
            return True
        count, ws = row['count'], row['window_start']
        if now - ws > window_s:
            c.execute("UPDATE rate_limits SET count=1, window_start=? WHERE user_id=? AND action=?",
                      (now, user_id, action))
            return True
        if count >= max_n:
            return False
        c.execute("UPDATE rate_limits SET count=count+1 WHERE user_id=? AND action=?",
                  (user_id, action))
    return True

def add_xp(c, user_id: int, xp_gain: int) -> dict:
    row = c.execute("SELECT xp, xp_max, level FROM players WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {}
    xp, xp_max, lv = row['xp'] + xp_gain, row['xp_max'], row['level']
    leveled = False
    while xp >= xp_max:
        xp -= xp_max
        lv += 1
        xp_max = xp_for_level(lv + 1)
        leveled = True
    c.execute("UPDATE players SET xp=?, xp_max=?, level=? WHERE user_id=?", (xp, xp_max, lv, user_id))
    return {'leveled': leveled, 'new_level': lv, 'xp': xp, 'xp_max': xp_max}

def flag_cheat(user_id: int, code: str, reason: str, extra: str = ''):
    with db() as c:
        c.execute("INSERT INTO anticheat_log(user_id,code,reason,extra) VALUES(?,?,?,?)",
                  (user_id, code, reason, extra))
        c.execute("UPDATE players SET suspicion=suspicion+15 WHERE user_id=?", (user_id,))
        score = c.execute("SELECT suspicion FROM players WHERE user_id=?", (user_id,)).fetchone()
    logger.warning(f"[AC] {user_id} | {code}: {reason}")
    if score and score['suspicion'] >= 75:
        with db() as c:
            c.execute("UPDATE players SET is_banned=1, ban_reason=? WHERE user_id=?", (code, user_id))

# ─── COMBO + WEATHER CALCULATIONS ────────────────────────────────────────────

def calc_harvest_reward(user_id: int, plot: dict, all_plots: list) -> dict:
    """Server calculates final reward with weather, combo, fertilizer."""
    crop = CROPS.get(plot['crop_id'])
    if not crop:
        return {}

    weather_key = weather_for_time(time.time())
    w = WEATHER[weather_key]

    # Base
    gold = plot['base_reward']
    fvt  = plot['base_fvt']
    xp   = plot['base_xp']

    # Fertilizer reward bonus
    if plot['fertilized']:
        fert = FERTILIZERS.get(plot['fertilized'], {})
        if fert.get('reward_boost', 0) > 0:
            gold = int(gold * (1 + fert['reward_boost']))
            fvt  = int(fvt  * (1 + fert['reward_boost']))

    # Weather multiplier
    gold = int(gold * w['reward_mult'])
    fvt  = int(fvt  * w['reward_mult'])

    # Upgrade XP bonus
    eff = calc_upgrade_effects(user_id)
    xp  = int(xp * (1 + eff.get('harvest_xp', 0)))

    # Rarity multiplier
    rarity_mult = RARITY_MULT.get(crop['rarity'], 1.0)
    gold = int(gold * rarity_mult)
    fvt  = int(fvt  * rarity_mult)

    # Combo bonus: count same combo_tag in ready plots
    combo_tag = crop.get('combo', '')
    same_tag_count = sum(
        1 for p in all_plots
        if p['crop_id'] == plot['crop_id'] or
           (p['crop_id'] and CROPS.get(p['crop_id'], {}).get('combo') == combo_tag)
    ) if combo_tag else 0

    combo_bonus = 0
    combo_label = ''
    if same_tag_count >= 3 and combo_tag in COMBO_BONUS:
        cb = COMBO_BONUS[combo_tag]
        combo_bonus_gold = int(gold * cb['gold_pct'] / 100)
        combo_bonus_fvt  = int(fvt  * cb['fvt_pct'] / 100)
        gold += combo_bonus_gold
        fvt  += combo_bonus_fvt
        combo_bonus = combo_bonus_gold
        combo_label = cb['label']

    # Pest: if plot has pest, 50% reward reduction
    if plot['has_pest']:
        gold = gold // 2
        fvt  = fvt  // 2

    # Boost
    with db() as c:
        p = c.execute("SELECT boost_mult, boost_until FROM players WHERE user_id=?", (user_id,)).fetchone()
    if p and p['boost_until'] > time.time():
        gold = int(gold * p['boost_mult'])
        fvt  = int(fvt  * p['boost_mult'])

    return dict(gold=gold, fvt=fvt, xp=xp, combo_bonus=combo_bonus,
                combo_label=combo_label, weather=weather_key,
                weather_label=w['label'], weather_emoji=w['emoji'])

# ─── STATE BUILDER ────────────────────────────────────────────────────────────

def build_state(user_id: int) -> dict:
    p = get_player(user_id)
    if not p:
        return {}
    weather_key = weather_for_time(time.time())
    w = WEATHER[weather_key]
    with db() as c:
        plots = c.execute("SELECT * FROM plots WHERE user_id=? ORDER BY slot", (user_id,)).fetchall()
        inv   = c.execute("SELECT * FROM inventory WHERE user_id=? AND quantity>0", (user_id,)).fetchall()
        owned_upgrades = c.execute("SELECT upgrade_id FROM upgrades WHERE user_id=?", (user_id,)).fetchall()
        nfts  = c.execute("SELECT nft_id FROM nfts WHERE owner_id=? LIMIT 1", (user_id,)).fetchall() if False else []
    eff = calc_upgrade_effects(user_id)
    return {
        'type':         'STATE_SYNC',
        'gold':         p['gold'],
        'gems':         p['gems'],
        'fvt':          p['fvt'],
        'level':        p['level'],
        'xp':           p['xp'],
        'xpMax':        p['xp_max'],
        'energy':       p['energy'],
        'energyMax':    p['energy_max'],
        'totalHarvest': p['total_harvest'],
        'streak':       p['streak'],
        'questHarvest': p['quest_harvest'],
        'questPlant':   p['quest_plant'],
        'questDone':    bool(p['quest_done']),
        'boostMult':    p['boost_mult'],
        'boostUntil':   p['boost_until'],
        'weather':      weather_key,
        'weatherEmoji': w['emoji'],
        'weatherLabel': w['label'],
        'upgradeEffects': eff,
        'plots': [{
            'slot':      pl['slot'],
            'locked':    bool(pl['locked']),
            'cropId':    pl['crop_id'] or None,
            'plantedAt': int(pl['planted_at'] * 1000) if pl['planted_at'] else 0,
            'growTime':  pl['grow_time'],
            'hasPest':   bool(pl['has_pest']),
            'fertilized':pl['fertilized'] or None,
            'nonce':     pl['nonce'],
        } for pl in plots],
        'inventory': [dict(it) for it in inv],
        'ownedUpgrades': [r['upgrade_id'] for r in owned_upgrades],
    }

# ─── WEBAPP HANDLER (core game loop) ─────────────────────────────────────────

async def handle_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    raw  = getattr(update.effective_message.web_app_data, 'data', None)
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except Exception:
        flag_cheat(user.id, 'INVALID_JSON', 'bad payload')
        return

    action = payload.get('action', '')
    data   = payload.get('data', {})

    p = get_player(user.id)
    if not p:
        upsert_player(user.id, user.username or '', user.first_name)
        p = get_player(user.id)
    if p['is_banned']:
        return

    async def reply_json(obj: dict):
        try:
            await ctx.bot.send_message(user.id, json.dumps(obj))
        except Exception:
            pass

    # ── GET_STATE ──────────────────────────────────────────────────────────
    if action == 'get_state':
        await reply_json(build_state(user.id))

    # ── PLANT ──────────────────────────────────────────────────────────────
    elif action == 'plant':
        crop_id  = data.get('cropId', '')
        slot     = int(data.get('slot', -1))
        if crop_id not in CROPS or slot < 0:
            return
        c_def = CROPS[crop_id]
        if c_def['lv'] > p['level']:
            return  # level gating, server enforced

        with db_exclusive() as c:
            p = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            plot = c.execute("SELECT * FROM plots WHERE user_id=? AND slot=?", (user.id, slot)).fetchone()
            if not plot or plot['locked'] or plot['crop_id']:
                return
            # Energy check
            regen_energy(c, p)
            if p['energy'] < ENERGY_COST['plant']:
                await reply_json({'type':'ERROR','msg':'⚡ Energy tidak cukup! Tunggu regen atau minum potion.'})
                return
            # Cost check
            if c_def['ct'] == 'gold' and p['gold'] < c_def['cost']:
                await reply_json({'type':'ERROR','msg':'❌ Koin tidak cukup!'})
                return
            if c_def['ct'] == 'gems' and p['gems'] < c_def['cost']:
                await reply_json({'type':'ERROR','msg':'❌ Gems tidak cukup!'})
                return
            # Deduct cost + energy
            if c_def['ct'] == 'gold':
                c.execute("UPDATE players SET gold=gold-?, energy=energy-?, quest_plant=quest_plant+1 WHERE user_id=?",
                           (c_def['cost'], ENERGY_COST['plant'], user.id))
            else:
                c.execute("UPDATE players SET gems=gems-?, energy=energy-?, quest_plant=quest_plant+1 WHERE user_id=?",
                           (c_def['cost'], ENERGY_COST['plant'], user.id))
            # Weather-adjusted grow time
            eff = calc_upgrade_effects(user.id)
            weather_key = weather_for_time(time.time())
            w = WEATHER[weather_key]
            base_grow = c_def['grow']
            actual_grow = int(base_grow * w['grow_mult'] * (1 - eff.get('grow_speed', 0)))
            actual_grow = max(30, actual_grow)
            nonce = f"{user.id}_{slot}_{int(time.time()*1000)}"
            # Pest chance from weather
            pest = random.random() < w['pest_chance']
            c.execute("""
                UPDATE plots SET crop_id=?, planted_at=?, grow_time=?, base_reward=?,
                    base_xp=?, base_fvt=?, nonce=?, has_pest=?, fertilized='', fert_boost=0
                WHERE user_id=? AND slot=?
            """, (crop_id, time.time(), actual_grow, c_def['reward'],
                  c_def['xp'], c_def['fvt'], nonce, 1 if pest else 0,
                  user.id, slot))
        state = build_state(user.id)
        pest_warn = ' ⚠️ Hama terdeteksi!' if pest else ''
        await reply_json({'type':'PLANT_OK', 'slot':slot, 'cropId':crop_id,
                          'hasPest':pest, 'msg': f'🌱 {c_def["name"]} ditanam!{pest_warn}',
                          'plots':state['plots'], 'gold':state['gold'], 'gems':state['gems'],
                          'energy':state['energy']})

    # ── FERTILIZE ─────────────────────────────────────────────────────────
    elif action == 'fertilize':
        slot    = int(data.get('slot', -1))
        fert_id = data.get('fertId', '')
        if fert_id not in FERTILIZERS or slot < 0:
            return
        fert = FERTILIZERS[fert_id]
        with db_exclusive() as c:
            plot = c.execute("SELECT * FROM plots WHERE user_id=? AND slot=?", (user.id, slot)).fetchone()
            if not plot or not plot['crop_id']:
                await reply_json({'type':'ERROR','msg':'❌ Lahan tidak ada tanaman!'})
                return
            if plot['fertilized']:
                await reply_json({'type':'ERROR','msg':'❌ Sudah difertilisasi!'})
                return
            if not inv_take(c, user.id, fert_id, 1):
                await reply_json({'type':'ERROR','msg':f'❌ Tidak punya {fert["name"]}!'})
                return
            # Apply grow boost
            new_grow = int(plot['grow_time'] * (1 - fert.get('grow_boost', 0)))
            new_grow = max(10, new_grow)
            c.execute("UPDATE plots SET fertilized=?, grow_time=?, fert_boost=? WHERE user_id=? AND slot=?",
                      (fert_id, new_grow, fert.get('reward_boost', 0), user.id, slot))
        state = build_state(user.id)
        await reply_json({'type':'FERT_OK','slot':slot,'msg':f'⚡ {fert["name"]} diterapkan!',
                          'plots':state['plots'],'inventory':state['inventory']})

    # ── USE PESTICIDE ──────────────────────────────────────────────────────
    elif action == 'use_pesticide':
        slot    = int(data.get('slot', -1))
        mass    = data.get('mass', False)
        pest_id = 'mass_pest' if mass else 'pesticide'
        with db_exclusive() as c:
            if not inv_take(c, user.id, pest_id, 1):
                await reply_json({'type':'ERROR','msg':'❌ Tidak punya pestisida!'})
                return
            if mass:
                c.execute("UPDATE plots SET has_pest=0 WHERE user_id=?", (user.id,))
                msg = '💊 Semua hama dibasmi!'
            else:
                if slot < 0:
                    return
                c.execute("UPDATE plots SET has_pest=0 WHERE user_id=? AND slot=?", (user.id, slot))
                msg = '🧴 Hama dibasmi!'
        state = build_state(user.id)
        await reply_json({'type':'PEST_OK','msg':msg,'plots':state['plots'],'inventory':state['inventory']})

    # ── HARVEST ────────────────────────────────────────────────────────────
    elif action == 'harvest':
        slot  = int(data.get('slot', -1))
        nonce = data.get('nonce', '')
        if slot < 0:
            return

        with db_exclusive() as c:
            plot = c.execute("SELECT * FROM plots WHERE user_id=? AND slot=?", (user.id, slot)).fetchone()
            if not plot or not plot['crop_id']:
                return
            # Server-side timing validation
            now = time.time()
            elapsed = now - plot['planted_at']
            if elapsed < plot['grow_time'] - 5:
                flag_cheat(user.id, 'HARVEST_EARLY',
                           f"slot={slot} elapsed={elapsed:.1f} < grow={plot['grow_time']}")
                await reply_json({'type':'ERROR','msg':'❌ Belum waktunya panen!'})
                return
            # Nonce replay check
            dup = c.execute("SELECT 1 FROM harvest_log WHERE nonce=? AND user_id=?",
                            (plot['nonce'], user.id)).fetchone()
            if dup:
                flag_cheat(user.id, 'REPLAY', f"nonce={plot['nonce']}")
                return
            # Energy
            p2 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            regen_energy(c, p2)
            if p2['energy'] < ENERGY_COST['harvest']:
                await reply_json({'type':'ERROR','msg':'⚡ Energy habis! Tunggu regen.'})
                return

            # All plots for combo calc
            all_plots = [dict(r) for r in c.execute(
                "SELECT * FROM plots WHERE user_id=? AND crop_id!=''", (user.id,)).fetchall()]

            rewards = calc_harvest_reward(user.id, dict(plot), all_plots)
            gold, fvt, xp_gain = rewards['gold'], rewards['fvt'], rewards['xp']

            # Add to inventory (1 unit of crop)
            crop_def = CROPS[plot['crop_id']]
            inv_add(c, user.id, plot['crop_id'], crop_def['emoji'], crop_def['name'],
                    'crop', 1, crop_def['fvt'])

            # Update player
            c.execute("""UPDATE players SET gold=gold+?, fvt=fvt+?,
                total_harvest=total_harvest+?, energy=energy-?,
                quest_harvest=quest_harvest+? WHERE user_id=?""",
                (gold, fvt, 1, ENERGY_COST['harvest'], 1, user.id))
            lv_info = add_xp(c, user.id, xp_gain)

            # Daily quest check
            p3 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            today = date.today().isoformat()
            if p3['quest_date'] != today:
                c.execute("UPDATE players SET quest_harvest=1, quest_plant=0, quest_done=0, quest_date=? WHERE user_id=?",
                           (today, user.id))
            elif p3['quest_harvest'] >= 5 and not p3['quest_done']:
                c.execute("UPDATE players SET gold=gold+300, gems=gems+20, fvt=fvt+50, quest_done=1 WHERE user_id=?",
                           (user.id,))

            # Streak
            if p3['last_streak_day'] != today:
                yesterday = (date.today() - __import__('datetime').timedelta(days=1)).isoformat()
                new_streak = p3['streak'] + 1 if p3['last_streak_day'] == yesterday else 1
                streak_bonus_gold = new_streak * 20 if new_streak % 7 == 0 else 0
                c.execute("UPDATE players SET streak=?, last_streak_day=?, gold=gold+? WHERE user_id=?",
                           (new_streak, today, streak_bonus_gold, user.id))

            # Clear plot
            c.execute("""UPDATE plots SET crop_id='', planted_at=0, grow_time=0,
                base_reward=0, base_xp=0, base_fvt=0, nonce='',
                has_pest=0, fertilized='', fert_boost=0 WHERE user_id=? AND slot=?""",
                (user.id, slot))

            # Log
            c.execute("""INSERT INTO harvest_log(user_id,slot,crop_id,nonce,gold_earned,
                fvt_earned,xp_earned,combo_bonus,weather,harvested_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (user.id, slot, plot['crop_id'], plot['nonce'],
                 gold, fvt, xp_gain, rewards['combo_bonus'],
                 rewards['weather'], now))

        state = build_state(user.id)
        resp = {
            'type':        'HARVEST_OK',
            'slot':        slot,
            'gold':        gold,
            'fvt':         fvt,
            'xp':          xp_gain,
            'comboBonus':  rewards['combo_bonus'],
            'comboLabel':  rewards['combo_label'],
            'weather':     rewards['weather'],
            'weatherEmoji':rewards['weather_emoji'],
            'leveled':     lv_info.get('leveled', False),
            'newLevel':    lv_info.get('new_level', p['level']),
            'plots':       state['plots'],
            'goldTotal':   state['gold'],
            'fvtTotal':    state['fvt'],
            'energy':      state['energy'],
            'inventory':   state['inventory'],
        }
        await reply_json(resp)

    # ── HARVEST ALL ────────────────────────────────────────────────────────
    elif action == 'harvest_all':
        harvested, total_gold, total_fvt, total_xp = [], 0, 0, 0
        with db_exclusive() as c:
            plots = [dict(r) for r in c.execute(
                "SELECT * FROM plots WHERE user_id=? AND crop_id!='' AND locked=0", (user.id,)).fetchall()]
            now = time.time()
            p2  = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            regen_energy(c, p2)
            avail_energy = p2['energy']
            all_plots_copy = list(plots)

            for plot in plots:
                elapsed = now - plot['planted_at']
                if elapsed < plot['grow_time'] - 5:
                    continue
                if avail_energy < ENERGY_COST['harvest']:
                    break
                dup = c.execute("SELECT 1 FROM harvest_log WHERE nonce=? AND user_id=?",
                                (plot['nonce'], user.id)).fetchone()
                if dup:
                    continue
                rewards = calc_harvest_reward(user.id, plot, all_plots_copy)
                crop_def = CROPS.get(plot['crop_id'], {})
                inv_add(c, user.id, plot['crop_id'], crop_def.get('emoji','🌾'),
                        crop_def.get('name', plot['crop_id']), 'crop', 1, crop_def.get('fvt', 0))
                total_gold += rewards['gold']
                total_fvt  += rewards['fvt']
                total_xp   += rewards['xp']
                avail_energy -= ENERGY_COST['harvest']
                harvested.append(plot['slot'])
                c.execute("""UPDATE plots SET crop_id='', planted_at=0, grow_time=0,
                    base_reward=0, base_xp=0, base_fvt=0, nonce='',
                    has_pest=0, fertilized='', fert_boost=0 WHERE user_id=? AND slot=?""",
                    (user.id, plot['slot']))
                c.execute("""INSERT INTO harvest_log(user_id,slot,crop_id,nonce,gold_earned,
                    fvt_earned,xp_earned,combo_bonus,weather,harvested_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (user.id, plot['slot'], plot['crop_id'], plot['nonce'],
                     rewards['gold'], rewards['fvt'], rewards['xp'],
                     rewards['combo_bonus'], rewards['weather'], now))

            if total_gold > 0:
                c.execute("""UPDATE players SET gold=gold+?, fvt=fvt+?,
                    total_harvest=total_harvest+?, energy=?,
                    quest_harvest=quest_harvest+? WHERE user_id=?""",
                    (total_gold, total_fvt, len(harvested), avail_energy, len(harvested), user.id))
                lv_info = add_xp(c, user.id, total_xp)

        state = build_state(user.id)
        await reply_json({'type':'HARVEST_ALL_OK','harvested':harvested,
                          'totalGold':total_gold,'totalFvt':total_fvt,'totalXp':total_xp,
                          'plots':state['plots'],'goldTotal':state['gold'],
                          'fvtTotal':state['fvt'],'energy':state['energy'],
                          'inventory':state['inventory']})

    # ── UNLOCK PLOT ────────────────────────────────────────────────────────
    elif action == 'unlock_plot':
        slot = int(data.get('slot', -1))
        cost = int(data.get('cost', 0))
        with db_exclusive() as c:
            p2 = dict(c.execute("SELECT gold FROM players WHERE user_id=?", (user.id,)).fetchone())
            if p2['gold'] < cost or cost <= 0:
                flag_cheat(user.id, 'UNLOCK_CHEAT', f"cost={cost},gold={p2['gold']}")
                return
            c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (cost, user.id))
            c.execute("UPDATE plots SET locked=0 WHERE user_id=? AND slot=?", (user.id, slot))
        state = build_state(user.id)
        await reply_json({'type':'UNLOCK_OK','slot':slot,'plots':state['plots'],'gold':state['gold']})

    # ── BUY SHOP ──────────────────────────────────────────────────────────
    elif action == 'buy_shop':
        item_id = data.get('itemId', '')
        if item_id not in SHOP_ITEMS:
            return
        item = SHOP_ITEMS[item_id]
        with db_exclusive() as c:
            p2 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            price = item['price']
            cur   = item['cur']
            if cur == 'gold':
                if p2['gold'] < price:
                    await reply_json({'type':'ERROR','msg':'❌ Koin tidak cukup!'})
                    return
                c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (price, user.id))
            elif cur == 'gems':
                if p2['gems'] < price:
                    await reply_json({'type':'ERROR','msg':'❌ Gems tidak cukup!'})
                    return
                c.execute("UPDATE players SET gems=gems-? WHERE user_id=?", (price, user.id))
            # Add to inventory
            if item_id == 'plot_key':
                # Unlock next locked plot
                plot = c.execute("SELECT slot FROM plots WHERE user_id=? AND locked=1 ORDER BY slot LIMIT 1",
                                 (user.id,)).fetchone()
                if plot:
                    c.execute("UPDATE plots SET locked=0 WHERE user_id=? AND slot=?",
                               (user.id, plot['slot']))
            elif item_id == 'energy_pot':
                p3 = dict(c.execute("SELECT energy, energy_max FROM players WHERE user_id=?", (user.id,)).fetchone())
                new_e = min(p3['energy'] + 30, p3['energy_max'])
                c.execute("UPDATE players SET energy=? WHERE user_id=?", (new_e, user.id))
            elif item_id.startswith('seed_'):
                # Map seed → crop and add to inv
                seed_map = {'seed_wheat':('wheat',5), 'seed_corn':('corn',3), 'seed_straw':('strawberry',1)}
                if item_id in seed_map:
                    cid, qty = seed_map[item_id]
                    cd = CROPS[cid]
                    inv_add(c, user.id, cid, cd['emoji'], cd['name'], 'seed', qty, cd['fvt'])
            else:
                inv_add(c, user.id, item_id, item['emoji'], item['name'], 'tool', 1, 0)
        state = build_state(user.id)
        await reply_json({'type':'BUY_OK','itemId':item_id,'msg':f'✅ {item["name"]} dibeli!',
                          'gold':state['gold'],'gems':state['gems'],
                          'energy':state['energy'],'plots':state['plots'],
                          'inventory':state['inventory']})

    # ── BUY UPGRADE ───────────────────────────────────────────────────────
    elif action == 'buy_upgrade':
        upg_id = data.get('upgradeId', '')
        if upg_id not in UPGRADES:
            return
        u = UPGRADES[upg_id]
        with db_exclusive() as c:
            # Already owned?
            already = c.execute("SELECT 1 FROM upgrades WHERE user_id=? AND upgrade_id=?",
                                 (user.id, upg_id)).fetchone()
            if already:
                await reply_json({'type':'ERROR','msg':'❌ Upgrade sudah dimiliki!'})
                return
            p2 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            if p2['level'] < u['req_lv']:
                await reply_json({'type':'ERROR','msg':f'❌ Butuh Level {u["req_lv"]}!'})
                return
            price = u['cost']
            if u['ct'] == 'gold':
                if p2['gold'] < price:
                    await reply_json({'type':'ERROR','msg':'❌ Koin tidak cukup!'})
                    return
                c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (price, user.id))
            # Apply energy_max instantly
            if u['effect'] == 'energy_max':
                c.execute("UPDATE players SET energy_max=energy_max+? WHERE user_id=?",
                           (int(u['val']), user.id))
            c.execute("INSERT OR IGNORE INTO upgrades(user_id,upgrade_id) VALUES(?,?)", (user.id, upg_id))
        state = build_state(user.id)
        await reply_json({'type':'UPGRADE_OK','upgradeId':upg_id,
                          'msg':f'🔧 {u["label"]} berhasil!',
                          'gold':state['gold'],'upgradeEffects':state['upgradeEffects'],
                          'energyMax':state['energyMax']})

    # ── MARKET LIST (from frontend) ────────────────────────────────────────
    elif action == 'market_list':
        item_id  = data.get('itemId', '')
        quantity = int(data.get('quantity', 1))
        price    = int(data.get('price', 0))
        if not item_id or quantity <= 0 or price <= 0 or price > 10_000_000:
            return
        if not rate_limit(user.id, 'market_sell', 10, 3600):
            await reply_json({'type':'ERROR','msg':'⏳ Terlalu banyak listing. Coba lagi nanti.'})
            return
        with db_exclusive() as c:
            ok = inv_take(c, user.id, item_id, quantity)
            if not ok:
                await reply_json({'type':'ERROR','msg':'❌ Item tidak cukup di inventory!'})
                return
            # Get item info
            item_row = c.execute("SELECT * FROM inventory WHERE user_id=? AND item_id=?",
                                  (user.id, item_id)).fetchone()
            # We just took it so reconstruct from CROPS or SHOP
            crop_def = CROPS.get(item_id, {})
            emoji = crop_def.get('emoji', '📦')
            name  = crop_def.get('name', item_id)
            fee   = int(price * MARKET_FEE)
            c.execute("""INSERT INTO market_listings(seller_id,item_id,item_name,emoji,item_type,
                quantity,price_fvt,fee) VALUES(?,?,?,?,?,?,?,?)""",
                (user.id, item_id, f"{name} x{quantity}", emoji, 'crop', quantity, price, fee))
        state = build_state(user.id)
        await reply_json({'type':'MARKET_LIST_OK','msg':'✅ Listing berhasil!',
                          'inventory':state['inventory']})

    # ── WALLET CONNECT ────────────────────────────────────────────────────
    elif action == 'wallet_connect':
        addr = data.get('addr', '')
        if addr and re.match(r'^0x[0-9a-fA-F]{40}$', addr):
            with db() as c:
                c.execute("UPDATE players SET wallet_addr=? WHERE user_id=?", (addr, user.id))
        else:
            flag_cheat(user.id, 'INVALID_WALLET', f"addr={addr}")

    # ── ANTI-CHEAT FLAG from client ────────────────────────────────────────
    elif action == 'anti_cheat_flag':
        flag_cheat(user.id, data.get('code','UNKNOWN'), f"[CLIENT] {data.get('reason','')}")

# ─── BOT COMMANDS ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_player(user.id, user.username or '', user.first_name)
    # Referral
    if ctx.args:
        ref = ctx.args[0]
        if ref.isdigit() and int(ref) != user.id:
            ref_id = int(ref)
            with db() as c:
                dup = c.execute("SELECT 1 FROM rate_limits WHERE user_id=? AND action='ref_'+?",
                                (ref_id, str(user.id))).fetchone()
                if not dup:
                    c.execute("UPDATE players SET gold=gold+200, gems=gems+5, fvt=fvt+100 WHERE user_id=?", (ref_id,))
                    c.execute("UPDATE players SET gold=gold+100 WHERE user_id=?", (user.id,))
                    try:
                        await ctx.bot.send_message(ref_id,
                            f"🎉 *{user.first_name}* bergabung via referralmu!\n+200🪙 +5💎 +100 FVT!",
                            parse_mode="Markdown")
                    except Exception:
                        pass
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Buka FarmVerse!", web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton("📊 Profil", callback_data="profile"),
         InlineKeyboardButton("🎒 Inventory", callback_data="inventory")],
        [InlineKeyboardButton("🌤️ Cuaca", callback_data="weather"),
         InlineKeyboardButton("🌾 Farm", callback_data="farm")],
        [InlineKeyboardButton("🔄 Market", callback_data="market"),
         InlineKeyboardButton("❓ Bantuan", callback_data="help")],
    ])
    await update.message.reply_text(
        f"🌾 *Selamat datang di FarmVerse P1, {user.first_name}!*\n\n"
        "🎮 *Core Loop:* Tanam → Tunggu → Panen → Upgrade → Repeat\n\n"
        "✨ *Phase 1 Features:*\n"
        "• 🌤️ Weather system — cuaca pengaruhi hasil panen\n"
        "• 🐛 Pest system — hama bisa menyerang tanaman\n"
        "• 🌱 Fertilizer — percepat dan bonus reward\n"
        "• 🍓 Combo bonus — tanam crop sejenis untuk multiplier\n"
        "• ⚡ Energy system — anti-spam farming\n"
        "• 🔧 Farm upgrade — permanent boost permanen\n"
        "• 🎒 Inventory — semua hasil panen tersimpan\n"
        "• 💰 P2E market — jual hasil panen nyata\n\n"
        "🛡️ *Server-authoritative* — semua divalidasi backend",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user if q else update.effective_user
    if q: await q.answer()
    p = get_player(user.id)
    if not p:
        upsert_player(user.id, user.username or '', user.first_name)
        p = get_player(user.id)
    eff = calc_upgrade_effects(user.id)
    with db() as c:
        inv_count = c.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=?", (user.id,)).fetchone()[0]
        upg_count = c.execute("SELECT COUNT(*) FROM upgrades WHERE user_id=?", (user.id,)).fetchone()[0]
    xp_pct = int(p['xp'] / max(p['xp_max'],1) * 100)
    weather_key = weather_for_time(time.time())
    w = WEATHER[weather_key]
    boost_str = f"⚡ {p['boost_mult']}x" if p['boost_until'] > time.time() else "❌ Off"
    text = (
        f"👨‍🌾 *{p['first_name']}*\n━━━━━━━━━━━━━━━━\n"
        f"⭐ Lv.*{p['level']}* · XP:{p['xp']:,}/{p['xp_max']:,} ({xp_pct}%)\n"
        f"🪙 *{p['gold']:,}*  💎 *{p['gems']:,}*  🔵 *{p['fvt']:,} FVT*\n"
        f"⚡ Energy: *{p['energy']}/{p['energy_max']}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🌾 Total Panen: *{p['total_harvest']:,}*\n"
        f"🔥 Streak: *{p['streak']} hari*\n"
        f"🎒 Inventory: *{inv_count} item*\n"
        f"🔧 Upgrade: *{upg_count} aktif*\n"
        f"🚀 Boost: {boost_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{w['emoji']} Cuaca: *{w['label']}*\n"
        f"🏡 Soil Speed: +{int(eff['grow_speed']*100)}% · "
        f"XP Bonus: +{int(eff['harvest_xp']*100)}%"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌾 Main", web_app=WebAppInfo(url=GAME_URL))]])
    if q:
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_weather(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    now = time.time()
    weather_key = weather_for_time(now)
    w = WEATHER[weather_key]
    # Next change
    next_change = (int(now // (4*3600)) + 1) * 4 * 3600
    secs_left   = int(next_change - now)
    h, m = secs_left // 3600, (secs_left % 3600) // 60
    lines = [f"{w['emoji']} *Cuaca Sekarang: {w['label']}*\n━━━━━━━━━━━━━━━━\n"]
    lines.append(f"🌱 Grow Speed: `{'%.0f%%' % ((1-w['grow_mult'])*100 if w['grow_mult']<1 else (w['grow_mult']-1)*100)}` {'🐢 lebih lambat' if w['grow_mult']<1 else '⚡ lebih cepat' if w['grow_mult']>1 else 'normal'}")
    lines.append(f"💰 Reward: `{'%.0f%%' % ((1-w['reward_mult'])*100 if w['reward_mult']<1 else (w['reward_mult']-1)*100)}` {'📉 berkurang' if w['reward_mult']<1 else '📈 bonus' if w['reward_mult']>1 else 'normal'}")
    lines.append(f"🐛 Risiko Hama: `{'%.0f%%' % (w['pest_chance']*100)}`")
    lines.append(f"\n⏳ Berubah dalam *{h}j {m}m*\n")
    lines.append("🌈 *Semua Cuaca:*")
    for k, wd in WEATHER.items():
        lines.append(f"  {wd['emoji']} {wd['label']} — grow{'%.0f%%'%(abs(wd['grow_mult']-1)*100)}, reward{'%.0f%%'%(abs(wd['reward_mult']-1)*100)}")
    text = "\n".join(lines)
    if q:
        await q.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_farm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    user = q.from_user if q else update.effective_user
    p = get_player(user.id)
    if not p:
        upsert_player(user.id, user.username or '', user.first_name)
        p = get_player(user.id)
    with db() as c:
        plots = c.execute("SELECT * FROM plots WHERE user_id=? ORDER BY slot", (user.id,)).fetchall()
    now = time.time()
    lines = [f"🌾 *Farm {p['first_name']}* · Lv.{p['level']}\n━━━━━━━━━━━━━━━━"]
    ready = growing = locked = empty = 0
    pest_plots = []
    for pl in plots:
        if pl['locked']:
            locked += 1
            continue
        if not pl['crop_id']:
            empty += 1
            continue
        elapsed = now - pl['planted_at']
        crop = CROPS.get(pl['crop_id'], {})
        remaining = max(0, int(pl['grow_time'] - elapsed))
        if remaining == 0:
            ready += 1
        else:
            growing += 1
        if pl['has_pest']:
            pest_plots.append(pl['slot'] + 1)
    lines.append(f"✅ Siap Panen: *{ready}*  🌱 Tumbuh: *{growing}*")
    lines.append(f"🌿 Kosong: *{empty}*  🔒 Terkunci: *{locked}*")
    lines.append(f"⚡ Energy: *{p['energy']}/{p['energy_max']}*")
    if pest_plots:
        lines.append(f"\n⚠️ *Hama di lahan: {', '.join(map(str, pest_plots))}!*")
        lines.append("Gunakan /pesticide atau beli pestisida di toko!")
    eff = calc_upgrade_effects(user.id)
    owned_upg = get_upgrades(user.id)
    if owned_upg:
        lines.append(f"\n🔧 *Upgrade Aktif:*")
        for uid in owned_upg[:5]:
            u = UPGRADES.get(uid, {})
            lines.append(f"  {u.get('label','?')}")
    lines.append(f"\n🛒 *Upgrade tersedia:*")
    for uid, u in UPGRADES.items():
        if uid not in owned_upg and p['level'] >= u['req_lv']:
            lines.append(f"  {u['label']} — {u['cost']:,}🪙  (/upgrade {uid})")
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Buka Farm", web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton("🌤️ Cuaca", callback_data="weather"),
         InlineKeyboardButton("📊 Profil", callback_data="profile")],
    ])
    if q:
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_inventory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user if q else update.effective_user
    if q: await q.answer()
    if not get_player(user.id):
        upsert_player(user.id, user.username or '', user.first_name)
    with db() as c:
        items = c.execute("SELECT * FROM inventory WHERE user_id=? AND quantity>0 ORDER BY item_type, item_id",
                           (user.id,)).fetchall()
    if not items:
        text = "🎒 *Inventory Kosong*\nPanen tanaman untuk mengisi inventory!"
    else:
        crops  = [dict(i) for i in items if i['item_type'] == 'crop']
        tools  = [dict(i) for i in items if i['item_type'] == 'tool']
        seeds  = [dict(i) for i in items if i['item_type'] == 'seed']
        lines  = ["🎒 *Inventory Kamu*\n━━━━━━━━━━━━━━━━"]
        total_items = sum(i['quantity'] for i in items)
        total_fvt   = sum(i['quantity'] * i['fvt_value'] for i in items)
        lines.append(f"📦 {total_items} item · Est. *{total_fvt:,} FVT*\n")
        if crops:
            lines.append("🌾 *Hasil Panen:*")
            for it in crops[:12]:
                lines.append(f"  {it['emoji']} {it['item_name']} — *x{it['quantity']}*  (~{it['fvt_value']*it['quantity']} FVT)")
        if tools:
            lines.append("\n🔧 *Tools:*")
            for it in tools:
                lines.append(f"  {it['emoji']} {it['item_name']} — *x{it['quantity']}*")
        if seeds:
            lines.append("\n🌱 *Benih:*")
            for it in seeds:
                lines.append(f"  {it['emoji']} {it['item_name']} — *x{it['quantity']}*")
        lines.append(f"\n━━━━━━━━━━━━━━━━")
        lines.append("Gunakan `/sell <item_id> <qty> <harga>` untuk jual")
        text = "\n".join(lines)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌾 Main", web_app=WebAppInfo(url=GAME_URL))]])
    if q:
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/sell <item_id> <qty> <price_fvt>"""
    user = update.effective_user
    p = get_player(user.id)
    if not p or p['is_banned']:
        await update.message.reply_text("❌ Akun tidak valid."); return
    if not ctx.args or len(ctx.args) < 3:
        with db() as c:
            items = c.execute("SELECT item_id, quantity FROM inventory WHERE user_id=? AND quantity>0 AND item_type='crop'",
                               (user.id,)).fetchall()
        inv_str = "\n".join(f"  `{i['item_id']}` x{i['quantity']}" for i in items[:10]) if items else "  (kosong)"
        await update.message.reply_text(
            "📦 *Cara Jual:* `/sell <item_id> <qty> <harga_fvt>`\n\n"
            f"🎒 *Inventory Panen:*\n{inv_str}\n\n"
            "Contoh: `/sell wheat 10 500`",
            parse_mode="Markdown"); return
    item_id = ctx.args[0].lower()
    try:
        qty   = int(ctx.args[1])
        price = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("❌ Format: /sell <item_id> <qty> <harga>"); return
    if qty <= 0 or price <= 0:
        await update.message.reply_text("❌ Qty dan harga harus > 0"); return
    if price > 10_000_000:
        flag_cheat(user.id, "PRICE_INJECT", f"price={price}")
        await update.message.reply_text("❌ Harga terlalu tinggi!"); return
    if not rate_limit(user.id, 'market_sell', 10, 3600):
        await update.message.reply_text("⏳ Terlalu banyak listing/jam."); return
    crop = CROPS.get(item_id)
    emoji = crop['emoji'] if crop else '📦'
    name  = crop['name']  if crop else item_id.capitalize()
    with db_exclusive() as c:
        ok = inv_take(c, user.id, item_id, qty)
        if not ok:
            await update.message.reply_text(f"❌ Item `{item_id}` tidak cukup di inventory!\nCek `/inventory`",
                                            parse_mode="Markdown"); return
        fee = int(price * MARKET_FEE)
        c.execute("""INSERT INTO market_listings(seller_id,item_id,item_name,emoji,item_type,quantity,price_fvt,fee)
            VALUES(?,?,?,?,?,?,?,?)""",
            (user.id, item_id, f"{name} x{qty}", emoji, 'crop', qty, price, fee))
        lid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    await update.message.reply_text(
        f"✅ *Listing #{lid} Berhasil!*\n"
        f"{emoji} *{name}* x{qty}\n"
        f"💰 *{price:,} FVT* · Fee: *{fee:,}* · Net: *{price-fee:,}*\n"
        f"Batalkan: `/cancellist {lid}`",
        parse_mode="Markdown")

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/buy <listing_id>"""
    user = update.effective_user
    p = get_player(user.id)
    if not p or p['is_banned']:
        await update.message.reply_text("❌ Akun tidak valid."); return
    if not ctx.args:
        await update.message.reply_text("Usage: `/buy <listing_id>`", parse_mode="Markdown"); return
    try:
        lid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID harus angka!"); return
    with db_exclusive() as c:
        row = c.execute("SELECT * FROM market_listings WHERE id=? AND status='active'", (lid,)).fetchone()
        if not row:
            await update.message.reply_text("❌ Listing tidak ditemukan!"); return
        l = dict(row)
        if l['seller_id'] == user.id:
            await update.message.reply_text("❌ Tidak bisa beli listing sendiri!"); return
        p2 = dict(c.execute("SELECT fvt FROM players WHERE user_id=?", (user.id,)).fetchone())
        if p2['fvt'] < l['price_fvt']:
            await update.message.reply_text(
                f"❌ FVT tidak cukup!\nKamu: *{p2['fvt']:,}* · Harga: *{l['price_fvt']:,}*",
                parse_mode="Markdown"); return
        if not rate_limit(user.id, 'market_buy', 20, 3600):
            await update.message.reply_text("⏳ Terlalu banyak pembelian."); return
        net = l['price_fvt'] - l['fee']
        c.execute("UPDATE players SET fvt=fvt-? WHERE user_id=?", (l['price_fvt'], user.id))
        c.execute("UPDATE players SET fvt=fvt+? WHERE user_id=?", (net, l['seller_id']))
        c.execute("UPDATE market_listings SET status='sold', buyer_id=?, sold_at=CURRENT_TIMESTAMP WHERE id=?",
                  (user.id, lid))
        crop = CROPS.get(l['item_id'], {})
        inv_add(c, user.id, l['item_id'], l['emoji'], l['item_name'], l['item_type'],
                l['quantity'], crop.get('fvt', 0))
        c.execute("""INSERT INTO market_history(listing_id,item_id,item_name,emoji,quantity,
            price_fvt,seller_id,buyer_id) VALUES(?,?,?,?,?,?,?,?)""",
            (lid, l['item_id'], l['item_name'], l['emoji'], l['quantity'],
             l['price_fvt'], l['seller_id'], user.id))
    await update.message.reply_text(
        f"✅ *Pembelian Berhasil!*\n{l['emoji']} *{l['item_name']}*\n"
        f"💰 Dibayar: *{l['price_fvt']:,} FVT*\nItem masuk `/inventory`!",
        parse_mode="Markdown")
    try:
        await ctx.bot.send_message(l['seller_id'],
            f"🎉 Item terjual!\n{l['emoji']} {l['item_name']}\n+{net:,} FVT",
            parse_mode="Markdown")
    except Exception:
        pass

async def cmd_cancellist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: `/cancellist <id>`", parse_mode="Markdown"); return
    try:
        lid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID harus angka!"); return
    with db_exclusive() as c:
        row = c.execute("SELECT * FROM market_listings WHERE id=? AND seller_id=? AND status='active'",
                        (lid, user.id)).fetchone()
        if not row:
            await update.message.reply_text("❌ Listing tidak ditemukan!"); return
        l = dict(row)
        c.execute("UPDATE market_listings SET status='cancelled' WHERE id=?", (lid,))
        crop = CROPS.get(l['item_id'], {})
        inv_add(c, user.id, l['item_id'], l['emoji'], l['item_name'], l['item_type'],
                l['quantity'], crop.get('fvt', 0))
    await update.message.reply_text(
        f"✅ Listing #{lid} dibatalkan.\n{l['emoji']} {l['item_name']} dikembalikan ke inventory.",
        parse_mode="Markdown")

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    with db() as c:
        active = c.execute("""SELECT ml.*, p.first_name AS sname FROM market_listings ml
            JOIN players p ON p.user_id=ml.seller_id WHERE ml.status='active'
            ORDER BY ml.created_at DESC LIMIT 12""").fetchall()
        vol = c.execute("SELECT COALESCE(SUM(price_fvt),0) FROM market_listings WHERE status='sold'").fetchone()[0]
        trades = c.execute("SELECT COUNT(*) FROM market_history WHERE date(sold_at)=date('now')").fetchone()[0]
    if not active:
        text = "🔄 *Marketplace*\nBelum ada listing.\n\n`/sell <item> <qty> <harga>` untuk mulai!"
    else:
        lines = [f"🔄 *Marketplace* · Vol:{vol:,} FVT · {trades} trades hari ini\n━━━━━━━━━━━━━━━━"]
        for l in active:
            lines.append(f"{l['emoji']} *{l['item_name']}* · {l['price_fvt']:,} FVT\n   👤 {l['sname']} · ID: `#{l['id']}`")
        text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Buka Market", web_app=WebAppInfo(url=GAME_URL+'#market'))],
        [InlineKeyboardButton("📜 Riwayat", callback_data="market_hist")],
    ])
    if q:
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_market_hist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    with db() as c:
        rows = c.execute("""SELECT mh.*, ps.first_name AS sname, pb.first_name AS bname
            FROM market_history mh
            LEFT JOIN players ps ON ps.user_id=mh.seller_id
            LEFT JOIN players pb ON pb.user_id=mh.buyer_id
            ORDER BY mh.sold_at DESC LIMIT 10""").fetchall()
    if not rows:
        text = "📜 *Riwayat Market*\nBelum ada transaksi."
    else:
        lines = ["📜 *Riwayat Market*\n━━━━━━━━━━━━━━━━"]
        for r in rows:
            lines.append(f"{r['emoji']} *{r['item_name']}* · {r['price_fvt']:,} FVT\n"
                         f"   {r['sname']}→{r['bname']or'?'} · {r['sold_at'][:16]}")
        text = "\n".join(lines)
    if q:
        await q.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    with db() as c:
        rows = c.execute("""SELECT user_id, first_name, level, total_harvest, fvt
            FROM players WHERE is_banned=0 ORDER BY total_harvest DESC LIMIT 10""").fetchall()
    medals = ['🥇','🥈','🥉']
    lines = ["🏆 *Leaderboard FarmVerse*\n━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows):
        m = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{m} *{r['first_name']}* — {r['total_harvest']:,} panen · Lv.{r['level']}")
    text = "\n".join(lines)
    if q:
        await q.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/upgrade [upgrade_id]"""
    user = update.effective_user
    p = get_player(user.id)
    if not p:
        upsert_player(user.id, user.username or '', user.first_name)
        p = get_player(user.id)
    owned = get_upgrades(user.id)
    if not ctx.args:
        lines = ["🔧 *Farm Upgrades*\n━━━━━━━━━━━━━━━━"]
        for uid, u in UPGRADES.items():
            status = "✅" if uid in owned else ("🔒" if p['level'] < u['req_lv'] else "🛒")
            req    = f"Lv.{u['req_lv']}" if p['level'] < u['req_lv'] else ""
            lines.append(f"{status} {u['label']} {req}\n   {u['desc']} · {u['cost']:,}🪙\n   `/upgrade {uid}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown"); return
    upg_id = ctx.args[0]
    if upg_id not in UPGRADES:
        await update.message.reply_text("❌ Upgrade tidak dikenal!"); return
    u = UPGRADES[upg_id]
    if upg_id in owned:
        await update.message.reply_text(f"✅ {u['label']} sudah dimiliki!"); return
    if p['level'] < u['req_lv']:
        await update.message.reply_text(f"❌ Butuh Level {u['req_lv']}! Kamu Level {p['level']}."); return
    if u['ct'] == 'gold' and p['gold'] < u['cost']:
        await update.message.reply_text(f"❌ Koin tidak cukup! ({p['gold']:,}/{u['cost']:,})"); return
    with db_exclusive() as c:
        if u['ct'] == 'gold':
            c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (u['cost'], user.id))
        if u['effect'] == 'energy_max':
            c.execute("UPDATE players SET energy_max=energy_max+? WHERE user_id=?", (int(u['val']), user.id))
        c.execute("INSERT OR IGNORE INTO upgrades(user_id,upgrade_id) VALUES(?,?)", (user.id, upg_id))
    await update.message.reply_text(
        f"🔧 *{u['label']}* berhasil dibeli!\n{u['desc']}\n✅ Aktif sekarang!",
        parse_mode="Markdown")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    text = (
        "❓ *Panduan FarmVerse Phase 1*\n━━━━━━━━━━━━━━━━\n\n"
        "🌾 *Farming (Core Loop):*\n"
        "Tanam → Tunggu → Panen → Upgrade → Repeat\n\n"
        "🌤️ *Weather:*\n`/weather` — cek cuaca & efeknya\n\n"
        "🌱 *Farm:*\n`/farm` — status lahan & upgrade\n"
        "`/upgrade` — lihat/beli upgrade permanen\n"
        "`/upgrade <id>` — beli upgrade spesifik\n\n"
        "🎒 *Inventory:*\n`/inventory` — lihat semua item\n\n"
        "🔄 *Market:*\n`/market` — listing aktif\n"
        "`/sell <item> <qty> <harga>` — jual dari inventory\n"
        "`/buy <id>` — beli listing\n"
        "`/cancellist <id>` — batalkan (item kembali)\n\n"
        "📊 */profile* · */leaderboard* · */balance*\n\n"
        "🐛 *Tips Gameplay:*\n"
        "• Tanam crop sejenis untuk **Combo Bonus**!\n"
        "• Gunakan pupuk sebelum panen untuk bonus\n"
        "• Cuaca 🌟 Golden = reward 1.5x!\n"
        "• Upgrade soil untuk grow speed permanen\n"
        "• Energy regen otomatis ~2/menit\n\n"
        "🛡️ *Server-authoritative* — semua divalidasi backend."
    )
    if q:
        await q.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    p = get_player(user.id)
    if not p:
        upsert_player(user.id, user.username or '', user.first_name)
        p = get_player(user.id)
    await update.message.reply_text(
        f"💰 *Saldo {p['first_name']}*\n"
        f"🪙 {p['gold']:,} Koin · 💎 {p['gems']:,} Gems · 🔵 {p['fvt']:,} FVT\n"
        f"⚡ Energy: {p['energy']}/{p['energy_max']}\n"
        f"💼 Wallet: `{p['wallet_addr'] or 'Belum terhubung'}`",
        parse_mode="Markdown")

# ─── ADMIN ────────────────────────────────────────────────────────────────────

def require_admin(fn):
    async def wrapper(update, ctx):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Hanya admin."); return
        return await fn(update, ctx)
    return wrapper

@require_admin
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        total   = c.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        active  = c.execute("SELECT COUNT(*) FROM players WHERE date(last_seen)=date('now')").fetchone()[0]
        banned  = c.execute("SELECT COUNT(*) FROM players WHERE is_banned=1").fetchone()[0]
        harvest = c.execute("SELECT COALESCE(SUM(total_harvest),0) FROM players").fetchone()[0]
        vol     = c.execute("SELECT COALESCE(SUM(price_fvt),0) FROM market_listings WHERE status='sold'").fetchone()[0]
        flags   = c.execute("SELECT COUNT(*) FROM anticheat_log WHERE date(ts)=date('now')").fetchone()[0]
    weather_key = weather_for_time(time.time())
    w = WEATHER[weather_key]
    await update.message.reply_text(
        f"📊 *Admin Dashboard v4*\n━━━━━━━━━━━━━━━━\n"
        f"👥 {total:,} pemain · Aktif: {active} · Ban: {banned}\n"
        f"🌾 Total Panen: {harvest:,}\n"
        f"💰 Market Vol: {vol:,} FVT\n"
        f"🛡️ AC Flags Hari Ini: {flags}\n"
        f"{w['emoji']} Cuaca: {w['label']}",
        parse_mode="Markdown")

@require_admin
async def cmd_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text("Usage: /give <uid> <gold|gems|fvt|item_id> <amount>"); return
    try:
        uid = int(ctx.args[0]); cur = ctx.args[1].lower(); amt = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("❌ Format salah!"); return
    col = {'gold':'gold','gems':'gems','fvt':'fvt'}.get(cur)
    if col:
        with db() as c:
            c.execute(f"UPDATE players SET {col}={col}+? WHERE user_id=?", (amt, uid))
        await update.message.reply_text(f"✅ +{amt:,} {cur} → {uid}")
    elif cur in CROPS:
        with db() as c:
            cd = CROPS[cur]
            inv_add(c, uid, cur, cd['emoji'], cd['name'], 'crop', amt, cd['fvt'])
        await update.message.reply_text(f"✅ +{amt}x {CROPS[cur]['name']} → {uid}")
    else:
        await update.message.reply_text("❌ Tidak dikenal.")

@require_admin
async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /ban <uid> [reason]"); return
    uid = int(ctx.args[0])
    reason = ' '.join(ctx.args[1:]) or 'Admin ban'
    with db() as c:
        c.execute("UPDATE players SET is_banned=1, ban_reason=? WHERE user_id=?", (reason, uid))
    await update.message.reply_text(f"🚫 {uid} dibanned: {reason}")

@require_admin
async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /unban <uid>"); return
    uid = int(ctx.args[0])
    with db() as c:
        c.execute("UPDATE players SET is_banned=0, ban_reason='', suspicion=0 WHERE user_id=?", (uid,))
    await update.message.reply_text(f"✅ {uid} di-unban.")

@require_admin
async def cmd_flags_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = c.execute("""SELECT acl.*, p.first_name FROM anticheat_log acl
            JOIN players p ON p.user_id=acl.user_id
            ORDER BY acl.ts DESC LIMIT 15""").fetchall()
    if not rows:
        await update.message.reply_text("✅ Tidak ada flags."); return
    lines = ["🛡️ *AC Flags*\n━━━━━━━━━━━━━━━━"]
    for r in rows:
        lines.append(f"👤 {r['first_name']} ({r['user_id']})\n   ⚠️ {r['code']}: {r['reason']}\n   🕐 {r['ts'][:16]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─── CALLBACK HANDLER ────────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    handlers = {
        'profile':     cmd_profile,
        'inventory':   cmd_inventory,
        'weather':     cmd_weather,
        'farm':        cmd_farm,
        'market':      cmd_market,
        'market_hist': cmd_market_hist,
        'help':        cmd_help,
    }
    if data in handlers:
        await handlers[data](update, ctx)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    logger.info(f"🌾 FarmVerse v4 Phase 1 — token: {BOT_TOKEN[:12]}...")

    app = Application.builder().token(BOT_TOKEN).build()

    cmds = [
        ("start",        cmd_start),
        ("profile",      cmd_profile),
        ("inventory",    cmd_inventory),
        ("balance",      cmd_balance),
        ("weather",      cmd_weather),
        ("farm",         cmd_farm),
        ("upgrade",      cmd_upgrade),
        ("market",       cmd_market),
        ("sell",         cmd_sell),
        ("buy",          cmd_buy),
        ("cancellist",   cmd_cancellist),
        ("leaderboard",  cmd_leaderboard),
        ("help",         cmd_help),
        ("admin",        cmd_admin),
        ("give",         cmd_give),
        ("ban",          cmd_ban),
        ("unban",        cmd_unban),
        ("flags",        cmd_flags_cmd),
    ]
    for name, fn in cmds:
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp))

    logger.info("✅ Bot running — polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
