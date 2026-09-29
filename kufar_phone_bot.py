import asyncio
import json
import logging
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("kufar_hunter")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEFAULT_ADMIN_CHAT_ID = os.getenv("TARGET_CHAT_ID", "7805601948")
WEB_PORT = int(os.getenv("PORT", 10000))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", 25))
SUBSCRIBERS_FILE = "subscribers.json"

KUFAR_SEARCH_API_URL = "https://api.kufar.by/search-api/v1/search/rendered-paginated"

DEFAULT_PARAMS: Dict[str, str] = {
    "cat": "17010",          # Мобильные телефоны
    "sort": "lst.d",         # Свежие объявления первыми
    "size": "30",            # Проверять последние 30 объявлений
}

# (Регулярка названия, Отображаемое имя, Максимальная цена выкупа BYN, Оценка рынка BYN)
PRICE_MATRIX: List[Tuple[re.Pattern, str, float, float]] = [
    # ==================== APPLE IPHONE ====================
    (re.compile(r"\b16\s*pro\s*max\b", re.I), "iPhone 16 Pro Max", 2800, 4200),
    (re.compile(r"\b16\s*pro\b", re.I), "iPhone 16 Pro", 2400, 3600),
    (re.compile(r"\biphone\s*16\s*plus\b", re.I), "iPhone 16 Plus", 2000, 3100),
    (re.compile(r"\biphone\s*16\b|\bайфон\s*16\b", re.I), "iPhone 16", 1800, 2800),

    (re.compile(r"\b15\s*pro\s*max\b", re.I), "iPhone 15 Pro Max", 1950, 3100),
    (re.compile(r"\b15\s*pro\b", re.I), "iPhone 15 Pro", 1650, 2600),
    (re.compile(r"\b15\s*plus\b", re.I), "iPhone 15 Plus", 1350, 2100),
    (re.compile(r"\biphone\s*15\b|\bайфон\s*15\b", re.I), "iPhone 15", 1250, 1950),

    (re.compile(r"\b14\s*pro\s*max\b", re.I), "iPhone 14 Pro Max", 1550, 2500),
    (re.compile(r"\b14\s*pro\b", re.I), "iPhone 14 Pro", 1350, 2100),
    (re.compile(r"\b14\s*plus\b", re.I), "iPhone 14 Plus", 1050, 1650),
    (re.compile(r"\biphone\s*14\b|\bайфон\s*14\b", re.I), "iPhone 14", 950, 1550),

    (re.compile(r"\b13\s*pro\s*max\b", re.I), "iPhone 13 Pro Max", 1200, 1950),
    (re.compile(r"\b13\s*pro\b", re.I), "iPhone 13 Pro", 950, 1600),
    (re.compile(r"\b13\s*mini\b", re.I), "iPhone 13 mini", 650, 1150),
    (re.compile(r"\biphone\s*13\b|\bайфон\s*13\b", re.I), "iPhone 13", 750, 1350),

    (re.compile(r"\b12\s*pro\s*max\b", re.I), "iPhone 12 Pro Max", 800, 1350),
    (re.compile(r"\b12\s*pro\b", re.I), "iPhone 12 Pro", 650, 1150),
    (re.compile(r"\b12\s*mini\b", re.I), "iPhone 12 mini", 400, 750),
    (re.compile(r"\biphone\s*12\b|\bайфон\s*12\b", re.I), "iPhone 12", 500, 950),

    (re.compile(r"\b11\s*pro\s*max\b", re.I), "iPhone 11 Pro Max", 550, 950),
    (re.compile(r"\b11\s*pro\b", re.I), "iPhone 11 Pro", 450, 800),
    (re.compile(r"\biphone\s*11\b|\bайфон\s*11\b", re.I), "iPhone 11", 350, 650),

    (re.compile(r"\biphone\s*se\s*(2022|3)\b", re.I), "iPhone SE 2022", 350, 680),
    (re.compile(r"\biphone\s*se\s*(2020|2)\b", re.I), "iPhone SE 2020", 220, 420),
    (re.compile(r"\b(iphone\s*)?(xr|xs\s*max|xs)\b", re.I), "iPhone XR/XS", 250, 480),

    # ==================== SAMSUNG GALAXY ====================
    # Флагманы S-серии
    (re.compile(r"\bs24\s*ultra\b", re.I), "Samsung S24 Ultra", 1850, 3100),
    (re.compile(r"\bs24\s*\+\b|\bs24\s*plus\b", re.I), "Samsung S24+", 1400, 2300),
    (re.compile(r"\bs24\b", re.I), "Samsung S24", 1100, 1900),

    (re.compile(r"\bs23\s*ultra\b", re.I), "Samsung S23 Ultra", 1300, 2200),
    (re.compile(r"\bs23\s*\+\b|\bs23\s*plus\b", re.I), "Samsung S23+", 1050, 1750),
    (re.compile(r"\bs23\s*fe\b", re.I), "Samsung S23 FE", 650, 1150),
    (re.compile(r"\bs23\b", re.I), "Samsung S23", 850, 1450),

    (re.compile(r"\bs22\s*ultra\b", re.I), "Samsung S22 Ultra", 900, 1600),
    (re.compile(r"\bs22\s*\+\b|\bs22\s*plus\b", re.I), "Samsung S22+", 750, 1300),
    (re.compile(r"\bs22\b", re.I), "Samsung S22", 600, 1100),

    (re.compile(r"\bs21\s*ultra\b", re.I), "Samsung S21 Ultra", 650, 1100),
    (re.compile(r"\bs21\s*fe\b", re.I), "Samsung S21 FE", 420, 750),
    (re.compile(r"\bs21\b", re.I), "Samsung S21", 450, 800),

    (re.compile(r"\bs20\s*fe\b", re.I), "Samsung S20 FE", 280, 520),
    (re.compile(r"\bnote\s*20\s*ultra\b", re.I), "Samsung Note 20 Ultra", 550, 950),

    # Складные Z Flip и Z Fold
    (re.compile(r"\bz\s*flip\s*5\b", re.I), "Samsung Z Flip 5", 900, 1600),
    (re.compile(r"\bz\s*flip\s*4\b", re.I), "Samsung Z Flip 4", 550, 1050),
    (re.compile(r"\bz\s*flip\s*3\b", re.I), "Samsung Z Flip 3", 380, 700),
    (re.compile(r"\bz\s*fold\s*5\b", re.I), "Samsung Z Fold 5", 1600, 2700),
    (re.compile(r"\bz\s*fold\s*4\b", re.I), "Samsung Z Fold 4", 1100, 1900),

    # Ходовая A-серия Samsung
    (re.compile(r"\ba55\b", re.I), "Samsung A55", 520, 920),
    (re.compile(r"\ba54\b", re.I), "Samsung A54", 390, 720),
    (re.compile(r"\ba53\b", re.I), "Samsung A53", 280, 520),
    (re.compile(r"\ba52\b", re.I), "Samsung A52", 220, 420),
    (re.compile(r"\ba35\b", re.I), "Samsung A35", 420, 750),
    (re.compile(r"\ba34\b", re.I), "Samsung A34", 310, 580),

    # ==================== XIAOMI / REDMI / POCO ====================
    # Флагманская линейка Xiaomi
    (re.compile(r"\bxiaomi\s*14\s*ultra\b", re.I), "Xiaomi 14 Ultra", 1700, 2800),
    (re.compile(r"\bxiaomi\s*14\s*pro\b", re.I), "Xiaomi 14 Pro", 1400, 2400),
    (re.compile(r"\bxiaomi\s*14\b|\bсяоми\s*14\b", re.I), "Xiaomi 14", 1200, 2100),
    (re.compile(r"\bxiaomi\s*13\s*ultra\b", re.I), "Xiaomi 13 Ultra", 1250, 2150),
    (re.compile(r"\bxiaomi\s*13\s*pro\b", re.I), "Xiaomi 13 Pro", 1000, 1800),
    (re.compile(r"\bxiaomi\s*13\s*t\s*pro\b", re.I), "Xiaomi 13T Pro", 850, 1550),
    (re.compile(r"\bxiaomi\s*13\s*t\b", re.I), "Xiaomi 13T", 650, 1200),
    (re.compile(r"\bxiaomi\s*13\b|\bсяоми\s*13\b", re.I), "Xiaomi 13", 800, 1450),
    (re.compile(r"\bxiaomi\s*12\s*t\s*pro\b", re.I), "Xiaomi 12T Pro", 650, 1200),
    (re.compile(r"\bxiaomi\s*12\s*t\b", re.I), "Xiaomi 12T", 500, 950),
    (re.compile(r"\bxiaomi\s*12\s*pro\b", re.I), "Xiaomi 12 Pro", 600, 1100),
    (re.compile(r"\bxiaomi\s*12\b|\bсяоми\s*12\b", re.I), "Xiaomi 12", 450, 850),

    # POCO
    (re.compile(r"\bpoco\s*f6\s*pro\b", re.I), "Poco F6 Pro", 750, 1400),
    (re.compile(r"\bpoco\s*f6\b", re.I), "Poco F6", 600, 1100),
    (re.compile(r"\bpoco\s*f5\s*pro\b", re.I), "Poco F5 Pro", 550, 1050),
    (re.compile(r"\bpoco\s*f5\b", re.I), "Poco F5", 450, 850),
    (re.compile(r"\bpoco\s*x6\s*pro\b", re.I), "Poco X6 Pro", 450, 850),
    (re.compile(r"\bpoco\s*x6\b", re.I), "Poco X6", 350, 650),
    (re.compile(r"\bpoco\s*x5\s*pro\b", re.I), "Poco X5 Pro", 300, 600),

    # REDMI NOTE
    (re.compile(r"\bredmi\s*note\s*13\s*pro\s*\+\b", re.I), "Redmi Note 13 Pro+", 480, 900),
    (re.compile(r"\bredmi\s*note\s*13\s*pro\b", re.I), "Redmi Note 13 Pro", 380, 750),
    (re.compile(r"\bredmi\s*note\s*13\b", re.I), "Redmi Note 13", 260, 500),
    (re.compile(r"\bredmi\s*note\s*12\s*pro\s*\+\b", re.I), "Redmi Note 12 Pro+", 340, 650),
    (re.compile(r"\bredmi\s*note\s*12\s*pro\b", re.I), "Redmi Note 12 Pro", 280, 550),
    (re.compile(r"\bredmi\s*note\s*12\b", re.I), "Redmi Note 12", 200, 400),

    # ==================== GOOGLE PIXEL ====================
    (re.compile(r"\bpixel\s*9\s*pro\b", re.I), "Google Pixel 9 Pro", 1600, 2700),
    (re.compile(r"\bpixel\s*9\b", re.I), "Google Pixel 9", 1300, 2200),
    (re.compile(r"\bpixel\s*8\s*pro\b", re.I), "Google Pixel 8 Pro", 1100, 1900),
    (re.compile(r"\bpixel\s*8a\b", re.I), "Google Pixel 8a", 700, 1250),
    (re.compile(r"\bpixel\s*8\b", re.I), "Google Pixel 8", 850, 1450),
    (re.compile(r"\bpixel\s*7\s*pro\b", re.I), "Google Pixel 7 Pro", 700, 1250),
    (re.compile(r"\bpixel\s*7a\b", re.I), "Google Pixel 7a", 480, 880),
    (re.compile(r"\bpixel\s*7\b", re.I), "Google Pixel 7", 520, 950),
    (re.compile(r"\bpixel\s*6\s*pro\b", re.I), "Google Pixel 6 Pro", 450, 820),
    (re.compile(r"\bpixel\s*6a\b", re.I), "Google Pixel 6a", 330, 600),
    (re.compile(r"\bpixel\s*6\b", re.I), "Google Pixel 6", 360, 680),

    # ==================== ONEPLUS ====================
    (re.compile(r"\boneplus\s*12\b", re.I), "OnePlus 12", 1250, 2200),
    (re.compile(r"\boneplus\s*11\b", re.I), "OnePlus 11", 750, 1400),
    (re.compile(r"\boneplus\s*10\s*pro\b", re.I), "OnePlus 10 Pro", 550, 1050),
    (re.compile(r"\boneplus\s*10\s*t\b", re.I), "OnePlus 10T", 450, 850),
    (re.compile(r"\boneplus\s*9\s*pro\b", re.I), "OnePlus 9 Pro", 420, 800),
    (re.compile(r"\boneplus\s*nord\s*3\b", re.I), "OnePlus Nord 3", 420, 800),

    # ==================== NOTHING PHONE ====================
    (re.compile(r"\bnothing\s*phone\s*\(?2\)?\b", re.I), "Nothing Phone (2)", 750, 1350),
    (re.compile(r"\bnothing\s*phone\s*\(?2a\)?\b", re.I), "Nothing Phone (2a)", 450, 850),
    (re.compile(r"\bnothing\s*phone\s*\(?1\)?\b", re.I), "Nothing Phone (1)", 400, 750),

    # ==================== REALME (GT И PRO СЕРИИ) ====================
    (re.compile(r"\brealme\s*gt\s*neo\s*5\b", re.I), "Realme GT Neo 5", 550, 1050),
    (re.compile(r"\brealme\s*gt\s*5\b", re.I), "Realme GT 5", 650, 1200),
    (re.compile(r"\brealme\s*gt\s*3\b", re.I), "Realme GT 3", 600, 1100),
    (re.compile(r"\brealme\s*12\s*pro\s*\+\b", re.I), "Realme 12 Pro+", 500, 950),
    (re.compile(r"\brealme\s*12\s*pro\b", re.I), "Realme 12 Pro", 420, 800),
    (re.compile(r"\brealme\s*11\s*pro\s*\+\b", re.I), "Realme 11 Pro+", 400, 750),

    # ==================== HONOR ====================
    (re.compile(r"\bhonor\s*magic\s*6\s*pro\b", re.I), "Honor Magic 6 Pro", 1500, 2600),
    (re.compile(r"\bhonor\s*magic\s*5\s*pro\b", re.I), "Honor Magic 5 Pro", 1000, 1800),
    (re.compile(r"\bhonor\s*200\s*pro\b", re.I), "Honor 200 Pro", 800, 1500),
    (re.compile(r"\bhonor\s*200\b", re.I), "Honor 200", 550, 1050),
    (re.compile(r"\bhonor\s*90\b", re.I), "Honor 90", 420, 800),
    (re.compile(r"\bhonor\s*70\b", re.I), "Honor 70", 300, 580),

    # ==================== HUAWEI ====================
    (re.compile(r"\bhuawei\s*pura\s*70\b", re.I), "Huawei Pura 70", 1200, 2100),
    (re.compile(r"\bhuawei\s*p60\s*pro\b", re.I), "Huawei P60 Pro", 950, 1700),
    (re.compile(r"\bhuawei\s*p50\s*pro\b", re.I), "Huawei P50 Pro", 550, 1050),
    (re.compile(r"\bhuawei\s*mate\s*50\s*pro\b", re.I), "Huawei Mate 50 Pro", 750, 1400),
]

