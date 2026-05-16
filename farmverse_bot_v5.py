"""
FarmVerse Bot — v5.0  PHASE 1: FOUNDATION
==========================================
CORE LOOP: Tanam → Tunggu → Panen → Upgrade → Unlock → Repeat

BACKEND AUTHORITATIVE (Priority 1):
  • Semua reward, inventory, FVT, timer, XP dihitung server
  • Frontend = UI saja, tidak trusted
  • Setiap harvest: nonce DB, replay protection, timing check
  • Rate limiting per action, suspicion scoring, auto-ban

GAMEPLAY SYSTEMS:
  • 14 tanaman + rarity: common → legendary
  • Combo bonus (grain/veggie/fruit/flower)
  • Weather (5 jenis, server-deterministic per 4 jam)
  • Pest system (random saat tanam, needs pesticide)
  • Fertilizer (4 jenis: speed / reward boost)
  • Energy system (anti-spam, regen otomatis)
  • Farm upgrade permanent (soil, barn, water, tool, energy)
  • XP + Level gating server-side
  • Daily quest + streak reward
  • Inventory real (panen masuk, jual kurangi)
  • Market: item berpindah, fee 5%, history
  • State sync lengkap ke frontend

COMMANDS:
  User:  /start /farm /profile /inventory /weather /quest
         /sell /buy /cancellist /upgrade /pesticide
         /referral /help /balance
  Admin: /admin /ban /unban /give /broadcast /flags /resetquest

Requirements:
  pip install "python-telegram-bot>=20.7" python-dotenv
"""

import asyncio, json, os, sys, time, logging, re, sqlite3, random
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Optional

from dotenv import load_dotenv
load_dotenv(override=True)

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                           MessageHandler, ContextTypes, filters)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "").strip()
GAME_URL    = os.getenv("GAME_URL",  "https://farmverse-liart.vercel.app")
ADMIN_ID    = int(os.getenv("ADMIN_ID", "0"))
DB_PATH     = "farmverse_v5.db"
MARKET_FEE  = 0.05  # 5% seller fee

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("fv5")

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    log.critical("❌ BOT_TOKEN tidak valid!"); sys.exit(1)

# ─── GAME DATA (Single Source of Truth) ─────────────────────────────────────

RARITY_MULT  = {'common':1.0,'uncommon':1.2,'rare':1.5,'epic':2.0,'legendary':3.0}
RARITY_EMOJI = {'common':'⚪','uncommon':'🟢','rare':'🔵','epic':'🟣','legendary':'🟡'}
RARITY_LABEL = {'common':'Common','uncommon':'Uncommon','rare':'Rare','epic':'Epic','legendary':'Legendary'}

CROPS = {
    # id: cost, ct(gold/gems), grow(s), reward(gold), xp, fvt, emoji, name, lv, rarity, combo
    'wheat':       dict(cost=10,  ct='gold', grow=120,  reward=50,   xp=8,   fvt=5,   emoji='🌾', name='Gandum',        lv=1, rarity='common',    combo='grain'),
    'carrot':      dict(cost=25,  ct='gold', grow=300,  reward=120,  xp=15,  fvt=12,  emoji='🥕', name='Wortel',         lv=1, rarity='common',    combo='veggie'),
    'corn':        dict(cost=50,  ct='gold', grow=180,  reward=200,  xp=22,  fvt=20,  emoji='🌽', name='Jagung',         lv=2, rarity='uncommon',  combo='grain'),
    'tomato':      dict(cost=40,  ct='gold', grow=240,  reward=160,  xp=18,  fvt=16,  emoji='🍅', name='Tomat',          lv=2, rarity='uncommon',  combo='veggie'),
    'pumpkin':     dict(cost=70,  ct='gold', grow=360,  reward=280,  xp=30,  fvt=28,  emoji='🎃', name='Labu',           lv=3, rarity='uncommon',  combo='veggie'),
    'sunflower':   dict(cost=100, ct='gold', grow=480,  reward=380,  xp=42,  fvt=38,  emoji='🌻', name='Bunga Matahari', lv=4, rarity='rare',      combo='flower'),
    'rose':        dict(cost=120, ct='gold', grow=420,  reward=340,  xp=38,  fvt=34,  emoji='🌹', name='Mawar',          lv=4, rarity='rare',      combo='flower'),
    'mango':       dict(cost=130, ct='gold', grow=600,  reward=520,  xp=55,  fvt=52,  emoji='🥭', name='Mangga',         lv=5, rarity='rare',      combo='fruit'),
    'grape':       dict(cost=200, ct='gold', grow=900,  reward=850,  xp=80,  fvt=85,  emoji='🍇', name='Anggur',         lv=6, rarity='epic',      combo='fruit'),
    'dragon_fruit':dict(cost=250, ct='gold', grow=1200, reward=1200, xp=110, fvt=120, emoji='🐉', name='Buah Naga',      lv=8, rarity='epic',      combo='fruit'),
    'strawberry':  dict(cost=15,  ct='gems', grow=180,  reward=400,  xp=45,  fvt=80,  emoji='🍓', name='Stroberi',       lv=3, rarity='rare',      combo='fruit', premium=True),
    'blueberry':   dict(cost=25,  ct='gems', grow=240,  reward=600,  xp=60,  fvt=120, emoji='🫐', name='Blueberry',      lv=4, rarity='epic',      combo='fruit', premium=True),
    'golden_wheat':dict(cost=40,  ct='gems', grow=300,  reward=900,  xp=90,  fvt=180, emoji='✨', name='Gandum Emas',    lv=7, rarity='legendary', combo='grain', premium=True),
    'crystal_rose':dict(cost=50,  ct='gems', grow=360,  reward=1100, xp=100, fvt=220, emoji='💎', name='Mawar Kristal',  lv=9, rarity='legendary', combo='flower',premium=True),
}

COMBO_BONUS = {
    'grain': dict(gold_pct=15, fvt_pct=10, label='🌾 Grain Combo!'),
    'veggie':dict(gold_pct=12, fvt_pct=8,  label='🥕 Veggie Combo!'),
    'fruit': dict(gold_pct=20, fvt_pct=15, label='🍓 Fruit Combo!'),
    'flower':dict(gold_pct=18, fvt_pct=12, label='🌸 Flower Combo!'),
}

WEATHER = {
    'sunny': dict(emoji='☀️',  label='Cerah',       grow_mult=1.0, reward_mult=1.0,  pest_chance=0.05),
    'rainy': dict(emoji='🌧️', label='Hujan',        grow_mult=0.85,reward_mult=1.15, pest_chance=0.02),
    'windy': dict(emoji='💨',  label='Berangin',     grow_mult=1.1, reward_mult=0.9,  pest_chance=0.08),
    'stormy':dict(emoji='⛈️', label='Badai',        grow_mult=0.70,reward_mult=0.80, pest_chance=0.15),
    'golden':dict(emoji='🌟',  label='Cuaca Emas ✨',grow_mult=1.3, reward_mult=1.5,  pest_chance=0.0),
}

FERTILIZERS = {
    'basic_fert': dict(emoji='🌱', name='Pupuk Dasar',  grow_boost=0.25, reward_boost=0.0,  price=30, cur='gold'),
    'super_fert': dict(emoji='⚡', name='Super Pupuk',  grow_boost=0.50, reward_boost=0.10, price=80, cur='gold'),
    'golden_fert':dict(emoji='✨', name='Pupuk Emas',   grow_boost=0.0,  reward_boost=0.30, price=20, cur='gems'),
    'mega_fert':  dict(emoji='💥', name='Mega Pupuk',   grow_boost=0.75, reward_boost=0.20, price=30, cur='gems'),
}

UPGRADES = {
    'soil_1':  dict(cost=500,  ct='gold', lv=1, effect='grow_speed',  val=0.05, label='🌱 Tanah Dasar I',       desc='+5% grow speed'),
    'soil_2':  dict(cost=1500, ct='gold', lv=3, effect='grow_speed',  val=0.10, label='🌱 Tanah Subur II',      desc='+10% grow speed'),
    'soil_3':  dict(cost=4000, ct='gold', lv=6, effect='grow_speed',  val=0.20, label='🌿 Tanah Premium III',   desc='+20% grow speed'),
    'barn_1':  dict(cost=800,  ct='gold', lv=2, effect='inv_cap',     val=20,   label='🏚️ Gudang I',            desc='+20 inv cap'),
    'barn_2':  dict(cost=2500, ct='gold', lv=5, effect='inv_cap',     val=50,   label='🏠 Gudang II',           desc='+50 inv cap'),
    'water_1': dict(cost=600,  ct='gold', lv=2, effect='pest_resist', val=0.10, label='💧 Irigasi I',           desc='+10% pest resist'),
    'water_2': dict(cost=2000, ct='gold', lv=4, effect='pest_resist', val=0.25, label='🚿 Irigasi Auto II',     desc='+25% pest resist'),
    'tool_1':  dict(cost=1000, ct='gold', lv=3, effect='xp_bonus',    val=0.10, label='🔧 Cangkul Besi I',      desc='+10% XP'),
    'tool_2':  dict(cost=3000, ct='gold', lv=7, effect='xp_bonus',    val=0.25, label='⚙️ Mesin Panen II',      desc='+25% XP'),
    'energy_1':dict(cost=700,  ct='gold', lv=2, effect='energy_max',  val=10,   label='⚡ Baterai I',           desc='+10 energy max'),
    'energy_2':dict(cost=2200, ct='gold', lv=5, effect='energy_max',  val=25,   label='🔋 Generator II',        desc='+25 energy max'),
    'combo_1': dict(cost=1200, ct='gold', lv=3, effect='combo_bonus', val=0.05, label='🔗 Combo Synergy I',     desc='+5% combo bonus'),
    'fvt_1':   dict(cost=2000, ct='gold', lv=5, effect='fvt_bonus',   val=0.10, label='💰 FVT Amplifier I',    desc='+10% FVT semua panen'),
}

