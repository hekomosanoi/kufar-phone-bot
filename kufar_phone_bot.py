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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "ВАШ_ТЕЛЕГРАМ_ТОКЕН_ЗДЕСЬ")
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

# Базовые ориентиры рынка (BYN) для ходовых моделей
BENCHMARK_PRICES = {
    # Apple iPhone
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
    
    # Samsung Galaxy
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

    # Xiaomi / Redmi / Poco
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

    # Google Pixel
    "pixel 6a": 620,
    "pixel 6": 720,
    "pixel 6 pro": 900,
    "pixel 7a": 850,
    "pixel 7": 1050,
    "pixel 7 pro": 1350,
    "pixel 8a": 1250,
    "pixel 8": 1600,
    "pixel 8 pro": 2100,

    # Realme / Honor / Huawei
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

    # Infinix / Tecno / OnePlus / Nothing
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

# Строгие стоп-слова: опасные неликвиды, подделки, кирпичи, блокировки Apple и Android
STRICT_STOP_WORDS = [
    # Apple блокировки
    "icloud", "айклауд", "байпас", "bypass", "копия", "реплика", "фейк",
    # Android блокировки (Google, Mi, Knox, Лизинг)
    "frp", "google lock", "гугл аккаунт", "аккаунт гугл", "ми аккаунт", "mi account", 
    "mi cloud", "ми клауд", "huawei id", "хуавей ид", "samsung account", "knox guard", 
    "knox", "лизинг", "яндекс плюс", "сплит", "подписк", "заблокирован оператором", "sim lock",
    "ldu", "live demo", "демо образец",
    # Аппаратные фатальные дефекты
    "на запчасти", "под восстановление", "не включается", "залит", "утопленник", 
    "mdm", "демо", "demo", "не видит сеть", "без сети", "не звонит", "кирпич",
    "отвал", "реболл", "bootloop", "бутлуп", "вечный ребут", "перезагружается сам", 
    "зеленая полоса", "зеленые полосы", "полоса на экране", "черное пятно"
]

# Отсутствие элементов комплекта — ЭТО НЕ ДЕФЕКТ! 
# Такие объявления ВСЕГДА разрешены и не блокируются, но мы выделяем это в карточке для торга!
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

# Допустимые мелкие дефекты корпуса/экрана/АКБ (телефон полностью функционален)
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
    """Определяет бренд телефона и проверяет, является ли он Android."""
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
    """Извлекает объем накопителя и ОЗУ (например: 8/256GB, 128 ГБ)"""
    combo_match = re.search(r'\b(\d{1,2})\s*/\s*(\d{2,4})\s*(?:gb|гб)?\b', text, re.IGNORECASE)
    if combo_match:
        return f"{combo_match.group(1)}/{combo_match.group(2)} GB"
    
    rom_match = re.search(r'\b(32|64|128|256|512|1024|1tb|1тб)\s*(?:gb|гб)?\b', text, re.IGNORECASE)
    if rom_match:
        return f"{rom_match.group(1).upper()} GB"
    return None

def analyze_phone_text(title: str, body: str) -> dict:
    """Анализирует текст на риски, дефекты, комплект и батарею."""
    full_text = f"{title} {body}".lower()
    
    # 1. Проверка на фатальные стоп-слова (высокий риск!)
    critical_triggers = []
    for stop_word in STRICT_STOP_WORDS:
        if stop_word in full_text:
            critical_triggers.append(stop_word)
            
    # 2. Проверка на комплектацию (коробка, зарядка) — ЭТО НЕ ДЕФЕКТ!
    kit_details = []
    for kit_key, kit_desc in KIT_NUANCES.items():
        if kit_key in full_text and kit_desc not in kit_details:
            kit_details.append(kit_desc)

    # 3. Проверка на мелкие физические нюансы
    found_defects = []
    for defect_key, defect_desc in MINOR_DEFECTS.items():
        if defect_key in full_text and defect_desc not in found_defects:
            found_defects.append(defect_desc)

    # 4. Поиск состояния батареи
    battery_match = re.search(r'(?:акб|батаре[яе]|емкость|ёмкость)\D*?(\d{2,3})\s*%', full_text)
    battery_health = int(battery_match.group(1)) if battery_match else None

    # 5. Объем памяти
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
    """Определяет ориентировочную рыночную цену по совпадению модели из базы бенчмарков."""
    cleaned_title = title.lower()
    for model_key, estimated_price in BENCHMARK_PRICES.items():
        tokens = model_key.split()
        if all(token in cleaned_title for token in tokens):
            return estimated_price
    return None

class KufarScraper:
    """Клиент для обращения к публичному Search API Куфара"""
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
            "cat": "17010",        # Категория: Мобильные телефоны на Kufar
            "sort": "lst.d",       # Сортировка: самые свежие
            "size": "30",          # Количество на странице
            "typ": "let"           # Частные объявления + компании
        }
        
        try:
            async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                async with session.get(self.BASE_URL, params=params, timeout=15) as response:
                    if response.status != 200:
                        logger.warning(f"Kufar API ответил статусом: {response.status}")
                        return []
                    data = await response.json()
                    return data.get("ads", [])
        except Exception as e:
            logger.error(f"Ошибка при запросе к Kufar API: {e}")
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
    
    # Если обнаружены блокировки или кирпичи — сразу отсекаем
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

    is_suspiciously_cheap = False
    if market_price and (price_byn < market_price * 0.35):
        is_suspiciously_cheap = True

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
        [
            InlineKeyboardButton(text="🌐 Все телефоны (iOS + Все Android)", callback_data="set_brand_all"),
        ],
        [
            InlineKeyboardButton(text="🤖 Только Android (Все бренды)", callback_data="set_brand_android"),
        ],
        [
            InlineKeyboardButton(text="🍏 Только Apple (iPhone)", callback_data="set_brand_apple"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main_menu"),
        ]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_data = db.get_or_create_user(user_id)
    
    welcome_text = (
        "👋 <b>Добро пожаловать в Kufar Phone Radar!</b>\n\n"
        "Этот бот мониторит Куфар 24/7, отбирая телефоны <b>сильно ниже рынка</b>.\n"
        "🛡 <b>Защита от рисков:</b> бот отсекает заблокированные (iCloud, Google FRP, Mi Account, Knox, лизинг/рассрочки), "
        "копии, битые матрицы, полосы и кирпичи.\n"
        "📦 <b>Комплектация:</b> телефоны <i>без коробки, без зарядки или без документов</i> <b>проходят в фильтр</b> "
        "и отмечаются как повод для торга!\n\n"
        f"🎯 <b>Ваш текущий фильтр:</b>\n"
        f"• Бюджет: <code>{user_data['min_budget']} - {user_data['max_budget']} BYN</code>\n"
        f"• Платформа: <b>{user_data['brands']}</b>\n"
        f"• Допуск мелких дефектов (АКБ, задняя крышка, выгорание): <b>{'ДА' if user_data['allow_minor_defects'] else 'НЕТ (только без нюансов)'}</b>\n\n"
        "Настройте фильтры кнопками ниже:"
    )
    await message.answer(welcome_text, reply_markup=build_budget_keyboard())

@dp.message(Command("budget"))
async def cmd_set_custom_budget(message: types.Message):
    parts = message.text.strip().split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("⚠️ Формат команды: <code>/budget МИН 
