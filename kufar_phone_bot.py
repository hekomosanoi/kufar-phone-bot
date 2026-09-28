import asyncio
import logging
import sqlite3
import re
import json
import os
import aiohttp
from aiohttp import web
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# =====================================================================
# НАСТРОЙКИ И ТОКЕНЫ
# =====================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PORT = int(os.getenv("PORT", 8080))
CHECK_INTERVAL_SECONDS = 75
DB_PATH = "kufar_phones.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("KufarPhoneBot")

class Database:
    """Модуль работы с локальной базой данных SQLite"""
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
                    min_discount_percent INTEGER DEFAULT 20,
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
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("""
                    INSERT INTO users (user_id, min_budget, max_budget, min_discount_percent, allow_minor_defects, brands, is_active)
                    VALUES (?, 100, 800, 20, 1, 'Все', 1)
                """, (user_id,))
                conn.commit()
                return {
                    "user_id": user_id, "min_budget": 100, "max_budget": 800,
                    "min_discount_percent": 20, "allow_minor_defects": 1,
                    "brands": "Все", "is_active": 1
                }
            return {
                "user_id": row[0], "min_budget": row[1], "max_budget": row[2],
                "min_discount_percent": row[3], "allow_minor_defects": row[4],
                "brands": row[5], "is_active": row[6]
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
            cursor.execute("SELECT user_id, min_budget, max_budget, min_discount_percent, allow_minor_defects, brands, is_active FROM users WHERE is_active = 1")
            rows = cursor.fetchall()
            return [
                {
                    "user_id": r[0], "min_budget": r[1], "max_budget": r[2],
                    "min_discount_percent": r[3], "allow_minor_defects": r[4],
                    "brands": r[5], "is_active": r[6]
                }
                for r in rows
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

db = Database()

# Базовые ориентиры цен рынка (BYN)
BENCHMARK_PRICES = {
    "iphone 11 64": 650,
    "iphone 11 128": 750,
    "iphone 12 mini": 800,
    "iphone 12 64": 950,
    "iphone 12 128": 1100,
    "iphone 12 pro 128": 1350,
    "iphone 13 mini": 1250,
    "iphone 13 128": 1500,
    "iphone 13 pro 128": 1850,
    "iphone 13 pro max 128": 2100,
    "iphone 14 128": 1850,
    "iphone 14 pro 128": 2350,
    "iphone 15 128": 2250,
    "iphone 15 pro 128": 2900,
    "iphone xr 64": 450,
    "iphone xr 128": 520,
    "iphone xs 64": 380,
    "iphone se 2020": 400,
    "iphone se 2022": 600,
    "s20 fe": 550,
    "s21 fe": 750,
    "s21 ultra": 1100,
    "s21": 850,
    "s22": 1100,
    "s22 ultra": 1600,
    "s23": 1550,
    "s23 ultra": 2200,
    "s24": 2100,
    "s24 ultra": 3100,
    "a52": 420,
    "a53": 520,
    "a54": 680,
    "a55": 850,
    "a34": 520,
    "a35": 650,
    "a24": 420,
    "a25": 550,
    "redmi note 10 pro": 350,
    "redmi note 11 pro": 450,
    "redmi note 12 pro": 580,
    "redmi note 13 pro": 750,
    "redmi 12": 320,
    "redmi 13c": 300,
    "poco x3 pro": 320,
    "poco x4 pro": 460,
    "poco x5 pro": 620,
    "poco x6 pro": 850,
    "poco f3": 480,
    "poco f4": 650,
    "poco f5": 900,
    "poco f6": 1200,
    "xiaomi 12": 850,
    "xiaomi 13": 1400,
    "xiaomi 13t": 1150,
    "xiaomi 14": 1900,
    "pixel 6a": 620,
    "pixel 6": 720,
    "pixel 6 pro": 900,
    "pixel 7a": 850,
    "pixel 7": 1050,
    "pixel 7 pro": 1350,
    "pixel 8a": 1250,
    "pixel 8": 1600,
    "pixel 8 pro": 2100,
    "realme gt neo": 600,
    "realme 10": 420,
    "realme 11 pro": 650,
    "realme 12 pro": 850,
    "honor 50": 450,
    "honor 70": 650,
    "honor 90": 850,
    "honor 200": 1100,
    "honor x9b": 600,
    "huawei pura 70": 1600,
    "huawei nova 11": 650,
    "infinix note 30": 400,
    "infinix note 40": 550,
    "infinix zero 30": 650,
    "tecno camon 20": 420,
    "tecno camon 30": 600,
    "tecno pova 5": 380,
    "tecno pova 6": 550,
    "oneplus 9": 700,
    "oneplus 10 pro": 1050,
    "oneplus 11": 1500,
    "oneplus 12": 2200,
    "oneplus nord ce": 500,
    "nothing phone 1": 850,
    "nothing phone 2": 1400,
    "nothing phone 2a": 950
}

# Строгие стоп-слова
STRICT_STOP_WORDS = [
    "icloud", "айклауд", "байпас", "bypass", "копия", "реплика", "фейк",
    "frp", "google lock", "гугл аккаунт", "аккаунт гугл", "ми аккаунт", "mi account", 
    "mi cloud", "ми клауд", "huawei id", "хуавей ид", "samsung account", "knox guard", 
    "knox", "лизинг", "яндекс плюс", "сплит", "подписк", "заблокирован оператором", "sim lock",
    "ldu", "live demo", "демо образец",
    "на запчасти", "под восстановление", "не включается", "залит", "утопленник", 
    "mdm", "демо", "demo", "не видит сеть", "без сети", "не звонит", "кирпич",
    "отвал", "реболл", "bootloop", "бутлуп", "вечный ребут", "перезагружается сам", 
    "зеленая полоса", "зеленые полосы", "полоса на экране", "черное пятно"
]

KIT_NUANCES = {
    "без коробк": "Без коробки",
    "нет коробк": "Без коробки",
    "без комплект": "Только телефон (без комплекта)",
    "нет комплект": "Без комплекта",
    "без зарядк": "Без зарядного устройства / блока",
    "нет зарядк": "Без зарядного блока",
    "без блок": "Без блока питания",
    "без шнур": "Без кабеля",
    "без провод": "Без кабеля",
    "без чек": "Без чека/документов",
    "нет чек": "Без чека",
    "только телефон": "Только сам телефон (без аксессуаров)"
}

MINOR_DEFECTS = {
    "акб": "Слабый АКБ / требует замены (расход ~45-75 BYN)",
    "батаре": "Износ аккумулятора",
    "трещина сзади": "Треснула задняя крышка (чехол решает, ремонт ~35-50 BYN)",
    "задней крышк": "Царапины/трещина крышки",
    "царапин": "Следы эксплуатации / царапины",
    "потертост": "Потертости корпуса",
    "потёртост": "Потертости корпуса",
    "скол": "Мелкие сколы по корпусу / рамке",
    "не работает truetone": "Нет TrueTone (менялся дисплей, но рабочий)",
    "не работает трутон": "Нет TrueTone (менялся дисплей)",
    "выгоран": "Выгорание AMOLED экрана / остаточные значки (отличный повод для торга!)",
    "остаточное изображени": "Остаточное изображение на дисплее",
    "китайск": "Китайская версия (CN / перешит)"
}

def detect_brand(title: str, body: str) -> Tuple[str, bool]:
    text = f"{title} {body}".lower()
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
        ("Nothing", ["nothing phone", "насинг"]),
        ("Motorola", ["motorola", "моторола", "moto"]),
        ("Vivo / iQOO", ["vivo", "виво", "iqoo"]),
        ("Oppo", ["oppo", "оппо"]),
        ("Sony", ["sony", "сони", "xperia"]),
        ("ZTE / Nubia", ["zte", "nubia", "blade"]),
        ("Asus", ["asus", "rog phone", "zenfone"]),
        ("Meizu", ["meizu", "мейзу"])
    ]

    for brand_name, keywords in android_brands:
        if any(kw in text for kw in keywords):
            return brand_name, True

    return "Другой Android", True

def extract_memory_info(text: str) -> Optional[str]:
    combo_match = re.search(r'\b(\d{1,2})\s*/\s*(\d{2,4})\s*(?:gb|гб)?\b', text, re.IGNORECASE)
    if combo_match:
        return f"{combo_match.group(1)}/{combo_match.group(2)} GB"
    
    rom_match = re.search(r'\b(32|64|128|256|512|1024|1tb|1тб)\s*(?:gb|гб)?\b', text, re.IGNORECASE)
    if rom_match:
        return f"{rom_match.group(1).upper()} GB"
    return None

def analyze_phone_text(title: str, body: str) -> dict:
    full_text = f"{title} {body}".lower()
    
    critical_triggers = []
    for stop_word in STRICT_STOP_WORDS:
        if stop_word in full_text:
            critical_triggers.append(stop_word)
            
    kit_details = []
    for kit_key, kit_desc in KIT_NUANCES.items():
        if kit_key in full_text and kit_desc not in kit_details:
            kit_details.append(kit_desc)

    found_defects = []
    for defect_key, defect_desc in MINOR_DEFECTS.items():
        if defect_key in full_text and defect_desc not in found_defects:
            found_defects.append(defect_desc)

    battery_match = re.search(r'(?:акб|батаре[яе]|емкость|ёмкость)\D*?(\d{2,3})\s*%', full_text)
    battery_health = int(battery_match.group(1)) if battery_match else None
    memory = extract_memory_info(f"{title} {body}")

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
    cleaned_title = title.lower()
    for model_key, estimated_price in BENCHMARK_PRICES.items():
        tokens = model_key.split()
        if all(token in cleaned_title for token in tokens):
            return estimated_price
    return None

class KufarScraper:
    BASE_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.kufar.by",
        "Referer": "https://www.kufar.by/l/mobilnye-telefony"
    }

    async def fetch_latest_phones(self) -> List[dict]:
        params = {
            "cat": "17010",
            "sort": "lst.d",
            "size": "30",
            "typ": "let"
        }
        try:
            async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                async with session.get(self.BASE_URL, params=params, timeout=15) as response:
                    if response.status != 200:
                        logger.warning(f"Kufar API status: {response.status}")
                        return []
                    data = await response.json()
                    return data.get("ads", [])
        except Exception as e:
            logger.error(f"Error fetching Kufar ads: {e}")
            return []