SHOP = {
    'basic_fert':  dict(cat='tool',   emoji='🌱', name='Pupuk Dasar',     desc='Percepat 25% 1 lahan',    price=30,  cur='gold'),
    'super_fert':  dict(cat='tool',   emoji='⚡', name='Super Pupuk',     desc='Percepat 50% + 10% reward',price=80, cur='gold'),
    'pesticide':   dict(cat='tool',   emoji='🧴', name='Pestisida',       desc='Basmi hama 1 lahan',      price=40,  cur='gold'),
    'mass_pest':   dict(cat='tool',   emoji='💊', name='Pestisida Massal',desc='Basmi semua hama',        price=15,  cur='gems'),
    'golden_fert': dict(cat='tool',   emoji='✨', name='Pupuk Emas',      desc='+30% reward panen',       price=20,  cur='gems'),
    'mega_fert':   dict(cat='tool',   emoji='💥', name='Mega Pupuk',      desc='Percepat 75% + 20% reward',price=30, cur='gems'),
    'energy_pot':  dict(cat='tool',   emoji='🧃', name='Energy Potion',   desc='+30 energy',              price=50,  cur='gold'),
    'plot_key':    dict(cat='land',   emoji='🔑', name='Kunci Lahan',     desc='Buka 1 lahan baru',       price=500, cur='gold'),
    'seed_wheat5': dict(cat='seed',   emoji='🌾', name='Benih Gandum x5', desc='5 benih gandum',          price=40,  cur='gold'),
    'seed_corn3':  dict(cat='seed',   emoji='🌽', name='Benih Jagung x3', desc='3 benih jagung · Lv2',   price=120, cur='gold'),
    'seed_straw':  dict(cat='seed',   emoji='🍓', name='Benih Stroberi',  desc='1 benih premium · Lv3',   price=12,  cur='gems'),
}

ENERGY_COST = {'plant':5, 'harvest':2}
ENERGY_REGEN_PER_MIN = 2

def xp_for_level(lv: int) -> int:
    return int(1000 * (1.45 ** (lv - 1)))