# Стоп-слова для исключения запчастей, аксессуаров и блокировок
STOP_WORDS: List[str] = [
    "чехол", "чехлы", "бампер", "стекло", "пленка", "гидрогель",
    "коробка от", "пустая коробка", "запчасти", "на запчасти", "донор",
    "icloud", "айклауд", "заблокирован", "байпас", "bypass", "парол",
    "r-sim", "rsim", "рсим", "демо", "demo", "не включается", "артефакт",
    "рассрочка", "кредит", "разбор", "дисплей от", "плата от"
]

seen_ad_ids: Set[str] = set()
is_first_run: bool = True
subscribers: Set[str] = set()


def load_subscribers() -> Set[str]:
    """Считывает базу Chat ID подписчиков из JSON-файла."""
    loaded = set()
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    loaded = set(str(uid) for uid in data)
        except Exception as err:
            logger.error(f"[STORAGE] Ошибка чтения {SUBSCRIBERS_FILE}: {err}")

    if DEFAULT_ADMIN_CHAT_ID:
        loaded.add(str(DEFAULT_ADMIN_CHAT_ID))
    return loaded


def save_subscribers(subs: Set[str]) -> None:
    """Сохраняет текущий список Chat ID подписчиков."""
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(subs), f, ensure_ascii=False, indent=2)
    except Exception as err:
        logger.error(f"[STORAGE] Ошибка сохранения {SUBSCRIBERS_FILE}: {err}")