scraper = KufarScraper()

def evaluate_deal(ad: dict) -> Optional[dict]:
    ad_id = str(ad.get("ad_id", ""))
    subject = ad.get("subject", "").strip()
    body = ad.get("body", "").strip()
    price_byn_raw = ad.get("price_byn", "0")
    
    try:
        price_byn = float(price_byn_raw) / 100.0 if float(price_byn_raw) > 10000 else float(price_byn_raw)
    except (ValueError, TypeError):
        return None

    if price_byn < 30:
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
        "body_preview": (body[:180] + "...") if len(body) > 180 else body
    }

dp = Dispatcher()
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

def build_budget_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="💵 До 400 BYN (Бюджетники)", callback_data="set_budget_50_400"),
            InlineKeyboardButton(text="💎 400 - 900 BYN (Топ-флип)", callback_data="set_budget_400_900"),
        ],
        [
            InlineKeyboardButton(text="🚀 900 - 1800 BYN (Флагманы)", callback_data="set_budget_900_1800"),
            InlineKeyboardButton(text="🔥 Без лимита (100 - 5000)", callback_data="set_budget_100_5000"),
        ],
        [
            InlineKeyboardButton(text="🤖 Выбор ОС / Брендов", callback_data="choose_brands_menu"),
            InlineKeyboardButton(text="⚙️ Мелкие дефекты: ВКЛ/ВЫКЛ", callback_data="toggle_defects"),
        ],
        [
            InlineKeyboardButton(text="📊 Мой профиль и фильтры", callback_data="show_profile"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_brands_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Все телефоны (iOS + Android)", callback_data="set_brand_all")],
        [InlineKeyboardButton(text="🤖 Только Android (Все бренды)", callback_data="set_brand_android")],
        [InlineKeyboardButton(text="🍏 Только Apple (iPhone)", callback_data="set_brand_apple")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main_menu")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_data = db.get_or_create_user(user_id)
    defects_txt = "ДА" if user_data["allow_minor_defects"] else "НЕТ (только без нюансов)"
    welcome_text = (
        "👋 <b>Добро пожаловать в Kufar Phone Radar!</b>\n\n"
        "Этот бот мониторит Куфар 24/7, отбирая телефоны <b>сильно ниже рынка</b>.\n"
        "🛡 <b>Защита от рисков:</b> бот отсекает блокировки (iCloud, FRP, Mi Account, Knox, рассрочки) и кирпичи.\n"
        "📦 <b>Комплектация:</b> телефоны без коробки или зарядки <b>проходят в фильтр</b> и отмечаются для торга!\n\n"
        f"🎯 <b>Ваш текущий фильтр:</b>\n"
        f"• Бюджет: <code>{user_data['min_budget']} - {user_data['max_budget']} BYN</code>\n"
        f"• Платформа: <b>{user_data['brands']}</b>\n"
        f"• Допуск мелких дефектов: <b>{defects_txt}</b>\n\n"
        "Настройте фильтры кнопками ниже:"
    )
    await message.answer(welcome_text, reply_markup=build_budget_keyboard())

@dp.message(Command("budget"))
async def cmd_set_custom_budget(message: types.Message):
    parts = message.text.strip().split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        msg = "⚠️ Формат команды: <code>/budget МИН МАКС</code>\nПример: <code>/budget 200 650</code>"
        await message.answer(msg)
        return
        
    min_b, max_b = int(parts[1]), int(parts[2])
    if min_b >= max_b:
        await message.answer("⚠️ Минимальный бюджет должен быть меньше максимального!")
        return

    db.update_user_budget(message.from_user.id, min_b, max_b)
    await message.answer(f"✅ <b>Бюджет успешно обновлен:</b> от <b>{min_b} BYN</b> до <b>{max_b} BYN</b>.")

@dp.callback_query(F.data.startswith("set_budget_"))
async def callback_budget_preset(callback: CallbackQuery):
    _, _, min_str, max_str = callback.data.split("_")
    min_b, max_b = int(min_str), int(max_str)
    db.update_user_budget(callback.from_user.id, min_b, max_b)
    await callback.answer(f"Бюджет: {min_b} - {max_b} BYN")
    await callback.message.edit_text(
        f"✅ <b>Установлен новый бюджет поиска:</b> <code>{min_b} - {max_b} BYN</code>\n"
        "Бот будет присылать предложения в этом диапазоне!",
        reply_markup=build_budget_keyboard()
    )

@dp.callback_query(F.data == "toggle_defects")
async def callback_toggle_defects(callback: CallbackQuery):
    user = db.get_or_create_user(callback.from_user.id)
    new_state = 0 if user["allow_minor_defects"] == 1 else 1
    db.update_user_defects(callback.from_user.id, new_state)
    state_str = "РАЗРЕШЕНЫ (АКБ/крышка/выгорание)" if new_state else "ЗАПРЕЩЕНЫ (только без нюансов)"
    await callback.answer(f"Мелкие дефекты: {state_str}")
    await callback.message.edit_text(
        f"⚙️ <b>Параметр обновлен!</b>\nДопустимость физических дефектов: <b>{state_str}</b>.\n"
        "<i>Телефоны без коробки или зарядки разрешены всегда.</i>",
        reply_markup=build_budget_keyboard()
    )

@dp.callback_query(F.data == "choose_brands_menu")
async def callback_choose_brands_menu(callback: CallbackQuery):
    user = db.get_or_create_user(callback.from_user.id)
    await callback.message.edit_text(
        f"🤖 <b>Настройка брендов и платформы</b>\n\n"
        f"Сейчас отслеживаются: <b>{user['brands']}</b>\n\n"
        "Выберите интересующую платформу:",
        reply_markup=build_brands_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "set_brand_all")
async def callback_set_brand_all(callback: CallbackQuery):
    db.update_user_brands(callback.from_user.id, "Все (iOS + Android)")
    await callback.answer("Выбраны все платформы")
    await callback.message.edit_text(
        "✅ <b>Мониторинг настроен:</b> ищем <b>ВСЕ</b> телефоны (iPhone и абсолютно все Android-бренды).",
        reply_markup=build_budget_keyboard()
    )

@dp.callback_query(F.data == "set_brand_android")
async def callback_set_brand_android(callback: CallbackQuery):
    db.update_user_brands(callback.from_user.id, "Только Android")
    await callback.answer("Выбран только Android")
    await callback.message.edit_text(
        "🤖 <b>Мониторинг настроен:</b> ищем <b>ВСЕ модели Android</b> "
        "(Samsung, Xiaomi, Poco, Pixel, Honor, OnePlus, Tecno, Infinix, Realme и др.).",
        reply_markup=build_budget_keyboard()
    )

@dp.callback_query(F.data == "set_brand_apple")
async def callback_set_brand_apple(callback: CallbackQuery):
    db.update_user_brands(callback.from_user.id, "Только Apple")
    await callback.answer("Выбран только Apple")
    await callback.message.edit_text(
        "🍏 <b>Мониторинг настроен:</b> ищем только <b>Apple iPhone</b>.",
        reply_markup=build_budget_keyboard()
    )

@dp.callback_query(F.data == "back_to_main_menu")
async def callback_back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("⚙️ <b>Панель управления поиском:</b>", reply_markup=build_budget_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "show_profile")
async def callback_profile(callback: CallbackQuery):
    user = db.get_or_create_user(callback.from_user.id)
    text = (
        f"👤 <b>Ваш профиль искателя:</b>\n\n"
        f"💰 Бюджет: <code>{user['min_budget']} - {user['max_budget']} BYN</code>\n"
        f"📱 Платформа: <b>{user['brands']}</b>\n"
        f"🔧 Мелкие дефекты: <code>{'Включены' if user['allow_minor_defects'] else 'Отключены'}</code>\n"
        f"📦 Без коробки/зарядки: <b>РАЗРЕШЕНО ВСЕГДА</b>\n"
        f"📡 Мониторинг: <b>АКТИВЕН 24/7</b>"
    )
    await callback.message.answer(text, reply_markup=build_budget_keyboard())
    await callback.answer()

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
        badges.append("🚨 <b>ВНИМАНИЕ: Слишком низкая цена (риск скама/предоплаты)! Только личная встреча!</b>")
    
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
        badges.append("✨ <b>Состояние:</b> Без критичных дефектов в описании")

    if market:
        market_block = (
            f"📈 <b>Ориентир рынка:</b> ~<code>{market} BYN</code>\n"
            f"🎁 <b>Зазор / Профит:</b> ~<code>{discount_byn:.0f} BYN</code> (<b>-{discount_pct}%</b>)\n"
        )
    else:
        market_block = "📈 <b>Рынок:</b> <i>Индивидуальная оценка (модель/комплект)</i>\n"

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

async def background_monitoring_loop():
    logger.info("Фоновый воркер запущен. Ожидание объявлений...")
    await asyncio.sleep(5)
    
    while True:
        try:
            raw_ads = await scraper.fetch_latest_phones()
            active_users = db.get_active_users()
            
            if not active_users:
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                continue

            for ad in raw_ads:
                ad_id = str(ad.get("ad_id", ""))
                if not ad_id or db.is_ad_seen(ad_id):
                    continue
                
                deal = evaluate_deal(ad)
                db.mark_ad_seen(ad_id, float(ad.get("price_byn", 0)) / 100.0)

                if not deal:
                    continue

                for user in active_users:
                    user_id = user["user_id"]
                    min_b = user["min_budget"]
                    max_b = user["max_budget"]
                    allow_defects = user["allow_minor_defects"]
                    user_brands = user.get("brands", "Все")

                    # Фильтр по бюджету
                    if not (min_b <= deal["price_byn"] <= max_b):
                        continue

                    # Фильтр по платформе
                    if user_brands == "Только Android" and not deal["is_android"]:
                        continue
                    if user_brands == "Только Apple" and deal["is_android"]:
                        continue

                    # Фильтр по дефектам (отсутствие коробки/зарядки НЕ считается дефектом)
                    if deal["analysis"]["has_minor_defects"] and not allow_defects:
                        continue

                    await send_deal_notification(deal, user_id)
                    await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Ошибка в фоновом цикле мониторинга: {e}", exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

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

async def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("ОШИБКА: TELEGRAM_BOT_TOKEN не задан в переменных окружения!")
        return

    logger.info("Запуск Telegram бота и мониторинга Kufar...")
    await start_health_server()
    asyncio.create_task(background_monitoring_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