def current_weather() -> str:
    """Deterministic: changes every 4 hours. 4% chance of golden."""
    block = int(time.time() // (4 * 3600))
    r = random.Random(block)
    if r.random() < 0.04:
        return 'golden'
    pool = ['sunny','sunny','rainy','rainy','windy','stormy','sunny','rainy','windy']
    return r.choice(pool)

def next_weather_change_secs() -> int:
    block = int(time.time() // (4 * 3600))
    return int((block + 1) * 4 * 3600 - time.time())

# ─── DATABASE ─────────────────────────────────────────────────────────────────

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=12000")
    try:
        yield conn; conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

@contextmanager
def db_write():
    """Exclusive write — BEGIN IMMEDIATE."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=12000")
    try:
        conn.execute("BEGIN IMMEDIATE"); yield conn; conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK"); raise
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS players (
            user_id         INTEGER PRIMARY KEY,
            username        TEXT    DEFAULT '',
            first_name      TEXT    DEFAULT 'Farmer',
            gold            INTEGER DEFAULT 500,
            gems            INTEGER DEFAULT 20,
            fvt             INTEGER DEFAULT 0,
            level           INTEGER DEFAULT 1,
            xp              INTEGER DEFAULT 0,
            xp_max          INTEGER DEFAULT 1000,
            energy          INTEGER DEFAULT 50,
            energy_max      INTEGER DEFAULT 50,
            energy_ts       REAL    DEFAULT 0,
            total_harvest   INTEGER DEFAULT 0,
            total_combo     INTEGER DEFAULT 0,
            streak          INTEGER DEFAULT 0,
            last_streak_day TEXT    DEFAULT '',
            quest_harvest   INTEGER DEFAULT 0,
            quest_plant     INTEGER DEFAULT 0,
            quest_sell      INTEGER DEFAULT 0,
            quest_done      INTEGER DEFAULT 0,
            quest_date      TEXT    DEFAULT '',
            boost_mult      REAL    DEFAULT 1.0,
            boost_until     REAL    DEFAULT 0,
            wallet_addr     TEXT    DEFAULT '',
            is_banned       INTEGER DEFAULT 0,
            ban_reason      TEXT    DEFAULT '',
            suspicion       INTEGER DEFAULT 0,
            ref_count       INTEGER DEFAULT 0,
            ref_by          INTEGER DEFAULT 0,
            created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
            last_seen       TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS plots (
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
            fertilized   TEXT    DEFAULT '',
            locked       INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, slot),
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
            seller_id  INTEGER NOT NULL,
            item_id    TEXT    NOT NULL,
            item_name  TEXT    DEFAULT '',
            emoji      TEXT    DEFAULT '📦',
            item_type  TEXT    DEFAULT 'crop',
            quantity   INTEGER DEFAULT 1,
            price_fvt  INTEGER NOT NULL,
            fee_fvt    INTEGER DEFAULT 0,
            status     TEXT    DEFAULT 'active',
            buyer_id   INTEGER DEFAULT 0,
            listed_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
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
            nonce        TEXT    UNIQUE,
            gold_earned  INTEGER DEFAULT 0,
            fvt_earned   INTEGER DEFAULT 0,
            xp_earned    INTEGER DEFAULT 0,
            combo_bonus  INTEGER DEFAULT 0,
            weather_key  TEXT    DEFAULT 'sunny',
            has_pest     INTEGER DEFAULT 0,
            fertilized   TEXT    DEFAULT '',
            harvested_at REAL    DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS rate_limits (
            user_id      INTEGER NOT NULL,
            action       TEXT    NOT NULL,
            count        INTEGER DEFAULT 0,
            window_start REAL    DEFAULT 0,
            PRIMARY KEY(user_id, action)
        );

        CREATE TABLE IF NOT EXISTS anticheat_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            code    TEXT,
            reason  TEXT,
            extra   TEXT DEFAULT '',
            ts      REAL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS broadcast_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id   INTEGER,
            message    TEXT,
            sent_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
    log.info("✅ DB v5 initialized (WAL)")

# ─── DB HELPERS ──────────────────────────────────────────────────────────────

def player_get(user_id: int) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    return dict(r) if r else None

def player_upsert(user_id: int, username: str, first_name: str):
    with db() as c:
        c.execute("""INSERT INTO players(user_id,username,first_name)
                     VALUES(?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET
                        username=excluded.username,
                        first_name=excluded.first_name,
                        last_seen=CURRENT_TIMESTAMP""",
                  (user_id, username or '', first_name or 'Farmer'))
        # Create 16 plots (6 unlocked by default)
        count = c.execute("SELECT COUNT(*) FROM plots WHERE user_id=?", (user_id,)).fetchone()[0]
        if count == 0:
            for i in range(16):
                c.execute("INSERT OR IGNORE INTO plots(user_id,slot,locked) VALUES(?,?,?)",
                           (user_id, i, 0 if i < 6 else 1))

def inv_add(c, uid: int, item_id: str, emoji: str, name: str, itype: str, qty: int, fvt_val: int = 0):
    c.execute("""INSERT INTO inventory(user_id,item_id,emoji,item_name,item_type,quantity,fvt_value)
                 VALUES(?,?,?,?,?,?,?)
                 ON CONFLICT(user_id,item_id) DO UPDATE SET
                    quantity=quantity+excluded.quantity""",
              (uid, item_id, emoji, name, itype, qty, fvt_val))

def inv_take(c, uid: int, item_id: str, qty: int) -> bool:
    r = c.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?", (uid, item_id)).fetchone()
    if not r or r['quantity'] < qty:
        return False
    new_qty = r['quantity'] - qty
    if new_qty == 0:
        c.execute("DELETE FROM inventory WHERE user_id=? AND item_id=?", (uid, item_id))
    else:
        c.execute("UPDATE inventory SET quantity=? WHERE user_id=? AND item_id=?", (new_qty, uid, item_id))
    return True

def get_upgrades_owned(uid: int) -> list:
    with db() as c:
        return [r['upgrade_id'] for r in c.execute("SELECT upgrade_id FROM upgrades WHERE user_id=?", (uid,)).fetchall()]

def calc_effects(uid: int) -> dict:
    eff = dict(grow_speed=0.0, inv_cap=0, pest_resist=0.0, xp_bonus=0.0, energy_max=0, combo_bonus=0.0, fvt_bonus=0.0)
    for uid_upg in get_upgrades_owned(uid):
        u = UPGRADES.get(uid_upg, {})
        e = u.get('effect','')
        if e in eff:
            eff[e] += u.get('val', 0)
    return eff

def regen_energy(c, p: dict) -> int:
    now = time.time()
    elapsed_min = (now - (p['energy_ts'] or now)) / 60
    regen = int(elapsed_min * ENERGY_REGEN_PER_MIN)
    if regen > 0:
        new_e = min(p['energy'] + regen, p['energy_max'])
        c.execute("UPDATE players SET energy=?, energy_ts=? WHERE user_id=?", (new_e, now, p['user_id']))
        p['energy'] = new_e
    return p['energy']

def rate_check(uid: int, action: str, max_n: int, window_s: int) -> bool:
    now = time.time()
    with db() as c:
        r = c.execute("SELECT count, window_start FROM rate_limits WHERE user_id=? AND action=?", (uid, action)).fetchone()
        if not r:
            c.execute("INSERT INTO rate_limits VALUES(?,?,1,?)", (uid, action, now))
            return True
        cnt, ws = r['count'], r['window_start']
        if now - ws > window_s:
            c.execute("UPDATE rate_limits SET count=1, window_start=? WHERE user_id=? AND action=?", (now, uid, action))
            return True
        if cnt >= max_n:
            return False
        c.execute("UPDATE rate_limits SET count=count+1 WHERE user_id=? AND action=?", (uid, action))
    return True

def xp_add(c, uid: int, gain: int) -> dict:
    r = c.execute("SELECT xp, xp_max, level FROM players WHERE user_id=?", (uid,)).fetchone()
    if not r: return {}
    xp, xp_max, lv = r['xp'] + gain, r['xp_max'], r['level']
    leveled, gems_bonus = False, 0
    while xp >= xp_max:
        xp -= xp_max; lv += 1
        xp_max = xp_for_level(lv + 1)
        leveled = True; gems_bonus += 10 + lv
    c.execute("UPDATE players SET xp=?, xp_max=?, level=?, gems=gems+? WHERE user_id=?",
              (xp, xp_max, lv, gems_bonus, uid))
    return dict(leveled=leveled, new_level=lv, xp=xp, xp_max=xp_max, gems_bonus=gems_bonus)

def flag(uid: int, code: str, reason: str, extra: str = ''):
    with db() as c:
        c.execute("INSERT INTO anticheat_log(user_id,code,reason,extra) VALUES(?,?,?,?)", (uid, code, reason, extra))
        c.execute("UPDATE players SET suspicion=suspicion+15 WHERE user_id=?", (uid,))
        score = c.execute("SELECT suspicion FROM players WHERE user_id=?", (uid,)).fetchone()
    log.warning(f"[AC] {uid} | {code}: {reason}")
    if score and score['suspicion'] >= 75:
        with db() as c:
            c.execute("UPDATE players SET is_banned=1, ban_reason=? WHERE user_id=?", (code, uid))

# ─── REWARD CALCULATOR ───────────────────────────────────────────────────────

def calc_reward(uid: int, plot: dict, all_active: list) -> dict:
    """Server-side authoritative reward calculation."""
    crop = CROPS.get(plot['crop_id'])
    if not crop:
        return dict(gold=0, fvt=0, xp=0, combo_bonus=0, combo_label='', weather=current_weather())

    wk  = current_weather()
    w   = WEATHER[wk]
    eff = calc_effects(uid)

    gold = plot['base_reward']
    fvt  = plot['base_fvt']
    xp   = plot['base_xp']

    # Fertilizer reward boost
    if plot['fertilized']:
        fert = FERTILIZERS.get(plot['fertilized'], {})
        rb = fert.get('reward_boost', 0)
        if rb > 0:
            gold = int(gold * (1 + rb))
            fvt  = int(fvt  * (1 + rb))

    # Weather
    gold = int(gold * w['reward_mult'])
    fvt  = int(fvt  * w['reward_mult'])

    # Rarity multiplier
    rm = RARITY_MULT.get(crop['rarity'], 1.0)
    gold = int(gold * rm)
    fvt  = int(fvt  * rm)

    # FVT upgrade bonus
    if eff['fvt_bonus'] > 0:
        fvt = int(fvt * (1 + eff['fvt_bonus']))

    # XP upgrade bonus
    xp = int(xp * (1 + eff['xp_bonus']))

    # Combo bonus: 3+ same tag among all active plots
    combo_tag   = crop.get('combo','')
    combo_count = sum(1 for p in all_active if p['crop_id'] and
                      CROPS.get(p['crop_id'], {}).get('combo') == combo_tag) if combo_tag else 0
    combo_bonus, combo_label = 0, ''
    if combo_count >= 3 and combo_tag in COMBO_BONUS:
        cb   = COMBO_BONUS[combo_tag]
        cb_mod = 1.0 + eff.get('combo_bonus', 0)
        cb_gold = int(gold * cb['gold_pct'] / 100 * cb_mod)
        cb_fvt  = int(fvt  * cb['fvt_pct']  / 100 * cb_mod)
        gold += cb_gold; fvt += cb_fvt
        combo_bonus = cb_gold; combo_label = cb['label']

    # Pest penalty: -50%
    if plot['has_pest']:
        gold //= 2; fvt //= 2

    # Boost
    p_row = None
    with db() as c:
        p_row = c.execute("SELECT boost_mult, boost_until FROM players WHERE user_id=?", (uid,)).fetchone()
    if p_row and p_row['boost_until'] > time.time():
        gold = int(gold * p_row['boost_mult'])
        fvt  = int(fvt  * p_row['boost_mult'])

    return dict(gold=gold, fvt=fvt, xp=xp, combo_bonus=combo_bonus,
                combo_label=combo_label, weather=wk,
                w_emoji=w['emoji'], w_label=w['label'])

# ─── STATE BUILDER ────────────────────────────────────────────────────────────

def build_state(uid: int) -> dict:
    p = player_get(uid)
    if not p: return {'type':'ERROR','msg':'Player not found'}
    wk = current_weather()
    w  = WEATHER[wk]
    with db() as c:
        plots  = [dict(r) for r in c.execute("SELECT * FROM plots WHERE user_id=? ORDER BY slot", (uid,)).fetchall()]
        inv    = [dict(r) for r in c.execute("SELECT * FROM inventory WHERE user_id=? AND quantity>0", (uid,)).fetchall()]
        upgs   = [r['upgrade_id'] for r in c.execute("SELECT upgrade_id FROM upgrades WHERE user_id=?", (uid,)).fetchall()]
    eff = calc_effects(uid)
    next_wc = next_weather_change_secs()
    return {
        'type':          'STATE_SYNC',
        'gold':          p['gold'],
        'gems':          p['gems'],
        'fvt':           p['fvt'],
        'level':         p['level'],
        'xp':            p['xp'],
        'xpMax':         p['xp_max'],
        'energy':        p['energy'],
        'energyMax':     p['energy_max'],
        'totalHarvest':  p['total_harvest'],
        'totalCombo':    p['total_combo'],
        'streak':        p['streak'],
        'questHarvest':  p['quest_harvest'],
        'questPlant':    p['quest_plant'],
        'questSell':     p['quest_sell'],
        'questDone':     bool(p['quest_done']),
        'boostMult':     p['boost_mult'],
        'boostUntil':    p['boost_until'],
        'walletAddr':    p['wallet_addr'],
        'weather':       wk,
        'weatherEmoji':  w['emoji'],
        'weatherLabel':  w['label'],
        'weatherGrowMult':  w['grow_mult'],
        'weatherRwdMult':   w['reward_mult'],
        'nextWeatherSecs':  next_wc,
        'effects':       eff,
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
        'inventory':      inv,
        'ownedUpgrades':  upgs,
    }

# ─── WEBAPP HANDLER ──────────────────────────────────────────────────────────

async def handle_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    raw  = getattr(update.effective_message.web_app_data, 'data', None)
    if not raw: return

    try:
        payload = json.loads(raw)
    except Exception:
        flag(user.id, 'INVALID_JSON', 'bad payload'); return

    action = payload.get('action', '')
    data   = payload.get('data', {})

    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    if p['is_banned']:
        return

    async def reply(obj: dict):
        try: await ctx.bot.send_message(user.id, json.dumps(obj))
        except Exception: pass

    # ── GET_STATE ──────────────────────────────────────────────────────────
    if action == 'get_state':
        await reply(build_state(user.id))

    # ── PLANT ──────────────────────────────────────────────────────────────
    elif action == 'plant':
        crop_id = data.get('cropId','')
        slot    = int(data.get('slot', -1))
        if crop_id not in CROPS or slot < 0: return
        cd = CROPS[crop_id]

        with db_write() as c:
            p = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            # Level gate
            if cd['lv'] > p['level']:
                await reply({'type':'ERROR','msg':f'❌ Butuh Level {cd["lv"]}!'}); return
            # Plot check
            plot = c.execute("SELECT * FROM plots WHERE user_id=? AND slot=?", (user.id, slot)).fetchone()
            if not plot or plot['locked']:
                await reply({'type':'ERROR','msg':'❌ Lahan terkunci!'}); return
            if plot['crop_id']:
                await reply({'type':'ERROR','msg':'❌ Lahan sudah ada tanaman!'}); return
            # Energy regen + check
            regen_energy(c, p)
            if p['energy'] < ENERGY_COST['plant']:
                await reply({'type':'ERROR','msg':f'⚡ Energy kurang! ({p["energy"]}/{p["energy_max"]})\nTunggu regen atau minum Energy Potion.'}); return
            # Cost check
            if cd['ct'] == 'gold' and p['gold'] < cd['cost']:
                await reply({'type':'ERROR','msg':'❌ Koin tidak cukup!'}); return
            if cd['ct'] == 'gems' and p['gems'] < cd['cost']:
                await reply({'type':'ERROR','msg':'❌ Gems tidak cukup!'}); return
            # Apply weather & upgrade to grow time
            eff = calc_effects(user.id)
            wk  = current_weather()
            gm  = WEATHER[wk]['grow_mult']
            actual_grow = max(30, int(cd['grow'] * gm * (1 - eff['grow_speed'])))
            # Pest chance
            pest_chance = WEATHER[wk]['pest_chance'] * (1 - eff['pest_resist'])
            has_pest = random.random() < max(0, pest_chance)
            nonce = f"{user.id}_{slot}_{int(time.time()*1000)}_{random.randint(1000,9999)}"
            # Deduct
            if cd['ct'] == 'gold':
                c.execute("UPDATE players SET gold=gold-?, energy=energy-?, quest_plant=quest_plant+1 WHERE user_id=?",
                           (cd['cost'], ENERGY_COST['plant'], user.id))
            else:
                c.execute("UPDATE players SET gems=gems-?, energy=energy-?, quest_plant=quest_plant+1 WHERE user_id=?",
                           (cd['cost'], ENERGY_COST['plant'], user.id))
            c.execute("""UPDATE plots SET crop_id=?, planted_at=?, grow_time=?, base_reward=?,
                base_xp=?, base_fvt=?, nonce=?, has_pest=?, fertilized='', locked=0
                WHERE user_id=? AND slot=?""",
                (crop_id, time.time(), actual_grow, cd['reward'], cd['xp'], cd['fvt'],
                 nonce, 1 if has_pest else 0, user.id, slot))
        state = build_state(user.id)
        pest_warn = ' ⚠️ Hama terdeteksi! Gunakan pestisida.' if has_pest else ''
        await reply({'type':'PLANT_OK','slot':slot,'cropId':crop_id,'growTime':actual_grow,
                     'hasPest':has_pest,'plots':state['plots'],
                     'gold':state['gold'],'gems':state['gems'],'energy':state['energy'],
                     'msg':f'🌱 {cd["name"]} ditanam!{pest_warn}'})

    # ── FERTILIZE ─────────────────────────────────────────────────────────
    elif action == 'fertilize':
        slot    = int(data.get('slot',-1))
        fert_id = data.get('fertId','')
        if fert_id not in FERTILIZERS or slot < 0: return
        fert = FERTILIZERS[fert_id]
        with db_write() as c:
            plot = c.execute("SELECT * FROM plots WHERE user_id=? AND slot=?", (user.id, slot)).fetchone()
            if not plot or not plot['crop_id']:
                await reply({'type':'ERROR','msg':'❌ Tidak ada tanaman di lahan ini!'}); return
            if plot['fertilized']:
                await reply({'type':'ERROR','msg':'❌ Lahan sudah difertilisasi!'}); return
            # Check inventory
            if not inv_take(c, user.id, fert_id, 1):
                await reply({'type':'ERROR','msg':f'❌ {fert["name"]} tidak ada di inventory!'}); return
            # Apply grow boost
            if fert['grow_boost'] > 0:
                new_grow = max(10, int(plot['grow_time'] * (1 - fert['grow_boost'])))
                c.execute("UPDATE plots SET fertilized=?, grow_time=? WHERE user_id=? AND slot=?",
                           (fert_id, new_grow, user.id, slot))
            else:
                c.execute("UPDATE plots SET fertilized=? WHERE user_id=? AND slot=?", (fert_id, user.id, slot))
        state = build_state(user.id)
        await reply({'type':'FERT_OK','slot':slot,'plots':state['plots'],'inventory':state['inventory'],
                     'msg':f'🌱 {fert["name"]} diterapkan!'})

    # ── PESTICIDE ──────────────────────────────────────────────────────────
    elif action == 'pesticide':
        slot     = int(data.get('slot',-1))
        mass     = data.get('mass', False)
        pest_id  = 'mass_pest' if mass else 'pesticide'
        with db_write() as c:
            if not inv_take(c, user.id, pest_id, 1):
                await reply({'type':'ERROR','msg':'❌ Pestisida tidak ada di inventory!'}); return
            if mass:
                c.execute("UPDATE plots SET has_pest=0 WHERE user_id=?", (user.id,))
            else:
                c.execute("UPDATE plots SET has_pest=0 WHERE user_id=? AND slot=?", (user.id, slot))
        state = build_state(user.id)
        await reply({'type':'PEST_OK','plots':state['plots'],'inventory':state['inventory'],
                     'msg':'✅ Hama berhasil dibasmi!'})

    # ── HARVEST ────────────────────────────────────────────────────────────
    elif action == 'harvest':
        slot  = int(data.get('slot',-1))
        nonce = data.get('nonce','')

        with db_write() as c:
            plot = c.execute("SELECT * FROM plots WHERE user_id=? AND slot=?", (user.id, slot)).fetchone()
            if not plot or not plot['crop_id']:
                await reply({'type':'ERROR','msg':'❌ Tidak ada tanaman!'}); return

            # ── Server-side timing validation ──────────────────────────────
            now     = time.time()
            elapsed = now - plot['planted_at']
            grow    = plot['grow_time']
            if elapsed < grow - 5:  # 5s server tolerance
                flag(user.id, 'EARLY_HARVEST', f"slot={slot} elapsed={elapsed:.1f} grow={grow}")
                await reply({'type':'ERROR','msg':'❌ Tanaman belum siap panen!'}); return

            # ── Replay / nonce protection ───────────────────────────────────
            dup = c.execute("SELECT 1 FROM harvest_log WHERE nonce=? AND user_id=?", (plot['nonce'], user.id)).fetchone()
            if dup:
                flag(user.id, 'REPLAY', f"nonce={plot['nonce']}")
                await reply({'type':'ERROR','msg':'❌ Sudah dipanen!'}); return

            # ── Rate limit ──────────────────────────────────────────────────
            if not rate_check(user.id, 'harvest', 20, 60):
                await reply({'type':'ERROR','msg':'⏳ Terlalu cepat! Tunggu sebentar.'}); return

            # ── Energy check ────────────────────────────────────────────────
            p2 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            regen_energy(c, p2)
            if p2['energy'] < ENERGY_COST['harvest']:
                await reply({'type':'ERROR','msg':f'⚡ Energy habis! ({p2["energy"]}/{p2["energy_max"]})\nTunggu regen.'}); return

            # ── Calculate reward (server-authoritative) ─────────────────────
            all_plots = [dict(r) for r in c.execute(
                "SELECT * FROM plots WHERE user_id=? AND crop_id!=''", (user.id,)).fetchall()]
            rewards = calc_reward(user.id, dict(plot), all_plots)

            crop_def = CROPS.get(plot['crop_id'], {})

            # Add to inventory (harvest result)
            inv_add(c, user.id, plot['crop_id'], crop_def.get('emoji','🌾'),
                    crop_def.get('name', plot['crop_id']), 'crop', 1, crop_def.get('fvt',0))

            # Update player gold/fvt/energy/harvest count
            c.execute("""UPDATE players SET gold=gold+?, fvt=fvt+?,
                energy=energy-?, total_harvest=total_harvest+?,
                total_combo=total_combo+?,
                quest_harvest=quest_harvest+1 WHERE user_id=?""",
                (rewards['gold'], rewards['fvt'], ENERGY_COST['harvest'],
                 1, 1 if rewards['combo_bonus']>0 else 0, user.id))

            # XP
            lv_info = xp_add(c, user.id, rewards['xp'])

            # Daily quest & streak
            p3 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            today = date.today().isoformat()
            if p3['quest_date'] != today:
                c.execute("""UPDATE players SET quest_harvest=1,quest_plant=0,quest_sell=0,
                    quest_done=0,quest_date=? WHERE user_id=?""", (today, user.id))
            elif not p3['quest_done'] and p3['quest_harvest'] >= 5 and p3['quest_plant'] >= 3:
                c.execute("""UPDATE players SET gold=gold+300, gems=gems+20, fvt=fvt+100,
                    quest_done=1 WHERE user_id=?""", (user.id,))

            # Streak
            if p3['last_streak_day'] != today:
                yesterday = (date.today() - timedelta(days=1)).isoformat()
                new_streak = p3['streak'] + 1 if p3['last_streak_day'] == yesterday else 1
                streak_bonus = new_streak * 25 if new_streak % 7 == 0 else 0
                c.execute("UPDATE players SET streak=?, last_streak_day=?, gold=gold+? WHERE user_id=?",
                           (new_streak, today, streak_bonus, user.id))

            # Clear plot
            c.execute("""UPDATE plots SET crop_id='',planted_at=0,grow_time=0,
                base_reward=0,base_xp=0,base_fvt=0,nonce='',has_pest=0,fertilized=''
                WHERE user_id=? AND slot=?""", (user.id, slot))

            # Log
            c.execute("""INSERT INTO harvest_log(user_id,slot,crop_id,nonce,gold_earned,
                fvt_earned,xp_earned,combo_bonus,weather_key,has_pest,fertilized,harvested_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user.id, slot, plot['crop_id'], plot['nonce'], rewards['gold'], rewards['fvt'],
                 rewards['xp'], rewards['combo_bonus'], rewards['weather'],
                 plot['has_pest'], plot['fertilized'], now))

        state = build_state(user.id)
        await reply({
            'type':'HARVEST_OK', 'slot':slot,
            'gold':rewards['gold'], 'fvt':rewards['fvt'], 'xp':rewards['xp'],
            'comboBonus':rewards['combo_bonus'], 'comboLabel':rewards['combo_label'],
            'weather':rewards['weather'], 'weatherEmoji':rewards['w_emoji'],
            'leveled':lv_info.get('leveled',False), 'newLevel':lv_info.get('new_level',p['level']),
            'gemsBonus':lv_info.get('gems_bonus',0),
            'plots':state['plots'], 'goldTotal':state['gold'], 'fvtTotal':state['fvt'],
            'energy':state['energy'], 'inventory':state['inventory'],
        })

    # ── HARVEST ALL ────────────────────────────────────────────────────────
    elif action == 'harvest_all':
        harvested, t_gold, t_fvt, t_xp = [], 0, 0, 0
        with db_write() as c:
            p2 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            regen_energy(c, p2)
            energy = p2['energy']
            plots  = [dict(r) for r in c.execute(
                "SELECT * FROM plots WHERE user_id=? AND crop_id!='' AND locked=0", (user.id,)).fetchall()]
            all_active = list(plots)
            now = time.time()

            for pl in plots:
                if energy < ENERGY_COST['harvest']: break
                elapsed = now - pl['planted_at']
                if elapsed < pl['grow_time'] - 5: continue
                dup = c.execute("SELECT 1 FROM harvest_log WHERE nonce=? AND user_id=?", (pl['nonce'], user.id)).fetchone()
                if dup: continue

                rw = calc_reward(user.id, pl, all_active)
                crop_def = CROPS.get(pl['crop_id'], {})
                inv_add(c, user.id, pl['crop_id'], crop_def.get('emoji','🌾'),
                        crop_def.get('name',pl['crop_id']), 'crop', 1, crop_def.get('fvt',0))
                t_gold += rw['gold']; t_fvt += rw['fvt']; t_xp += rw['xp']
                energy -= ENERGY_COST['harvest']
                harvested.append(pl['slot'])
                c.execute("""UPDATE plots SET crop_id='',planted_at=0,grow_time=0,
                    base_reward=0,base_xp=0,base_fvt=0,nonce='',has_pest=0,fertilized=''
                    WHERE user_id=? AND slot=?""", (user.id, pl['slot']))
                c.execute("""INSERT INTO harvest_log(user_id,slot,crop_id,nonce,gold_earned,
                    fvt_earned,xp_earned,combo_bonus,weather_key,has_pest,fertilized,harvested_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (user.id, pl['slot'], pl['crop_id'], pl['nonce'], rw['gold'], rw['fvt'],
                     rw['xp'], rw['combo_bonus'], rw['weather'], pl['has_pest'], pl['fertilized'], now))

            if harvested:
                c.execute("""UPDATE players SET gold=gold+?,fvt=fvt+?,
                    total_harvest=total_harvest+?,energy=?,quest_harvest=quest_harvest+?
                    WHERE user_id=?""",
                    (t_gold, t_fvt, len(harvested), energy, len(harvested), user.id))
                lv_info = xp_add(c, user.id, t_xp)
                # Quest check
                p3 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
                today = date.today().isoformat()
                if p3['quest_date'] == today and not p3['quest_done'] and \
                   p3['quest_harvest'] >= 5 and p3['quest_plant'] >= 3:
                    c.execute("UPDATE players SET gold=gold+300,gems=gems+20,fvt=fvt+100,quest_done=1 WHERE user_id=?", (user.id,))

        state = build_state(user.id)
        await reply({'type':'HARVEST_ALL_OK','harvested':harvested,
                     'totalGold':t_gold,'totalFvt':t_fvt,'totalXp':t_xp,
                     'plots':state['plots'],'goldTotal':state['gold'],'fvtTotal':state['fvt'],
                     'energy':state['energy'],'inventory':state['inventory']})

    # ── UNLOCK PLOT ────────────────────────────────────────────────────────
    elif action == 'unlock_plot':
        slot = int(data.get('slot',-1))
        if slot < 0: return
        UNLOCK_COSTS = {6:200, 7:400, 8:600, 9:900, 10:1200,
                        11:1600, 12:2000, 13:2500, 14:3200, 15:4000}
        cost = UNLOCK_COSTS.get(slot, 9999)
        with db_write() as c:
            p2 = dict(c.execute("SELECT gold FROM players WHERE user_id=?", (user.id,)).fetchone())
            if p2['gold'] < cost:
                await reply({'type':'ERROR','msg':f'❌ Butuh {cost:,}🪙'}); return
            pl = c.execute("SELECT locked FROM plots WHERE user_id=? AND slot=?", (user.id, slot)).fetchone()
            if not pl or not pl['locked']:
                await reply({'type':'ERROR','msg':'❌ Lahan sudah terbuka!'}); return
            c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (cost, user.id))
            c.execute("UPDATE plots SET locked=0 WHERE user_id=? AND slot=?", (user.id, slot))
        state = build_state(user.id)
        await reply({'type':'UNLOCK_OK','slot':slot,'plots':state['plots'],'gold':state['gold']})

    # ── BUY SHOP ──────────────────────────────────────────────────────────
    elif action == 'buy_shop':
        item_id = data.get('itemId','')
        if item_id not in SHOP: return
        it = SHOP[item_id]
        with db_write() as c:
            p2 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            pr = it['price']
            if it['cur'] == 'gold':
                if p2['gold'] < pr:
                    await reply({'type':'ERROR','msg':'❌ Koin tidak cukup!'}); return
                c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (pr, user.id))
            elif it['cur'] == 'gems':
                if p2['gems'] < pr:
                    await reply({'type':'ERROR','msg':'❌ Gems tidak cukup!'}); return
                c.execute("UPDATE players SET gems=gems-? WHERE user_id=?", (pr, user.id))

            if item_id == 'energy_pot':
                p3 = dict(c.execute("SELECT energy, energy_max FROM players WHERE user_id=?", (user.id,)).fetchone())
                c.execute("UPDATE players SET energy=? WHERE user_id=?",
                           (min(p3['energy']+30, p3['energy_max']), user.id))
            elif item_id == 'plot_key':
                locked = c.execute("SELECT slot FROM plots WHERE user_id=? AND locked=1 ORDER BY slot LIMIT 1", (user.id,)).fetchone()
                if locked:
                    c.execute("UPDATE plots SET locked=0 WHERE user_id=? AND slot=?", (user.id, locked['slot']))
            elif item_id == 'pesticide':
                inv_add(c, user.id, 'pesticide', '🧴', 'Pestisida', 'tool', 1)
            elif item_id == 'mass_pest':
                inv_add(c, user.id, 'mass_pest', '💊', 'Pestisida Massal', 'tool', 1)
            elif item_id.endswith('_fert'):
                f = FERTILIZERS.get(item_id, it)
                inv_add(c, user.id, item_id, f.get('emoji',it['emoji']), f.get('name',it['name']), 'tool', 1)
            elif item_id == 'seed_wheat5':
                inv_add(c, user.id, 'wheat', CROPS['wheat']['emoji'], CROPS['wheat']['name'], 'seed', 5, CROPS['wheat']['fvt'])
            elif item_id == 'seed_corn3':
                inv_add(c, user.id, 'corn', CROPS['corn']['emoji'], CROPS['corn']['name'], 'seed', 3, CROPS['corn']['fvt'])
            elif item_id == 'seed_straw':
                inv_add(c, user.id, 'strawberry', CROPS['strawberry']['emoji'], CROPS['strawberry']['name'], 'seed', 1, CROPS['strawberry']['fvt'])
            else:
                inv_add(c, user.id, item_id, it['emoji'], it['name'], 'tool', 1)

        state = build_state(user.id)
        await reply({'type':'BUY_OK','itemId':item_id,'msg':f'✅ {it["name"]} dibeli!',
                     'gold':state['gold'],'gems':state['gems'],'energy':state['energy'],
                     'plots':state['plots'],'inventory':state['inventory']})

    # ── BUY UPGRADE ───────────────────────────────────────────────────────
    elif action == 'buy_upgrade':
        upg_id = data.get('upgradeId','')
        if upg_id not in UPGRADES: return
        u = UPGRADES[upg_id]
        with db_write() as c:
            already = c.execute("SELECT 1 FROM upgrades WHERE user_id=? AND upgrade_id=?", (user.id, upg_id)).fetchone()
            if already:
                await reply({'type':'ERROR','msg':'❌ Upgrade sudah dimiliki!'}); return
            p2 = dict(c.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone())
            if p2['level'] < u['lv']:
                await reply({'type':'ERROR','msg':f'❌ Butuh Level {u["lv"]}!'}); return
            if p2['gold'] < u['cost']:
                await reply({'type':'ERROR','msg':'❌ Koin tidak cukup!'}); return
            c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (u['cost'], user.id))
            if u['effect'] == 'energy_max':
                c.execute("UPDATE players SET energy_max=energy_max+? WHERE user_id=?", (int(u['val']), user.id))
            c.execute("INSERT OR IGNORE INTO upgrades(user_id,upgrade_id) VALUES(?,?)", (user.id, upg_id))
        state = build_state(user.id)
        await reply({'type':'UPGRADE_OK','upgradeId':upg_id,'msg':f'🔧 {u["label"]} berhasil!',
                     'gold':state['gold'],'effects':state['effects'],'energyMax':state['energyMax']})

    # ── MARKET: LIST ITEM ──────────────────────────────────────────────────
    elif action == 'market_list':
        item_id  = data.get('itemId','')
        quantity = max(1, int(data.get('quantity',1)))
        price    = int(data.get('price',0))
        if not item_id or price <= 0 or price > 10_000_000:
            await reply({'type':'ERROR','msg':'❌ Parameter tidak valid!'}); return
        if not rate_check(user.id, 'market_sell', 10, 3600):
            await reply({'type':'ERROR','msg':'⏳ Terlalu banyak listing. Coba lagi nanti.'}); return
        with db_write() as c:
            if not inv_take(c, user.id, item_id, quantity):
                await reply({'type':'ERROR','msg':'❌ Item tidak cukup di inventory!'}); return
            crop_def = CROPS.get(item_id, {})
            emoji    = crop_def.get('emoji','📦')
            name     = crop_def.get('name', item_id)
            fee      = int(price * MARKET_FEE)
            c.execute("""INSERT INTO market_listings
                (seller_id,item_id,item_name,emoji,item_type,quantity,price_fvt,fee_fvt)
                VALUES(?,?,?,?,?,?,?,?)""",
                (user.id, item_id, f"{name} x{quantity}", emoji, 'crop', quantity, price, fee))
        state = build_state(user.id)
        await reply({'type':'MARKET_LIST_OK','msg':'✅ Listing berhasil! Item dikurangi dari inventory.',
                     'inventory':state['inventory']})

    # ── MARKET: BUY ITEM ───────────────────────────────────────────────────
    elif action == 'market_buy':
        listing_id = int(data.get('listingId',0))
        with db_write() as c:
            lst = c.execute("SELECT * FROM market_listings WHERE id=? AND status='active'", (listing_id,)).fetchone()
            if not lst:
                await reply({'type':'ERROR','msg':'❌ Listing tidak ada atau sudah terjual!'}); return
            lst = dict(lst)
            if lst['seller_id'] == user.id:
                await reply({'type':'ERROR','msg':'❌ Tidak bisa beli listing sendiri!'}); return
            p2 = dict(c.execute("SELECT fvt FROM players WHERE user_id=?", (user.id,)).fetchone())
            if p2['fvt'] < lst['price_fvt']:
                await reply({'type':'ERROR','msg':f'❌ FVT kurang! (Kamu: {p2["fvt"]:,} / Harga: {lst["price_fvt"]:,})'}); return
            if not rate_check(user.id, 'market_buy', 20, 3600):
                await reply({'type':'ERROR','msg':'⏳ Terlalu banyak pembelian.'}); return

            net_to_seller = lst['price_fvt'] - lst['fee_fvt']
            # Buyer pays full price
            c.execute("UPDATE players SET fvt=fvt-? WHERE user_id=?", (lst['price_fvt'], user.id))
            # Seller receives net
            c.execute("UPDATE players SET fvt=fvt+?, quest_sell=quest_sell+1 WHERE user_id=?",
                       (net_to_seller, lst['seller_id']))
            # Transfer item to buyer inventory
            crop_def = CROPS.get(lst['item_id'], {})
            inv_add(c, user.id, lst['item_id'], lst['emoji'], crop_def.get('name',lst['item_id']),
                    lst['item_type'], lst['quantity'], crop_def.get('fvt',0))
            # Update listing
            c.execute("UPDATE market_listings SET status='sold', buyer_id=?, sold_at=CURRENT_TIMESTAMP WHERE id=?",
                       (user.id, listing_id))
            # History
            c.execute("""INSERT INTO market_history(listing_id,item_id,item_name,emoji,quantity,
                price_fvt,seller_id,buyer_id) VALUES(?,?,?,?,?,?,?,?)""",
                (listing_id, lst['item_id'], lst['item_name'], lst['emoji'],
                 lst['quantity'], lst['price_fvt'], lst['seller_id'], user.id))

        state = build_state(user.id)
        await reply({'type':'MARKET_BUY_OK','listingId':listing_id,
                     'msg':f'✅ {lst["item_name"]} dibeli!',
                     'fvtTotal':state['fvt'],'inventory':state['inventory']})
        # Notify seller
        try:
            await ctx.bot.send_message(lst['seller_id'],
                f"🎉 *Item terjual!*\n{lst['emoji']} *{lst['item_name']}*\n"
                f"💰 Kamu menerima: *{net_to_seller:,} FVT*", parse_mode="Markdown")
        except Exception: pass

    # ── MARKET: CANCEL LISTING ─────────────────────────────────────────────
    elif action == 'market_cancel':
        listing_id = int(data.get('listingId',0))
        with db_write() as c:
            lst = c.execute("SELECT * FROM market_listings WHERE id=? AND seller_id=? AND status='active'",
                             (listing_id, user.id)).fetchone()
            if not lst:
                await reply({'type':'ERROR','msg':'❌ Listing tidak ditemukan!'}); return
            lst = dict(lst)
            # Return item to inventory
            crop_def = CROPS.get(lst['item_id'], {})
            inv_add(c, user.id, lst['item_id'], lst['emoji'], crop_def.get('name',lst['item_id']),
                    lst['item_type'], lst['quantity'], crop_def.get('fvt',0))
            c.execute("UPDATE market_listings SET status='cancelled' WHERE id=?", (listing_id,))
        state = build_state(user.id)
        await reply({'type':'CANCEL_OK','listingId':listing_id,
                     'msg':'✅ Listing dibatalkan. Item dikembalikan ke inventory.',
                     'inventory':state['inventory']})

    # ── WALLET CONNECT ────────────────────────────────────────────────────
    elif action == 'wallet_connect':
        addr = data.get('addr','')
        if addr and re.match(r'^0x[0-9a-fA-F]{40}$', addr):
            with db() as c:
                c.execute("UPDATE players SET wallet_addr=? WHERE user_id=?", (addr, user.id))
            await reply({'type':'WALLET_OK','addr':addr})
        else:
            flag(user.id, 'INVALID_WALLET', f"addr={addr}")

    # ── ANTI-CHEAT: client-reported ────────────────────────────────────────
    elif action == 'anti_cheat_flag':
        flag(user.id, data.get('code','UNKNOWN'), f"[CLIENT] {data.get('reason','')}")

# ─── BOT COMMANDS ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    player_upsert(user.id, user.username or '', user.first_name)

    # Referral
    if ctx.args:
        ref = ctx.args[0]
        if ref.isdigit() and int(ref) != user.id:
            ref_id = int(ref)
            with db() as c:
                # Check if already referred
                already = c.execute("SELECT ref_by FROM players WHERE user_id=?", (user.id,)).fetchone()
                if already and already['ref_by'] == 0:
                    c.execute("UPDATE players SET gold=gold+200, gems=gems+5, fvt=fvt+100, ref_count=ref_count+1 WHERE user_id=?", (ref_id,))
                    c.execute("UPDATE players SET gold=gold+100, ref_by=? WHERE user_id=?", (ref_id, user.id))
                    try:
                        await ctx.bot.send_message(ref_id,
                            f"🎉 *{user.first_name}* bergabung via referralmu!\n+200🪙 +5💎 +100 FVT",
                            parse_mode="Markdown")
                    except Exception: pass

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Buka FarmVerse!", web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton("📊 Profil", callback_data="profile"),
         InlineKeyboardButton("🎒 Inventory", callback_data="inventory")],
        [InlineKeyboardButton("🌤️ Cuaca", callback_data="weather"),
         InlineKeyboardButton("🌾 Status Farm", callback_data="farm")],
        [InlineKeyboardButton("🔄 Market", callback_data="market"),
         InlineKeyboardButton("📋 Quest", callback_data="quest")],
        [InlineKeyboardButton("🔗 Referral", callback_data="referral"),
         InlineKeyboardButton("❓ Bantuan", callback_data="help")],
    ])
    await update.message.reply_text(
        f"🌾 *FarmVerse — Phase 1: Foundation*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👋 Halo, *{user.first_name}!*\n\n"
        f"🎮 *Core Loop:*\n"
        f"Tanam → Tunggu → Panen → Upgrade → Unlock → Repeat\n\n"
        f"⚙️ *Sistem Aktif:*\n"
        f"• 🌤️ Weather (5 jenis, pengaruhi grow & reward)\n"
        f"• 🐛 Pest (hama acak saat tanam)\n"
        f"• 🌱 Fertilizer (speed + reward boost)\n"
        f"• 🍓 Combo Bonus (tanam sejenis dapat multiplier)\n"
        f"• ⚡ Energy (anti-spam, regen otomatis)\n"
        f"• 🔧 Upgrade Farm (permanent boost)\n"
        f"• 🎒 Inventory server-authoritative\n"
        f"• 🔄 Market real (item berpindah)\n\n"
        f"🛡️ *Backend authoritative* — semua divalidasi server",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user if q else update.effective_user
    if q: await q.answer()
    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    eff = calc_effects(user.id)
    with db() as c:
        inv_cnt = c.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=?", (user.id,)).fetchone()[0]
        upg_cnt = c.execute("SELECT COUNT(*) FROM upgrades WHERE user_id=?", (user.id,)).fetchone()[0]
        ref_cnt = c.execute("SELECT ref_count FROM players WHERE user_id=?", (user.id,)).fetchone()[0]
    wk = current_weather()
    w  = WEATHER[wk]
    xp_pct = int(p['xp'] / max(p['xp_max'],1) * 100)
    boost = f"⚡ {p['boost_mult']}x" if p['boost_until'] > time.time() else "❌ Off"
    wallet = f"`{p['wallet_addr'][:8]}...{p['wallet_addr'][-6:]}`" if p['wallet_addr'] else "Belum terhubung"
    text = (
        f"👨‍🌾 *{p['first_name']}*  @{p['username'] or '-'}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⭐ Lv.*{p['level']}*  XP: {p['xp']:,}/{p['xp_max']:,} ({xp_pct}%)\n"
        f"🪙 {p['gold']:,}  💎 {p['gems']:,}  🔵 {p['fvt']:,} FVT\n"
        f"⚡ Energy: {p['energy']}/{p['energy_max']}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🌾 Total Panen: *{p['total_harvest']:,}*\n"
        f"🔗 Combo Hits: *{p['total_combo']:,}*\n"
        f"🔥 Streak: *{p['streak']} hari*\n"
        f"🎒 Inventory: *{inv_cnt} item*\n"
        f"🔧 Upgrade: *{upg_cnt}*  👥 Referral: *{ref_cnt}*\n"
        f"🚀 Boost: {boost}\n"
        f"💼 Wallet: {wallet}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{w['emoji']} Cuaca: *{w['label']}*\n"
        f"🌱 Grow Speed +{int(eff['grow_speed']*100)}%  "
        f"⭐ XP +{int(eff['xp_bonus']*100)}%  "
        f"💰 FVT +{int(eff['fvt_bonus']*100)}%"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Main", web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton("🎒 Inventory", callback_data="inventory"),
         InlineKeyboardButton("🌾 Farm", callback_data="farm")],
    ])
    if q: await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else: await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_weather(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    wk = current_weather()
    w  = WEATHER[wk]
    secs = next_weather_change_secs()
    h, m = secs // 3600, (secs % 3600) // 60
    lines = [f"{w['emoji']} *Cuaca Sekarang: {w['label']}*\n━━━━━━━━━━━━━━━━"]
    gm = w['grow_mult']
    rm = w['reward_mult']
    lines.append(f"🌱 Grow Speed: {'%.0f%%' % (abs(gm-1)*100)} {'⚡ lebih cepat' if gm>1 else '🐢 lebih lambat' if gm<1 else 'normal'}")
    lines.append(f"💰 Reward: {'%.0f%%' % (abs(rm-1)*100)} {'📈 bonus' if rm>1 else '📉 berkurang' if rm<1 else 'normal'}")
    lines.append(f"🐛 Risiko Hama: {'%.0f%%' % (w['pest_chance']*100)}")
    lines.append(f"\n⏳ Berubah dalam *{h}j {m}m*\n")
    lines.append("🌈 *Semua Cuaca:*")
    for k, wd in WEATHER.items():
        active = " ← *Sekarang*" if k == wk else ""
        lines.append(f"  {wd['emoji']} *{wd['label']}*{active}")
        lines.append(f"    grow×{wd['grow_mult']} · reward×{wd['reward_mult']} · hama {int(wd['pest_chance']*100)}%")
    if q: await q.edit_message_text("\n".join(lines), parse_mode="Markdown")
    else: await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_farm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user if q else update.effective_user
    if q: await q.answer()
    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    with db() as c:
        plots = [dict(r) for r in c.execute("SELECT * FROM plots WHERE user_id=? ORDER BY slot", (user.id,)).fetchall()]
    now = time.time()
    ready = growing = empty = locked = 0
    pest_plots = []
    for pl in plots:
        if pl['locked']:   locked += 1; continue
        if not pl['crop_id']: empty += 1; continue
        elapsed = now - pl['planted_at']
        remaining = max(0, int(pl['grow_time'] - elapsed))
        if remaining == 0: ready += 1
        else: growing += 1
        if pl['has_pest']: pest_plots.append(pl['slot'] + 1)
    wk = current_weather()
    w = WEATHER[wk]
    lines = [
        f"🌾 *Farm {p['first_name']}* · Lv.{p['level']}\n━━━━━━━━━━━━━━━━",
        f"✅ Siap: *{ready}*  🌱 Tumbuh: *{growing}*  🌿 Kosong: *{empty}*  🔒 Terkunci: *{locked}*",
        f"⚡ Energy: *{p['energy']}/{p['energy_max']}*",
        f"{w['emoji']} Cuaca: *{w['label']}* (grow×{w['grow_mult']}, reward×{w['reward_mult']})",
    ]
    if pest_plots:
        lines.append(f"\n⚠️ *Hama di lahan {', '.join(map(str,pest_plots))}!*")
        lines.append("Gunakan `/pesticide` atau beli pestisida di toko game.")
    eff = calc_effects(user.id)
    owned = get_upgrades_owned(user.id)
    if owned:
        lines.append(f"\n🔧 *Upgrade Aktif ({len(owned)}):*")
        for uid in owned[:6]:
            u = UPGRADES.get(uid,{})
            lines.append(f"  {u.get('label','?')} — {u.get('desc','')}")
    # Available upgrades
    available = [(k,v) for k,v in UPGRADES.items() if k not in owned and p['level'] >= v['lv']]
    if available:
        lines.append(f"\n🛒 *Upgrade Tersedia:*")
        for uid, u in available[:5]:
            lines.append(f"  {u['label']} — {u['cost']:,}🪙  (/upgrade {uid})")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Buka Farm", web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton("🌤️ Cuaca", callback_data="weather"),
         InlineKeyboardButton("📊 Profil", callback_data="profile")],
    ])
    if q: await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)
    else: await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)

async def cmd_inventory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user if q else update.effective_user
    if q: await q.answer()
    if not player_get(user.id):
        player_upsert(user.id, user.username or '', user.first_name)
    with db() as c:
        items = [dict(r) for r in c.execute(
            "SELECT * FROM inventory WHERE user_id=? AND quantity>0 ORDER BY item_type,item_id",
            (user.id,)).fetchall()]
    if not items:
        text = ("🎒 *Inventory Kosong*\n\n"
                "Mulai tanam dan panen untuk mengisi inventory!\n"
                "Gunakan `/farm` untuk lihat status lahan kamu.")
    else:
        crops = [i for i in items if i['item_type']=='crop']
        seeds = [i for i in items if i['item_type']=='seed']
        tools = [i for i in items if i['item_type']=='tool']
        total_qty = sum(i['quantity'] for i in items)
        total_fvt = sum(i['quantity'] * i['fvt_value'] for i in items)
        lines = [f"🎒 *Inventory — {total_qty} item · ~{total_fvt:,} FVT*\n━━━━━━━━━━━━━━━━"]
        if crops:
            lines.append("🌾 *Hasil Panen:*")
            for it in crops[:15]:
                lines.append(f"  {it['emoji']} {it['item_name']} x{it['quantity']}  (~{it['fvt_value']*it['quantity']:,} FVT)")
        if seeds:
            lines.append("\n🌱 *Benih:*")
            for it in seeds:
                lines.append(f"  {it['emoji']} {it['item_name']} x{it['quantity']}")
        if tools:
            lines.append("\n🔧 *Tools:*")
            for it in tools:
                lines.append(f"  {it['emoji']} {it['item_name']} x{it['quantity']}")
        lines.append(f"\n━━━━━━━━━━━━━━━━")
        lines.append("💡 Jual: `/sell <item_id> <qty> <harga_fvt>`")
        lines.append("Contoh: `/sell wheat 10 500`")
        text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌾 Main", web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton("🔄 Market", callback_data="market"),
         InlineKeyboardButton("📊 Profil", callback_data="profile")],
    ])
    if q: await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else: await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/sell <item_id> <qty> <price_fvt>"""
    user = update.effective_user
    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    if p['is_banned']:
        await update.message.reply_text("🚫 Akun ditangguhkan."); return
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text(
            "📦 *Cara Jual:*\n`/sell <item_id> <jumlah> <harga_fvt>`\n\n"
            "Contoh:\n`/sell wheat 10 500`\n`/sell carrot 5 800`\n\n"
            "Gunakan /inventory untuk lihat item_id.",
            parse_mode="Markdown"); return
    item_id = ctx.args[0].lower()
    try:
        qty   = int(ctx.args[1])
        price = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("❌ Jumlah & harga harus angka!"); return
    if qty < 1 or price < 10:
        await update.message.reply_text("❌ Min qty=1, price=10 FVT"); return
    if price > 10_000_000:
        flag(user.id, 'PRICE_INJECT', f"item={item_id} price={price}")
        await update.message.reply_text("❌ Harga terlalu tinggi."); return
    if not rate_check(user.id, 'market_sell', 10, 3600):
        await update.message.reply_text("⏳ Max 10 listing/jam."); return
    with db_write() as c:
        if not inv_take(c, user.id, item_id, qty):
            await update.message.reply_text(f"❌ `{item_id}` tidak cukup di inventory!",
                                             parse_mode="Markdown"); return
        crop_def = CROPS.get(item_id, {})
        emoji = crop_def.get('emoji','📦')
        name  = crop_def.get('name', item_id)
        fee   = int(price * MARKET_FEE)
        c.execute("""INSERT INTO market_listings
            (seller_id,item_id,item_name,emoji,item_type,quantity,price_fvt,fee_fvt)
            VALUES(?,?,?,?,?,?,?,?)""",
            (user.id, item_id, f"{name} x{qty}", emoji, 'crop', qty, price, fee))
        listing_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    net = price - fee
    await update.message.reply_text(
        f"✅ *Listing #{listing_id} Berhasil!*\n━━━━━━━━━━━━━━━━\n"
        f"{emoji} *{name} x{qty}*\n"
        f"💰 Harga: *{price:,} FVT*\n"
        f"📝 Fee (5%): *{fee:,} FVT*\n"
        f"💵 Kamu terima: *{net:,} FVT*\n\n"
        f"Batalkan: `/cancellist {listing_id}`",
        parse_mode="Markdown"
    )

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/buy <listing_id>"""
    user = update.effective_user
    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    if p['is_banned']:
        await update.message.reply_text("🚫 Akun ditangguhkan."); return
    if not ctx.args:
        await update.message.reply_text("Usage: `/buy <listing_id>`", parse_mode="Markdown"); return
    try:
        lid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ listing_id harus angka!"); return
    with db_write() as c:
        lst = c.execute("SELECT * FROM market_listings WHERE id=? AND status='active'", (lid,)).fetchone()
        if not lst:
            await update.message.reply_text("❌ Listing tidak ditemukan!"); return
        lst = dict(lst)
        if lst['seller_id'] == user.id:
            await update.message.reply_text("❌ Tidak bisa beli listing sendiri!"); return
        p2 = dict(c.execute("SELECT fvt FROM players WHERE user_id=?", (user.id,)).fetchone())
        if p2['fvt'] < lst['price_fvt']:
            await update.message.reply_text(
                f"❌ FVT kurang!\nKamu: *{p2['fvt']:,}* / Harga: *{lst['price_fvt']:,}*",
                parse_mode="Markdown"); return
        net_seller = lst['price_fvt'] - lst['fee_fvt']
        c.execute("UPDATE players SET fvt=fvt-? WHERE user_id=?", (lst['price_fvt'], user.id))
        c.execute("UPDATE players SET fvt=fvt+? WHERE user_id=?", (net_seller, lst['seller_id']))
        crop_def = CROPS.get(lst['item_id'], {})
        inv_add(c, user.id, lst['item_id'], lst['emoji'], crop_def.get('name',lst['item_id']),
                lst['item_type'], lst['quantity'], crop_def.get('fvt',0))
        c.execute("UPDATE market_listings SET status='sold',buyer_id=?,sold_at=CURRENT_TIMESTAMP WHERE id=?",
                   (user.id, lid))
        c.execute("""INSERT INTO market_history(listing_id,item_id,item_name,emoji,quantity,
            price_fvt,seller_id,buyer_id) VALUES(?,?,?,?,?,?,?,?)""",
            (lid, lst['item_id'], lst['item_name'], lst['emoji'], lst['quantity'],
             lst['price_fvt'], lst['seller_id'], user.id))
    await update.message.reply_text(
        f"✅ *Pembelian Berhasil!*\n{lst['emoji']} *{lst['item_name']}*\n"
        f"💰 Dibayar: *{lst['price_fvt']:,} FVT*",
        parse_mode="Markdown"
    )
    try:
        await ctx.bot.send_message(lst['seller_id'],
            f"🎉 *Terjual!* {lst['emoji']} *{lst['item_name']}*\n💰 +{net_seller:,} FVT",
            parse_mode="Markdown")
    except Exception: pass

async def cmd_cancellist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: `/cancellist <listing_id>`", parse_mode="Markdown"); return
    try: lid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID harus angka!"); return
    with db_write() as c:
        lst = c.execute("SELECT * FROM market_listings WHERE id=? AND seller_id=? AND status='active'",
                         (lid, user.id)).fetchone()
        if not lst:
            await update.message.reply_text("❌ Listing tidak ditemukan!"); return
        lst = dict(lst)
        crop_def = CROPS.get(lst['item_id'], {})
        inv_add(c, user.id, lst['item_id'], lst['emoji'], crop_def.get('name',lst['item_id']),
                lst['item_type'], lst['quantity'], crop_def.get('fvt',0))
        c.execute("UPDATE market_listings SET status='cancelled' WHERE id=?", (lid,))
    await update.message.reply_text(
        f"✅ Listing #{lid} dibatalkan.\n{lst['emoji']} *{lst['item_name']}* dikembalikan ke inventory.",
        parse_mode="Markdown")

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    with db() as c:
        listings = [dict(r) for r in c.execute("""
            SELECT ml.*, p.first_name as seller_name
            FROM market_listings ml JOIN players p ON p.user_id=ml.seller_id
            WHERE ml.status='active' ORDER BY ml.id DESC LIMIT 15""").fetchall()]
        vol = c.execute("SELECT COALESCE(SUM(price_fvt),0) FROM market_listings WHERE status='sold'").fetchone()[0]
        trades_today = c.execute(
            "SELECT COUNT(*) FROM market_listings WHERE status='sold' AND date(sold_at)=date('now')").fetchone()[0]
    if not listings:
        text = f"🔄 *Marketplace FarmVerse*\n📊 Vol: {vol:,} FVT · {trades_today} trades hari ini\n━━━━━━━━━━━━━━━━\n📦 Belum ada listing aktif.\n\nJual item: `/sell <item_id> <qty> <harga_fvt>`"
    else:
        lines = [f"🔄 *Marketplace* · Vol: {vol:,} FVT · {trades_today} trades hari ini\n━━━━━━━━━━━━━━━━"]
        for l in listings:
            lines.append(f"{l['emoji']} *{l['item_name']}* — *{l['price_fvt']:,} FVT*\n   👤 {l['seller_name']}  /buy {l['id']}")
        text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Buka Market", web_app=WebAppInfo(url=GAME_URL+'#market'))],
    ])
    if q: await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else: await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_quest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user if q else update.effective_user
    if q: await q.answer()
    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    today = date.today().isoformat()
    fresh = p['quest_date'] != today
    qh, qp, qs = (0,0,0) if fresh else (p['quest_harvest'],p['quest_plant'],p['quest_sell'])
    done = bool(p['quest_done']) and not fresh
    lines = [f"📋 *Misi Harian* · Hari ke-{p['streak']+1}\n━━━━━━━━━━━━━━━━"]
    lines.append(f"{'✅' if qh>=5 else '⬜'} Panen 5 tanaman: *{min(qh,5)}/5*")
    lines.append(f"{'✅' if qp>=3 else '⬜'} Tanam 3 kali: *{min(qp,3)}/3*")
    lines.append(f"{'✅' if qs>=1 else '⬜'} Jual 1 item di market: *{min(qs,1)}/1*")
    lines.append(f"\n🎁 *Reward: 300🪙 + 20💎 + 100 FVT*")
    lines.append(f"\nStatus: {'✅ *SELESAI!*' if done else '⏳ Belum selesai'}")
    lines.append(f"\n🔥 Streak: *{p['streak']} hari*")
    lines.append("Streak 7 hari = bonus +200🪙 tambahan!")
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌾 Main Sekarang!", web_app=WebAppInfo(url=GAME_URL))]])
    if q: await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else: await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/upgrade [upgrade_id]"""
    user = update.effective_user
    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    owned = get_upgrades_owned(user.id)
    if not ctx.args:
        lines = ["🔧 *Farm Upgrades*\n━━━━━━━━━━━━━━━━"]
        for uid, u in UPGRADES.items():
            own = uid in owned
            avail = p['level'] >= u['lv']
            status = "✅" if own else ("🔓" if avail else f"🔒 Lv{u['lv']}")
            lines.append(f"{status} {u['label']}\n   {u['desc']} — {u['cost']:,}🪙")
            if avail and not own:
                lines.append(f"   `/upgrade {uid}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown"); return
    uid = ctx.args[0]
    if uid not in UPGRADES:
        await update.message.reply_text("❌ Upgrade ID tidak valid!"); return
    u = UPGRADES[uid]
    if uid in owned:
        await update.message.reply_text("❌ Sudah dimiliki!"); return
    if p['level'] < u['lv']:
        await update.message.reply_text(f"❌ Butuh Level {u['lv']}!"); return
    if p['gold'] < u['cost']:
        await update.message.reply_text(f"❌ Butuh {u['cost']:,}🪙"); return
    with db_write() as c:
        c.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (u['cost'], user.id))
        if u['effect'] == 'energy_max':
            c.execute("UPDATE players SET energy_max=energy_max+? WHERE user_id=?", (int(u['val']), user.id))
        c.execute("INSERT OR IGNORE INTO upgrades(user_id,upgrade_id) VALUES(?,?)", (user.id, uid))
    await update.message.reply_text(
        f"✅ *{u['label']}* berhasil!\n{u['desc']}", parse_mode="Markdown")

async def cmd_pesticide(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    mass = '--all' in (ctx.args or [])
    pest_id = 'mass_pest' if mass else 'pesticide'
    with db() as c:
        inv_row = c.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?",
                             (user.id, pest_id)).fetchone()
    if not inv_row or inv_row['quantity'] < 1:
        await update.message.reply_text(
            f"❌ Tidak ada {'Pestisida Massal' if mass else 'Pestisida'} di inventory!\n"
            "Beli di toko game dengan `/buy_item pesticide`"); return
    with db_write() as c:
        inv_take(c, user.id, pest_id, 1)
        if mass:
            c.execute("UPDATE plots SET has_pest=0 WHERE user_id=?", (user.id,))
        else:
            slot_arg = int(ctx.args[0]) - 1 if ctx.args and ctx.args[0].isdigit() else 0
            c.execute("UPDATE plots SET has_pest=0 WHERE user_id=? AND slot=?", (user.id, slot_arg))
    await update.message.reply_text("✅ Hama berhasil dibasmi!")

async def cmd_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user if q else update.effective_user
    if q: await q.answer()
    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    bot_info = await ctx.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={user.id}"
    text = (
        f"🔗 *Referral Kamu*\n━━━━━━━━━━━━━━━━\n"
        f"`{link}`\n\n"
        f"👥 Total referral: *{p['ref_count']}*\n"
        f"🎁 *Reward per referral:*\n"
        f"• Kamu: +200🪙 +5💎 +100 FVT\n"
        f"• Temanmu: +100🪙 starter bonus\n\n"
        f"Bagikan link ini ke teman kamu!"
    )
    if q: await q.edit_message_text(text, parse_mode="Markdown")
    else: await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    p = player_get(user.id)
    if not p:
        player_upsert(user.id, user.username or '', user.first_name)
        p = player_get(user.id)
    await update.message.reply_text(
        f"💰 *Saldo {p['first_name']}*\n━━━━━━━━━━━━━━━━\n"
        f"🪙 Koin: *{p['gold']:,}*\n"
        f"💎 Gems: *{p['gems']:,}*\n"
        f"🔵 FVT Token: *{p['fvt']:,}*\n"
        f"⚡ Energy: *{p['energy']}/{p['energy_max']}*\n"
        f"💼 Wallet: `{p['wallet_addr'] or 'Belum terhubung'}`",
        parse_mode="Markdown")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    text = (
        "❓ *Panduan FarmVerse Phase 1*\n━━━━━━━━━━━━━━━━\n\n"
        "🌾 *Cara Bermain:*\n"
        "1. Buka game → pilih lahan kosong → pilih tanaman\n"
        "2. Tunggu tanaman tumbuh (cuaca pengaruhi waktu)\n"
        "3. Kembali saat ready → panen!\n"
        "4. Hasil panen masuk inventory\n"
        "5. Jual di market untuk dapat FVT\n"
        "6. Beli upgrade untuk farming lebih efisien\n\n"
        "📋 *Commands Utama:*\n"
        "`/farm` — status lahan kamu\n"
        "`/inventory` — lihat isi tas\n"
        "`/weather` — cuaca & efek\n"
        "`/quest` — misi harian\n"
        "`/market` — lihat semua listing\n"
        "`/sell item qty harga` — jual item\n"
        "`/buy listing_id` — beli dari market\n"
        "`/cancellist id` — batalkan listing\n"
        "`/upgrade` — lihat & beli upgrade\n"
        "`/pesticide [slot]` — basmi hama\n"
        "`/referral` — link referral\n"
        "`/balance` — cek saldo\n\n"
        "💡 *Tips:*\n"
        "• Cuaca emas 🌟 = reward 1.5x, tidak ada hama!\n"
        "• Tanam 3+ crop sejenis = Combo Bonus!\n"
        "• Energy regen otomatis 2/menit\n"
        "• Streak 7 hari = bonus 200🪙 extra\n\n"
        "📞 Support: @farmverse\\_support"
    )
    if q: await q.edit_message_text(text, parse_mode="Markdown")
    else: await update.message.reply_text(text, parse_mode="Markdown")

# ─── ADMIN COMMANDS ───────────────────────────────────────────────────────────

def admin_only(fn):
    async def wrap(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if upd.effective_user.id != ADMIN_ID:
            await upd.message.reply_text("⛔ Hanya admin."); return
        return await fn(upd, ctx)
    return wrap

@admin_only
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        total  = c.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        active = c.execute("SELECT COUNT(*) FROM players WHERE date(last_seen)=date('now')").fetchone()[0]
        banned = c.execute("SELECT COUNT(*) FROM players WHERE is_banned=1").fetchone()[0]
        harv   = c.execute("SELECT COALESCE(SUM(total_harvest),0) FROM players").fetchone()[0]
        vol    = c.execute("SELECT COALESCE(SUM(price_fvt),0) FROM market_listings WHERE status='sold'").fetchone()[0]
        active_list= c.execute("SELECT COUNT(*) FROM market_listings WHERE status='active'").fetchone()[0]
        inv_tot= c.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory").fetchone()[0]
        flags  = c.execute("SELECT COUNT(*) FROM anticheat_log WHERE date(ts,'unixepoch')=date('now')").fetchone()[0]
        upg_tot= c.execute("SELECT COUNT(*) FROM upgrades").fetchone()[0]
    wk = current_weather()
    w  = WEATHER[wk]
    await update.message.reply_text(
        f"📊 *Admin Dashboard — FarmVerse v5*\n━━━━━━━━━━━━━━━━\n"
        f"👥 Pemain: *{total:,}* · Aktif: *{active:,}* · Ban: *{banned}*\n"
        f"🌾 Total Panen: *{harv:,}*\n"
        f"🎒 Total Item: *{inv_tot:,}*\n"
        f"🔧 Upgrade Dibeli: *{upg_tot:,}*\n"
        f"🔄 Market Vol: *{vol:,} FVT*  Active: *{active_list}*\n"
        f"🛡️ AC Flags Hari Ini: *{flags}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{w['emoji']} Cuaca: *{w['label']}*",
        parse_mode="Markdown")

@admin_only
async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /ban <user_id> [reason]"); return
    try: tid = int(ctx.args[0]); reason = ' '.join(ctx.args[1:]) or 'Admin ban'
    except ValueError:
        await update.message.reply_text("❌ user_id harus angka!"); return
    with db() as c:
        c.execute("UPDATE players SET is_banned=1, ban_reason=? WHERE user_id=?", (reason, tid))
    await update.message.reply_text(f"🚫 {tid} dibanned. Alasan: {reason}")
    try: await ctx.bot.send_message(tid, f"🚫 Akun ditangguhkan.\nAlasan: {reason}\nHubungi @farmverse_support")
    except Exception: pass

@admin_only
async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /unban <user_id>"); return
    try: tid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id harus angka!"); return
    with db() as c:
        c.execute("UPDATE players SET is_banned=0, ban_reason='', suspicion=0 WHERE user_id=?", (tid,))
    await update.message.reply_text(f"✅ {tid} di-unban.")
    try: await ctx.bot.send_message(tid, "✅ Akun dipulihkan. Selamat bermain!")
    except Exception: pass

@admin_only
async def cmd_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text("Usage: /give <uid> <gold|gems|fvt|item_id> <amount>"); return
    try: tid = int(ctx.args[0]); currency = ctx.args[1].lower(); amount = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("❌ Format salah!"); return
    col = {'gold':'gold','gems':'gems','fvt':'fvt'}.get(currency)
    if col:
        with db() as c:
            c.execute(f"UPDATE players SET {col}={col}+? WHERE user_id=?", (amount, tid))
        await update.message.reply_text(f"✅ +{amount:,} {currency} ke {tid}.")
    elif currency in CROPS:
        with db() as c:
            inv_add(c, tid, currency, CROPS[currency]['emoji'], CROPS[currency]['name'], 'crop', amount, CROPS[currency]['fvt'])
        await update.message.reply_text(f"✅ +{amount}x {CROPS[currency]['name']} ke {tid}.")
    else:
        await update.message.reply_text("❌ currency tidak valid.")

@admin_only
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast <pesan>"); return
    msg = ' '.join(ctx.args)
    with db() as c:
        ids = [r['user_id'] for r in c.execute("SELECT user_id FROM players WHERE is_banned=0").fetchall()]
    sent = failed = 0
    for uid in ids:
        try:
            await ctx.bot.send_message(uid, f"📢 *Pengumuman FarmVerse*\n\n{msg}", parse_mode="Markdown")
            sent += 1; await asyncio.sleep(0.05)
        except Exception: failed += 1
    with db() as c:
        c.execute("INSERT INTO broadcast_log(admin_id,message,sent_count) VALUES(?,?,?)", (ADMIN_ID, msg, sent))
    await update.message.reply_text(f"✅ Broadcast: {sent} terkirim, {failed} gagal.")

@admin_only
async def cmd_flags(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = [dict(r) for r in c.execute("""
            SELECT al.*, p.first_name, p.username
            FROM anticheat_log al JOIN players p ON p.user_id=al.user_id
            ORDER BY al.ts DESC LIMIT 20""").fetchall()]
    if not rows:
        await update.message.reply_text("✅ Tidak ada flags."); return
    lines = ["🛡️ *AC Flags Terbaru*\n━━━━━━━━━━━━━━━━"]
    for f in rows:
        lines.append(f"👤 {f['first_name']} ({f['user_id']})\n   ⚠️ {f['code']}: {f['reason']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

@admin_only
async def cmd_resetquest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Force reset daily quest for a user (for testing)."""
    if not ctx.args:
        await update.message.reply_text("Usage: /resetquest <user_id>"); return
    try: tid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id harus angka!"); return
    with db() as c:
        c.execute("UPDATE players SET quest_harvest=0,quest_plant=0,quest_sell=0,quest_done=0,quest_date='' WHERE user_id=?", (tid,))
    await update.message.reply_text(f"✅ Quest reset untuk {tid}.")

# ─── CALLBACK ROUTER ──────────────────────────────────────────────────────────

CALLBACKS = {
    'profile':   cmd_profile,
    'inventory': cmd_inventory,
    'weather':   cmd_weather,
    'farm':      cmd_farm,
    'market':    cmd_market,
    'quest':     cmd_quest,
    'referral':  cmd_referral,
    'help':      cmd_help,
}

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = update.callback_query.data
    if d in CALLBACKS:
        await CALLBACKS[d](update, ctx)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    log.info(f"✅ FarmVerse v5 ready · token: {BOT_TOKEN[:12]}...")

    app = Application.builder().token(BOT_TOKEN).build()

    # User
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("profile",     cmd_profile))
    app.add_handler(CommandHandler("farm",        cmd_farm))
    app.add_handler(CommandHandler("inventory",   cmd_inventory))
    app.add_handler(CommandHandler("inv",         cmd_inventory))
    app.add_handler(CommandHandler("weather",     cmd_weather))
    app.add_handler(CommandHandler("quest",       cmd_quest))
    app.add_handler(CommandHandler("market",      cmd_market))
    app.add_handler(CommandHandler("sell",        cmd_sell))
    app.add_handler(CommandHandler("buy",         cmd_buy))
    app.add_handler(CommandHandler("cancellist",  cmd_cancellist))
    app.add_handler(CommandHandler("upgrade",     cmd_upgrade))
    app.add_handler(CommandHandler("pesticide",   cmd_pesticide))
    app.add_handler(CommandHandler("referral",    cmd_referral))
    app.add_handler(CommandHandler("ref",         cmd_referral))
    app.add_handler(CommandHandler("balance",     cmd_balance))
    app.add_handler(CommandHandler("help",        cmd_help))

    # Admin
    app.add_handler(CommandHandler("admin",       cmd_admin))
    app.add_handler(CommandHandler("ban",         cmd_ban))
    app.add_handler(CommandHandler("unban",       cmd_unban))
    app.add_handler(CommandHandler("give",        cmd_give))
    app.add_handler(CommandHandler("broadcast",   cmd_broadcast))
    app.add_handler(CommandHandler("flags",       cmd_flags))
    app.add_handler(CommandHandler("resetquest",  cmd_resetquest))

    # WebApp & Callbacks
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp))
    app.add_handler(CallbackQueryHandler(handle_callback))

    log.info("🌾 FarmVerse Bot v5 running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