subscribers = load_subscribers()
logger.info(f"[STORAGE] Активных получателей при старте: {len(subscribers)} ({subscribers})")


def get_request_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
        "Referer": "https://www.kufar.by/",
        "Origin": "https://www.kufar.by",
    }


def analyze_phone_deal(item: dict) -> Optional[dict]:
    subject = str(item.get("subject", "")).lower()
    body = str(item.get("body", "")).lower()
    full_text = f"{subject} {body}"

    # 1. Проверяем стоп-слова в описании и заголовке
    for word in STOP_WORDS:
        if word in full_text:
            return None

    # 2. Получаем цену в BYN (Kufar отдает цену в копейках)
    raw_price = item.get("price_byn", "0")
    if not str(raw_price).isdigit():
        return None
    price_byn = int(raw_price) / 100.0

    # 3. Сверяем с расширенной матрицей выкупа
    for pattern, model_name, max_price, market_price in PRICE_MATRIX:
        if pattern.search(subject):
            if 70 <= price_byn <= max_price:
                profit = market_price - price_byn
                discount_pct = int(((market_price - price_byn) / market_price) * 100)
                return {
                    "model": model_name,
                    "price_byn": price_byn,
                    "market_price": market_price,
                    "profit": profit,
                    "discount_pct": discount_pct,
                }
            return None

    return None


