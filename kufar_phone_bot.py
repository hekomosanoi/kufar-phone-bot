import asyncio
import logging
import sqlite3
import re
import os
import random
import aiohttp
from aiohttp import web
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# =====================================================================
# НАСТРОЙКИ, ТОКЕН И ВАШ ID (ПРИВАТНЫЙ РЕЖИМ)
# =====================================================================
# Ваш токен от BotFather
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8680343679:AAH_9hyoIgmP7u3QgYWj1YK1HTQjzb2JKdw").strip()

# Ваш персональный Telegram ID (доступ разрешен только вам)
ALLOWED_USER_ID = 7805601948

PORT = int(os.getenv("PORT", 10000))
CHECK_INTERVAL_SECONDS = 75
DB_PATH = "kufar_phones.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("KufarRadar")

# =====================================================================
# БАЗА ДАННЫХ (SQLite)
# =====================================================================
class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    min_budget INTEGER DEFAULT 100,
                    max_budget INTEGER DEFAULT 800,
                    allow_minor_defects INTEGER DEFAULT 1,
                    brands TEXT DEFAULT 'Все',
                    is_active INTEGER DEFAULT 1
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS seen_ads (
                    ad_id TEXT PRIMARY KEY,
                    price_byn REAL,
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def get_or_create_user(self, user_id: int) -> dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, min_budget, max_budget, allow_minor_defects, brands, is_active FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("""
                    INSERT INTO users (user_id, min_budget, max_budget, allow_minor_defects, brands, is_active)
                    VALUES (?, 100, 800, 1, 'Все', 1)
                """, (user_id,))
                conn.commit()
                return {
                    "user_id": user_id, "min_budget": 100, "max_budget": 800,
                    "allow_minor_defects": 1, "brands": "Все", "is_active": 1
                }
            return {
                "user_id": row[0], "min_budget": row[1], "max_budget": row[2],
                "allow_minor_defects": row[3], "brands": row[4], "is_active": row[5]
            }

    def update_user_budget(self, user_id: int, min_b: int, max_b: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET min_budget = ?, max_budget = ? WHERE user_id = ?", (min_b, max_b, user_id))
            conn.commit()

    def update_user_defects(self, user_id: int, allow: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET allow_minor_defects = ? WHERE user_id = ?", (allow, user_id))
            conn.commit()

    def update_user_brands(self, user_id: int, brands: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET brands = ? WHERE user_id = ?", (brands, user_id))
            conn.commit()

    def get_active_users(self) -> List[dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, min_budget, max_budget, allow_minor_defects, brands, is_active FROM users WHERE is_active = 1")
            rows = cursor.fetchall()
            return [
                {
                    "user_id": r[0], "min_budget": r[1], "max_budget": r[2],
                    "allow_minor_defects": r[3], "brands": r[4], "is_active": r[5]
                }
                for r in rows
                if r[0] == ALLOWED_USER_ID  # Строгий фильтр: отправлять только вам
            ]

    def is_ad_seen(self, ad_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM seen_ads WHERE ad_id = ?", (ad_id,))
            return cursor.fetchone() is not None

    def mark_ad_seen(self, ad_id: str, price_byn: float):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO seen_ads (ad_id, price_byn) VALUES (?, ?)", (ad_id, price_byn))
            conn.commit()

    def get_total_seen_count(self) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM seen_ads")
            row = cursor.fetchone()
            return row[0] if row else 0

db = Database()

# =====================================================================
# ЦЕНОВЫЕ ОРИЕНТИРЫ РЫНКА (BYN)
# =====================================================================
BENCHMARK_PRICES = {
    # Apple
    "iphone 11 64": 650, "iphone 11 128": 750,
    "iphone 12 mini": 780, "iphone 12 64": 920, "iphone 12 128": 1050, "iphone 12 pro": 1300,
    "iphone 13 mini": 1150, "iphone 13 128": 1450, "iphone 13 pro": 1800, "iphone 13 pro max": 2050,
    "iphone 14 128": 1800, "iphone 14 pro": 2300,
    "iphone 15 128": 2200, "iphone 15 pro": 2850,
    "iphone xr": 480, "iphone xs": 400, "iphone se 2020": 420, "iphone se 2022": 620,
    # Samsung
    "s20 fe": 520, "s21 fe": 720, "s21 ultra": 1050, "s21": 800,
    "s22": 1050, "s22 ultra": 1550, "s23": 1500, "s23 ultra": 2150,
    "a52": 390, "a53": 490, "a54": 650, "a55": 820, "a34": 490, "a35": 620,
    # Xiaomi / Poco
    "redmi note 10 pro": 340, "redmi note 11 pro": 430, "redmi note 12 pro": 550, "redmi note 13 pro": 720,
    "poco x3 pro": 300, "poco x4 pro": 440, "poco x5 pro": 590, "poco x6 pro": 820,
    "poco f3": 460, "poco f4": 620, "poco f5": 870,
    # Pixel & Others
    "pixel 6a": 590, "pixel 6": 690, "pixel 7a": 800, "pixel 7": 990,
    "honor 50": 430, "honor 70": 620, "honor 90": 800
}

# Стоп-слова: критические дефекты и блокировки
STRICT_STOP_WORDS = [
    "icloud", "айклауд", "байпас", "bypass", "копия", "реплика", "фейк",
    "frp", "google lock", "гугл аккаунт", "аккаунт гугл", "ми аккаунт", "mi account", 
    "mi cloud", "ми клауд", "huawei id", "хуавей ид", "samsung account", "knox guard", 
    "knox", "лизинг", "яндекс плюс", "сплит", "рассрочк", "заблокирован оператором", "sim lock",
    "ldu", "live demo", "демо образец", "demo unit",
    "на запчасти", "под восстановление", "не включается", "залит", "утопленник", 
    "mdm", "демо", "не видит сеть", "без сети", "не звонит", "кирпич",
    "отвал", "реболл", "bootloop", "бутлуп", "вечный ребут", "перезагружается сам", 
    "зеленая полоса", "зеленые полосы", "полоса на экране", "черное пятно"
]

# Комплектация (не считается дефектом)
KIT_NUANCES = {
    "без коробк": "Без коробки",
    "нет коробк": "Без коробки",
    "коробка утеряна": "Без коробки",
    "без комплект": "Только телефон",
    "нет комплект": "Без комплекта",
    "без зарядк": "Без блока питания",
    "нет зарядк": "Без зарядного",
    "без блок": "Без блока питания",
    "без шнур": "Без кабеля",
    "без провод": "Без провода",
    "без чек": "Без документов",
    "нет чек": "Без документов",
    "только телефон": "Только сам телефон"
}

# Мелкие дефекты
MINOR_DEFECTS = {
    "акб": "Износ батареи / слабый АКБ",
    "батаре": "Снижена емкость аккумулятора",
    "трещина сзади": "Трещина на задней крышке",
    "задней крышк": "Царапины/трещина крышки",
    "царапин": "Следы использования / царапины",
    "потертост": "Потертости корпуса",
    "потёртост": "Потертости корпуса",
    "скол": "Мелкие сколы",
    "выгоран": "Выгорание AMOLED экрана"
}

def detect_brand(title: str, body: str) -> Tuple[str, bool]:
    text = f"{title or ''} {body or ''}".lower()
    if any(k in text for k in ["iphone", "айфон", "apple", "ios"]):
        return "Apple (iOS)", False

    android_brands = [
        ("Samsung", ["samsung", "самсунг", "galaxy", "галакси"]),
        ("Xiaomi / POCO", ["xiaomi", "сяоми", "redmi", "редми", "poco", "поко"]),
        ("Google Pixel", ["pixel", "пиксель", "google"]),
        ("Honor", ["honor", "хонор"]),
        ("Huawei", ["huawei", "хуавей"]),
        ("Realme", ["realme", "реалми"]),
        ("Tecno", ["tecno", "текно"]),
        ("Infinix", ["infinix", "инфиникс"]),
        ("OnePlus", ["oneplus", "ванплас", "1+"]),
        ("Nothing", ["nothing", "насинг"]),
        ("Motorola", ["motorola", "моторола"]),
        ("Vivo / iQOO", ["vivo", "виво", "iqoo"]),
        ("Oppo", ["oppo", "оппо"])
    ]

    for brand_name, keywords in android_brands:
        if any(kw in text for kw in keywords):
            return brand_name, True

    return "Android (Другой)", True

def extract_memory_info(text: str) -> Optional[str]:
    combo = re.search(r'\b(\d{1,2})\s*[\/\+]\s*(\d{2,4})\s*(?:gb|гб)?\b', text, re.IGNORECASE)
    if combo:
        return f"{combo.group(1)}/{combo.group(2)} GB"
    rom = re.search(r'\b(32|64|128|256|512|1024|1tb|1тб)\s*(?:gb|гб)?\b', text, re.IGNORECASE)
    if rom:
        return f"{rom.group(1).upper()} GB"
    return None

def analyze_phone_text(title: str, body: str) -> dict:
    full_text = f"{title or ''} {body or ''}".lower()
    critical_triggers = [w for w in STRICT_STOP_WORDS if w in full_text]
    
    kit_details = []
    for kit_key, kit_desc in KIT_NUANCES.items():
        if kit_key in full_text and kit_desc not in kit_details:
            kit_details.append(kit_desc)

    found_defects = []
    for defect_key, defect_desc in MINOR_DEFECTS.items():
        if defect_key in full_text and defect_desc not in found_defects:
            found_defects.append(defect_desc)

    bat_match = re.search(r'(?:акб|батаре[яе]|емкость|ёмкость)\D*?(\d{2,3})\s*%', full_text)
    battery_health = int(bat_match.group(1)) if bat_match else None
    memory = extract_memory_info(f"{title or ''} {body or ''}")

    is_dangerous = len(critical_triggers) > 0
    has_minor_defects = len(found_defects) > 0 or (battery_health is not None and battery_health < 80)

    return {
        "is_safe": not is_dangerous,
        "critical_reasons": critical_triggers,
        "has_minor_defects": has_minor_defects,
        "minor_defects": found_defects,
        "kit_details": kit_details,
        "battery_health": battery_health,
        "memory": memory
    }

def estimate_market_price(title: str) -> Optional[int]:
    cleaned = (title or "").lower()
    for model_key, est_price in BENCHMARK_PRICES.items():
        tokens = model_key.split()
        if all(token in cleaned for token in tokens):
            return est_price
    return None

# =====================================================================
# ЗАПРОС К KUFAR API
# =====================================================================
class KufarScraper:
    BASE_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.kufar.by",
        "Referer": "https://www.kufar.by/l/mobilnye-telefony",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    async def fetch_latest_phones(self) -> List[dict]:
        params = {
            "cat": "17010",
            "sort": "lst.d",
            "size": "30",
            "typ": "sell"
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(headers=self.HEADERS, timeout=timeout) as session:
                async with session.get(self.BASE_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        ads = data.get("ads", [])
                        logger.info(f"[KUFAR] Опрос успешен. Получено объявлений: {len(ads)}")
                        return ads
                    else:
                        logger.warning(f"[KUFAR] Ответ сервера со статусом: {resp.status}")
                        return []
        except Exception as e:
            logger.error(f"[KUFAR] Ошибка запроса к API: {e}")
            return []

scraper = KufarScraper()

def evaluate_deal(ad: dict) -> Optional[dict]:
    ad_id = str(ad.get("ad_id", ""))
    subject = (ad.get("subject") or "").strip()
    body = (ad.get("body") or "").strip()
    price_byn_raw = ad.get("price_byn", "0")
    
    try:
        val = float(price_byn_raw or 0)
        price_byn = val / 100.0 if val > 10000 else val
    except (ValueError, TypeError):
        return None

    if price_byn < 35:
        return None

    analysis = analyze_phone_text(subject, body)
    if not analysis["is_safe"]:
        return None

    brand_name, is_android = detect_brand(subject, body)
    market_price = estimate_market_price(subject)
    
    discount_byn = 0
    discount_pct = 0
    if market_price and market_price > price_byn:
        discount_byn = market_price - price_byn
        discount_pct = int((discount_byn / market_price) * 100)
    else:
        market_price = None

    ad_url = ad.get("ad_link", f"https://www.kufar.by/item/{ad_id}")
    is_suspiciously_cheap = bool(market_price and (price_byn < market_price * 0.35))

    return {
        "ad_id": ad_id,
        "title": subject,
        "brand": brand_name,
        "is_android": is_android,
        "price_byn": price_byn,
        "market_price": market_price,
        "discount_byn": discount_byn,
        "discount_pct": discount_pct,
        "analysis": analysis,
        "url": ad_url,
        "is_suspiciously_cheap": is_suspiciously_cheap,
        "body_preview": (body[:180] + "...") if len(body) > 180 else (body or "Без описания")
    }

# =====================================================================
# ИНТЕРФЕЙС И КЛАВИАТУРЫ TELEGRAM
# =====================================================================
dp = Dispatcher()
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Проверка владельца бота
def check_access(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

def build_budget_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💵 До 450 BYN (Бюджетники)", callback_data="set_budget_50_450"),
            InlineKeyboardButton(text="💎 450 - 950 BYN (Топ-сегмент)", callback_data="set_budget_450_950"),
        ],
        [
            InlineKeyboardButton(text="🚀 950 - 1900 BYN (Флагманы)", callback_data="set_budget_950_1900"),
            InlineKeyboardButton(text="🔥 Без лимита (100 - 5000)", callback_data="set_budget_100_5000"),
        ],
        [
            InlineKeyboardButton(text="🤖 Выбор ОС / Брендов", callback_data="choose_brands_menu"),
            InlineKeyboardButton(text="⚙️ Мелкие дефекты: ВКЛ/ВЫКЛ", callback_data="toggle_defects"),
        ],
        [
            InlineKeyboardButton(text="🔍 Проверить ленту сейчас", callback_data="manual_check_btn"),
            InlineKeyboardButton(text="📊 Мой профиль", callback_data="show_profile"),
        ]
    ])

def build_brands_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Все телефоны (iOS + Android)", callback_data="set_brand_all")],
        [InlineKeyboardButton(text="🤖 Только Android (Все бренды)", callback_data="set_brand_android")],
        [InlineKeyboardButton(text="🍏 Только Apple (iPhone)", callback_data="set_brand_apple")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main_menu")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not check_access(message.from_user.id):
        await message.answer("⛔ Доступ ограничен. Это приватный бот.")
        return

    user_id = message.from_user.id
    user_data = db.get_or_create_user(user_id)
    defects_txt = "РАЗРЕШЕНЫ" if user_data["allow_minor_defects"] else "ЗАПРЕЩЕНЫ (только идеал)"
    welcome_text = (
        "👋 <b>Добро пожаловать в Kufar Phone Radar!</b>\n\n"
        "🔒 <i>Приватный режим активен: бот работает исключительно для вас.</i>\n\n"
        "Бот непрерывно мониторит Куфар и находит телефоны <b>ниже рынка</b>.\n"
        "🛡 <b>Защита:</b> отсекает блокировки (iCloud, FRP, Mi Account, Knox, рассрочки) и нерабочие устройства.\n"
        "📦 <b>Комплектация:</b> телефоны без коробки или зарядки <b>проходят всегда</b> и отмечаются для торга!\n\n"
        f"🎯 <b>Ваш текущий фильтр:</b>\n"
        f"• Бюджет: <code>{user_data['min_budget']} - {user_data['max_budget']} BYN</code>\n"
        f"• Платформа: <b>{user_data['brands']}</b>\n"
        f"• Мелкие нюансы (АКБ/крышка): <b>{defects_txt}</b>\n\n"
        "Настройте параметры кнопками ниже:"
    )
    await message.answer(welcome_text, reply_markup=build_budget_keyboard())

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    if not check_access(message.from_user.id):
        return
    await message.answer("⏳ Запрашиваю свежие объявления с Kufar...")
    ads = await scraper.fetch_latest_phones()
    if ads:
        await message.answer(f"✅ Kufar ответил штатно! Получено свежих объявлений: <b>{len(ads)}</b>.")
    else:
        await message.answer("⚠️ Kufar временно вернул пустой список или паузу. Повторите через минуту.")

@dp.message(Command("budget"))
async def cmd_set_custom_budget(message: types.Message):
    if not check_access(message.from_user.id):
        return
    parts = message.text.strip().split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("⚠️ Формат команды: <code>/budget МИН МАКС</code>\nПример: <code>/budget 100 650</code>")
        return
        
    min_b, max_b = int(parts[1]), int(parts[2])
    if min_b >= max_b:
        await message.answer("⚠️ Минимальный бюджет должен быть меньше максимального!")
        return

    db.update_user_budget(message.from_user.id, min_b, max_b)
    await message.answer(f"✅ <b>Бюджет обновлен:</b> от <b>{min_b} BYN</b> до <b>{max_b} BYN</b>.")

@dp.callback_query(F.data.startswith("set_budget_"))
async def callback_budget_preset(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    _, _, min_str, max_str = callback.data.split("_")
    min_b, max_b = int(min_str), int(max_str)
    db.update_user_budget(callback.from_user.id, min_b, max_b)
    await callback.answer(f"Бюджет: {min_b} - {max_b} BYN")
    await callback.message.edit_text(
        f"✅ <b>Установлен новый бюджет поиска:</b> <code>{min_b} - {max_b} BYN</code>",
        reply_markup=build_budget_keyboard()
    )

@dp.callback_query(F.data == "manual_check_btn")
async def callback_manual_check_btn(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer("Опрашиваю Kufar...")
    ads = await scraper.fetch_latest_phones()
    seen = db.get_total_seen_count()
    if ads:
        await callback.message.answer(f"✅ Kufar активен! В ленте: <b>{len(ads)}</b> объявлений. Всего в памяти бота: <b>{seen}</b>.")
    else:
        await callback.message.answer("⚠️ Kufar пока не вернул данные. Попробуйте через пару минут.")

@dp.callback_query(F.data == "toggle_defects")
async def callback_toggle_defects(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    user = db.get_or_create_user(callback.from_user.id)
    new_state = 0 if user["allow_minor_defects"] == 1 else 1
    db.update_user_defects(callback.from_user.id, new_state)
    state_str = "РАЗРЕШЕНЫ (АКБ/крышка/следы)" if new_state else "ЗАПРЕЩЕНЫ (только идеал)"
    await callback.answer(f"Мелкие нюансы: {state_str}")
    await callback.message.edit_text(
        f"⚙️ <b>Параметр обновлен!</b>\nДопуск нюансов: <b>{state_str}</b>.\n"
        "<i>Телефоны без коробки или зарядки пропускаются всегда.</i>",
        reply_markup=build_budget_keyboard()
    )

@dp.callback_query(F.data == "choose_brands_menu")
async def callback_choose_brands_menu(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    user = db.get_or_create_user(callback.from_user.id)
    await callback.message.edit_text(
        f"🤖 <b>Настройка брендов</b>\n\nСейчас отслеживаются: <b>{user['brands']}</b>\n\nВыберите категорию:",
        reply_markup=build_brands_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "set_brand_all")
async def callback_set_brand_all(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        return
    db.update_user_brands(callback.from_user.id, "Все (iOS + Android)")
    await callback.answer("Все платформы")
    await callback.message.edit_text("✅ Ищем <b>ВСЕ</b> телефоны (iPhone и любые Android).", reply_markup=build_budget_keyboard())

@dp.callback_query(F.data == "set_brand_android")
async def callback_set_brand_android(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        return
    db.update_user_brands(callback.from_user.id, "Только Android")
    await callback.answer("Только Android")
    await callback.message.edit_text("🤖 Ищем <b>ТОЛЬКО Android</b> (Samsung, Xiaomi, Poco, Pixel, Honor и др.).", reply_markup=build_budget_keyboard())

@dp.callback_query(F.data == "set_brand_apple")
async def callback_set_brand_apple(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        return
    db.update_user_brands(callback.from_user.id, "Только Apple")
    await callback.answer("Только Apple")
    await callback.message.edit_text("🍏 Ищем <b>ТОЛЬКО Apple iPhone</b>.", reply_markup=build_budget_keyboard())

@dp.callback_query(F.data == "back_to_main_menu")
async def callback_back_to_main(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        return
    await callback.message.edit_text("⚙️ <b>Панель управления поиском:</b>", reply_markup=build_budget_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "show_profile")
async def callback_profile(callback: CallbackQuery):
    if not check_access(callback.from_user.id):
        return
    user = db.get_or_create_user(callback.from_user.id)
    seen = db.get_total_seen_count()
    text = (
        f"👤 <b>Ваш профиль (Владелец):</b>\n\n"
        f"🆔 Ваш Telegram ID: <code>{user['user_id']}</code>\n"
        f"💰 Бюджет: <code>{user['min_budget']} - {user['max_budget']} BYN</code>\n"
        f"📱 Платформа: <b>{user['brands']}</b>\n"
        f"🔧 Нюансы (АКБ/корпус): <code>{'Включены' if user['allow_minor_defects'] else 'Отключены'}</code>\n"
        f"📦 Без коробки/зарядки: <b>РАЗРЕШЕНО ВСЕГДА</b>\n"
        f"🗄 Просмотрено ботом: <b>{seen}</b> объявлений\n"
        f"📡 Статус: <b>АКТИВЕН 24/7</b>"
    )
    await callback.message.answer(text, reply_markup=build_budget_keyboard())
    await callback.answer()

# =====================================================================
# ФОРМИРОВАНИЕ И ОТПРАВКА УВЕДОМЛЕНИЙ
# =====================================================================
async def send_deal_notification(deal: dict, user_id: int):
    title = deal["title"]
    brand = deal["brand"]
    price = deal["price_byn"]
    market = deal["market_price"]
    discount_byn = deal["discount_byn"]
    discount_pct = deal["discount_pct"]
    analysis = deal["analysis"]
    url = deal["url"]
    
    badges = []
    if deal["is_suspiciously_cheap"]:
        badges.append("🚨 <b>ВНИМАНИЕ: Слишком низкая цена (риск предоплаты)! Только личная встреча!</b>")
    
    badges.append(f"🏷️ <b>Бренд:</b> {brand}")

    if analysis.get("memory"):
        badges.append(f"💾 <b>Память:</b> {analysis['memory']}")

    if analysis.get("battery_health"):
        badges.append(f"🔋 <b>АКБ:</b> {analysis['battery_health']}%")

    if analysis.get("kit_details"):
        kit_str = ", ".join(analysis["kit_details"])
        badges.append(f"📦 <b>Комплектация:</b> {kit_str} (повод сбить цену на 20-40 BYN)")

    if analysis["has_minor_defects"]:
        defects_str = ", ".join(analysis["minor_defects"]) if analysis["minor_defects"] else "требует внимания"
        badges.append(f"⚠️ <b>Нюансы:</b> {defects_str}")
    else:
        badges.append("✨ <b>Состояние:</b> Без критичных дефектов")

    if market:
        market_block = (
            f"📈 <b>Ориентир рынка:</b> ~<code>{market} BYN</code>\n"
            f"🎁 <b>Зазор / Профит:</b> ~<code>{discount_byn:.0f} BYN</code> (<b>-{discount_pct}%</b>)\n"
        )
    else:
        market_block = "📈 <b>Рынок:</b> <i>Индивидуальная оценка</i>\n"

    badges_formatted = "\n".join(badges)
    card_text = (
        f"🔥 <b>НАЙДЕН ТЕЛЕФОН В ВАШЕМ БЮДЖЕТЕ!</b>\n\n"
        f"📱 <b>{title}</b>\n"
        f"💵 <b>Цена продавца:</b> <code>{price:.0f} BYN</code>\n"
        f"{market_block}"
        f"{badges_formatted}\n\n"
        f"📝 <b>Из описания:</b> <i>{deal['body_preview']}</i>\n"
    )

    action_buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть на Kufar", url=url)]
    ])

    try:
        await bot.send_message(user_id, card_text, reply_markup=action_buttons, disable_web_page_preview=False)
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

# =====================================================================
# ФОНОВЫЙ ВОРКЕР МОНИТОРИНГА
# =====================================================================
async def background_monitoring_loop():
    logger.info("Фоновый воркер запущен. Ожидание первого цикла...")
    await asyncio.sleep(5)
    
    while True:
        try:
            raw_ads = await scraper.fetch_latest_phones()
            active_users = db.get_active_users()
            
            if active_users and raw_ads:
                for ad in raw_ads:
                    ad_id = str(ad.get("ad_id", ""))
                    if not ad_id or db.is_ad_seen(ad_id):
                        continue
                    
                    deal = evaluate_deal(ad)
                    db.mark_ad_seen(ad_id, float(ad.get("price_byn", 0) or 0) / 100.0)

                    if not deal:
                        continue

                    for user in active_users:
                        user_id = user["user_id"]
                        min_b = user["min_budget"]
                        max_b = user["max_budget"]
                        allow_defects = user["allow_minor_defects"]
                        user_brands = user.get("brands", "Все")

                        # Фильтр по цене
                        if not (min_b <= deal["price_byn"] <= max_b):
                            continue

                        # Фильтр по платформе
                        if user_brands == "Только Android" and not deal["is_android"]:
                            continue
                        if user_brands == "Только Apple" and deal["is_android"]:
                            continue

                        # Фильтр по нюансам (комплект без коробки/зарядки НЕ считается дефектом)
                        if deal["analysis"]["has_minor_defects"] and not allow_defects:
                            continue

                        await send_deal_notification(deal, user_id)
                        await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Ошибка в цикле мониторинга: {e}", exc_info=True)

        # Безопасный интервал (75-90 секунд с рандомизацией)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS + random.randint(3, 15))

# =====================================================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER (HEALTH-CHECK)
# =====================================================================
async def handle_health_check(request: web.Request) -> web.Response:
    return web.Response(text="Kufar Phone Radar is alive and running!", status=200)

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health-check сервер запущен на порту {PORT}")

# =====================================================================
# ТОЧКА ВХОДА
# =====================================================================
async def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("ОШИБКА: TELEGRAM_BOT_TOKEN не задан!")
        return

    logger.info(f"Запуск приватного бота для пользователя ID {ALLOWED_USER_ID}...")
    await start_health_server()
    asyncio.create_task(background_monitoring_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