async def fetch_kufar_ads(session: aiohttp.ClientSession) -> List[dict]:
    try:
        async with session.get(
            KUFAR_SEARCH_API_URL,
            params=DEFAULT_PARAMS,
            headers=get_request_headers(),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status != 200:
                logger.warning(f"[API] Статус ответа Kufar: {response.status}")
                return []
            data = await response.json()
            return data.get("ads", [])
    except Exception as exc:
        logger.error(f"[API ERROR] Ошибка запроса к Kufar: {exc}")
        return []


async def broadcast_sweet_deal(bot: Bot, item: dict, deal: dict) -> None:
    """Рассылает найденный лот всем подключенным пользователям."""
    global subscribers
    if not subscribers:
        logger.warning("[BROADCAST] Нет подписчиков для отправки.")
        return

    ad_id = str(item.get("ad_id", ""))
    subject = item.get("subject", "Без названия")
    ad_link = item.get("ad_link", f"https://www.kufar.by/item/{ad_id}")

    parameters = item.get("ad_parameters", [])
    location = "Беларусь"
    for param in parameters:
        if param.get("p") == "area":
            location = param.get("vl", location)

    message_text = (
        f"🚨 <b>ЖИРНЫЙ ЛОТ ПОД ВЫКУП!</b> 🚨\n\n"
        f"📱 <b>Модель:</b> {deal['model']}\n"
        f"📌 <i>{subject}</i>\n\n"
        f"🔥 <b>Цена продавца:</b> <code>{deal['price_byn']:.0f} BYN</code>\n"
        f"📊 <b>Рыночная цена:</b> ~{deal['market_price']:.0f} BYN\n"
        f"💸 <b>Потенциальный профит:</b> +{deal['profit']:.0f} BYN (-{deal['discount_pct']}%)\n"
        f"📍 <b>Локация:</b> {location}\n\n"
        f"⚠️ <i>Проверяйте телефон лично перед покупкой (оригинальность, блокировки, состояние)!</i>"
    )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡️ СРОЧНО ОТКРЫТЬ НА KUFAR", url=ad_link)]
        ]
    )

    unreachable_users: Set[str] = set()

    for chat_id in list(subscribers):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            await asyncio.sleep(0.05)
        except TelegramForbiddenError:
            unreachable_users.add(chat_id)
            logger.info(f"[BROADCAST] Чат {chat_id} заблокировал бота, удаляем.")
        except Exception as exc:
            logger.error(f"[BROADCAST ERROR] Не удалось отправить {chat_id}: {exc}")

    if unreachable_users:
        subscribers -= unreachable_users
        save_subscribers(subscribers)

    logger.info(
        f"[BROADCAST SENT] Лот {deal['model']} ({deal['price_byn']} BYN) разослан {len(subscribers)} подписчикам!"
    )


async def monitoring_worker(bot: Bot) -> None:
    global is_first_run
    logger.info("[WORKER] Мониторинг рынка запущен.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                ads = await fetch_kufar_ads(session)
                sweet_deals_found = 0

                for item in reversed(ads):
                    ad_id = str(item.get("ad_id", ""))
                    if not ad_id:
                        continue

                    if ad_id not in seen_ad_ids:
                        seen_ad_ids.add(ad_id)

                        if not is_first_run:
                            deal = analyze_phone_deal(item)
                            if deal:
                                await broadcast_sweet_deal(bot, item, deal)
                                sweet_deals_found += 1
                                await asyncio.sleep(1.0)

                if is_first_run:
                    logger.info(f"[WORKER] Инициализация. В памяти сохранено {len(seen_ad_ids)} текущих лотов.")
                    is_first_run = False
                else:
                    logger.info(f"[WORKER] Проверено лотов: {len(ads)}. Подходящих находок: {sweet_deals_found}")

            except Exception as e:
                logger.error(f"[WORKER CRITICAL ERROR] Ошибка цикла: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def health_check_handler(request: web.Request) -> web.Response:
    return web.Response(
        text=f"OK - Kufar Phone Hunter Active. Subscribers: {len(subscribers)}",
        content_type="text/plain",
    )


def create_web_application() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health_check_handler)
    app.router.add_get("/healthz", health_check_handler)
    return app


dp = Dispatcher()


@dp.message(CommandStart())
async def handle_start(message: types.Message) -> None:
    user_id = str(message.chat.id)
    if user_id not in subscribers:
        subscribers.add(user_id)
        save_subscribers(subscribers)
        logger.info(f"[NEW SUBSCRIBER] Добавлен пользователь: {user_id}. Всего: {len(subscribers)}")

    await message.answer(
        f"🎯 <b>Охотник за низом рынка Kufar активен!</b>\n\n"
        f"Я в реальном времени мониторю все ликвидные смартфоны с дисконтом:\n"
        f"• <b>Apple:</b> от iPhone XR/11 до 16 Pro Max, серия SE\n"
        f"• <b>Samsung:</b> серия S (S20–S24), раскладушки Z Flip/Fold, серия A (A34–A55)\n"
        f"• <b>Xiaomi / Poco:</b> Xiaomi 12/13/14 (вкл. T-серию), Poco F и X серии, Redmi Note 12/13 Pro\n"
        f"• <b>Google Pixel:</b> Pixel 6 / 7 / 8 / 9 (включая Pro и 'a')\n"
        f"• <b>OnePlus:</b> 9 / 10 / 11 / 12, Nord 3\n"
        f"• <b>Nothing Phone:</b> Phone (1), Phone (2), Phone (2a)\n"
        f"• <b>Realme:</b> серии GT, GT Neo, 11/12 Pro+\n"
        f"• <b>Honor & Huawei:</b> Honor 70/90/200, Magic 5/6 Pro, Huawei P50/P60/Pura 70\n\n"
        f"⚡️ При появлении выгодного лота бот моментально пришлёт расчёт выгоды и прямую ссылку.\n\n"
        f"👥 Подписчиков в системе: <b>{len(subscribers)}</b>\n"
        f"🔕 Чтобы отключить оповещения: /stop",
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command("stop"))
async def handle_stop(message: types.Message) -> None:
    user_id = str(message.chat.id)
    if user_id in subscribers:
        subscribers.remove(user_id)
        save_subscribers(subscribers)
        logger.info(f"[UNSUBSCRIBE] Пользователь отписался: {user_id}")
        await message.answer("🔕 <b>Вы отписались от уведомлений.</b> Чтобы включить обратно, отправьте /start", parse_mode=ParseMode.HTML)
    else:
        await message.answer("Вы не были подписаны. Чтобы включить рассылку, отправьте /start")


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан!")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    app = create_web_application()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    logger.info(f"[WEB] Keep-alive сервер слушает порт {WEB_PORT}")

    monitoring_task = asyncio.create_task(monitoring_worker(bot))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        monitoring_task.cancel()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
