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
    "cat": "17010",
    "sort": "lst.d",
    "size": "30",
}

# Формат: (регулярка, название модели, макс_цена_выкупа, ориентир_рынка)
RAW_MODELS_DATA = [
    # --- APPLE IPHONE ---
    (r"\b16\s*pro\s*max\b", "iPhone 16 Pro Max", 2800, 4200),
    (r"\b16\s*pro\b", "iPhone 16 Pro", 2400, 3600),
    (r"\biphone\s*16\s*plus\b", "iPhone 16 Plus", 2000, 3100),
    (r"\biphone\s*16\b|\bайфон\s*16\b", "iPhone 16", 1800, 2800),
    (r"\b15\s*pro\s*max\b", "iPhone 15 Pro Max", 1950, 3100),
    (r"\b15\s*pro\b", "iPhone 15 Pro", 1650, 2600),
    (r"\b15\s*plus\b", "iPhone 15 Plus", 1350, 2100),
    (r"\biphone\s*15\b|\bайфон\s*15\b", "iPhone 15", 1250, 1950),
    (r"\b14\s*pro\s*max\b", "iPhone 14 Pro Max", 1550, 2500),
    (r"\b14\s*pro\b", "iPhone 14 Pro", 1350, 2100),
    (r"\b14\s*plus\b", "iPhone 14 Plus", 1050, 1650),
    (r"\biphone\s*14\b|\bайфон\s*14\b", "iPhone 14", 950, 1550),
    (r"\b13\s*pro\s*max\b", "iPhone 13 Pro Max", 1200, 1950),
    (r"\b13\s*pro\b", "iPhone 13 Pro", 950, 1600),
    (r"\b13\s*mini\b", "iPhone 13 mini", 650, 1150),
    (r"\biphone\s*13\b|\bайфон\s*13\b", "iPhone 13", 750, 1350),
    (r"\b12\s*pro\s*max\b", "iPhone 12 Pro Max", 800, 1350),
    (r"\b12\s*pro\b", "iPhone 12 Pro", 650, 1150),
    (r"\b12\s*mini\b", "iPhone 12 mini", 400, 750),
    (r"\biphone\s*12\b|\bайфон\s*12\b", "iPhone 12", 500, 950),
    (r"\b11\s*pro\s*max\b", "iPhone 11 Pro Max", 550, 950),
    (r"\b11\s*pro\b", "iPhone 11 Pro", 450, 800),
    (r"\biphone\s*11\b|\bайфон\s*11\b", "iPhone 11", 350, 650),
    (r"\biphone\s*se\s*(2022|3)\b", "iPhone SE 2022", 350, 680),
    (r"\biphone\s*se\s*(2020|2)\b", "iPhone SE 2020", 200, 420),
    (r"\b(iphone\s*)?(xr|xs\s*max|xs)\b", "iPhone XR/XS", 250, 480),
    (r"\biphone\s*x\b|\bайфон\s*х\b|\bайфон\s*10\b", "iPhone X", 170, 360),
    (r"\biphone\s*8\s*plus\b|\bайфон\s*8\s*плюс\b", "iPhone 8 Plus", 140, 290),
    (r"\biphone\s*8\b|\bайфон\s*8\b", "iPhone 8", 100, 220),
    (r"\biphone\s*7\s*plus\b|\bайфон\s*7\s*плюс\b", "iPhone 7 Plus", 90, 190),
    (r"\biphone\s*7\b|\bайфон\s*7\b", "iPhone 7", 65, 140),

    # --- POCO & REDMI NOTE & XIAOMI ---
    (r"\bpoco\s*f6\s*pro\b", "Poco F6 Pro", 750, 1400),
    (r"\bpoco\s*f6\b", "Poco F6", 600, 1100),
    (r"\bpoco\s*f5\b", "Poco F5", 450, 850),
    (r"\bpoco\s*x6\s*pro\b", "Poco X6 Pro", 450, 850),
    (r"\bpoco\s*x6\b", "Poco X6", 350, 650),
    (r"\bpoco\s*x5\s*pro\b", "Poco X5 Pro", 300, 600),
    (r"\bpoco\s*x5\b", "Poco X5", 240, 480),
    (r"\bpoco\s*x4\s*gt\b", "Poco X4 GT", 270, 550),
    (r"\bpoco\s*x3\s*pro\b", "Poco X3 Pro", 140, 300),
    (r"\bpoco\s*x3(\s*nfc)?\b", "Poco X3 / NFC", 110, 250),
    (r"\bpoco\s*m6\s*pro\b", "Poco M6 Pro", 260, 520),
    (r"\bpoco\s*m5s?\b", "Poco M5 / M5s", 140, 300),
    (r"\bpoco\s*m4\s*pro\b", "Poco M4 Pro", 140, 290),
    (r"\bpoco\s*c65\b", "Poco C65", 140, 300),

    (r"\bredmi\s*note\s*13\s*pro\s*\+\b", "Redmi Note 13 Pro+", 480, 900),
    (r"\bredmi\s*note\s*13\s*pro\b", "Redmi Note 13 Pro", 380, 750),
    (r"\bredmi\s*note\s*13\b", "Redmi Note 13", 240, 500),
    (r"\bredmi\s*note\s*12\s*pro\s*\+\b", "Redmi Note 12 Pro+", 340, 650),
    (r"\bredmi\s*note\s*12\s*pro\b", "Redmi Note 12 Pro", 260, 550),
    (r"\bredmi\s*note\s*12s?\b", "Redmi Note 12/12S", 180, 380),
    (r"\bredmi\s*note\s*11\s*pro\b", "Redmi Note 11 Pro", 220, 460),
    (r"\bredmi\s*note\s*11s?\b", "Redmi Note 11/11S", 140, 320),
    (r"\bredmi\s*note\s*10\s*pro\b", "Redmi Note 10 Pro", 150, 330),
    (r"\bredmi\s*note\s*9\s*pro\b", "Redmi Note 9 Pro", 100, 230),
    (r"\bredmi\s*note\s*8\s*pro\b", "Redmi Note 8 Pro", 85, 200),

    (r"\bredmi\s*13c\b", "Redmi 13C", 140, 300),
    (r"\bredmi\s*12\b", "Redmi 12", 150, 320),
    (r"\bredmi\s*10c\b|\bredmi\s*10\b", "Redmi 10 / 10C", 110, 240),
    (r"\bredmi\s*9t?\b", "Redmi 9 / 9T", 75, 170),
    (r"\bredmi\s*9a\b|\bredmi\s*9c\b", "Redmi 9A / 9C", 50, 120),

    (r"\bxiaomi\s*14\b|\bсяоми\s*14\b", "Xiaomi 14", 1200, 2100),
    (r"\bxiaomi\s*13\s*t\s*pro\b", "Xiaomi 13T Pro", 850, 1550),
    (r"\bxiaomi\s*13\s*t\b", "Xiaomi 13T", 650, 1200),
    (r"\bxiaomi\s*13\b", "Xiaomi 13", 800, 1450),
    (r"\bxiaomi\s*12\s*t\b", "Xiaomi 12T", 500, 950),
    (r"\bxiaomi\s*12\b", "Xiaomi 12", 450, 850),
    (r"\bxiaomi\s*11\s*t\b", "Xiaomi 11T", 290, 600),

    # --- SAMSUNG GALAXY ---
    (r"\bs24\s*ultra\b", "Samsung S24 Ultra", 1850, 3100),
    (r"\bs24\b", "Samsung S24", 1100, 1900),
    (r"\bs23\s*ultra\b", "Samsung S23 Ultra", 1300, 2200),
    (r"\bs23\s*fe\b", "Samsung S23 FE", 650, 1150),
    (r"\bs23\b", "Samsung S23", 850, 1450),
    (r"\bs22\s*ultra\b", "Samsung S22 Ultra", 900, 1600),
    (r"\bs22\b", "Samsung S22", 600, 1100),
    (r"\bs21\s*ultra\b", "Samsung S21 Ultra", 650, 1100),
    (r"\bs21\s*fe\b", "Samsung S21 FE", 420, 750),
    (r"\bs21\b", "Samsung S21", 450, 800),
    (r"\bs20\s*fe\b|\bs20\b", "Samsung S20 / S20 FE", 280, 520),
    (r"\bnote\s*20\s*ultra\b", "Samsung Note 20 Ultra", 550, 950),
    (r"\bnote\s*10\b", "Samsung Note 10", 220, 460),
    (r"\bs10\b", "Samsung S10", 170, 380),
    (r"\bz\s*flip\s*5\b", "Samsung Z Flip 5", 900, 1600),
    (r"\bz\s*fold\s*4\b|\bz\s*fold\s*5\b", "Samsung Z Fold 4/5", 1300, 2200),

    (r"\ba55\b", "Samsung A55", 520, 920),
    (r"\ba54\b", "Samsung A54", 390, 720),
    (r"\ba53\b|\ba52s?\b", "Samsung A52 / A53", 260, 500),
    (r"\ba51\b", "Samsung A51", 130, 290),
    (r"\ba50\b", "Samsung A50", 90, 200),
    (r"\ba34\b|\ba35\b", "Samsung A34 / A35", 350, 650),
    (r"\ba24\b|\ba25\b", "Samsung A24 / A25", 240, 480),
    (r"\ba14\b|\ba15\b", "Samsung A14 / A15", 160, 340),
    (r"\ba12\b|\ba13\b", "Samsung A12 / A13", 110, 240),

    # --- INFINIX & TECNO ---
    (r"\binfinix\s*gt\s*(10|20)\s*pro\b", "Infinix GT 10/20 Pro", 380, 750),
    (r"\binfinix\s*note\s*40\b", "Infinix Note 40", 270, 520),
    (r"\binfinix\s*note\s*30\b", "Infinix Note 30", 170, 360),
    (r"\binfinix\s*hot\s*(30|40)\b", "Infinix Hot 30/40", 150, 320),
    (r"\btecno\s*camon\s*30\b", "Tecno Camon 30", 290, 580),
    (r"\btecno\s*pova\s*(5|6)\b", "Tecno Pova 5/6", 240, 480),
    (r"\btecno\s*spark\s*(10|20)\b", "Tecno Spark 10/20", 160, 330),

    # --- GOOGLE PIXEL & ONEPLUS ---
    (r"\bpixel\s*8\s*pro\b", "Pixel 8 Pro", 1100, 1900),
    (r"\bpixel\s*8\b|\bpixel\s*8a\b", "Pixel 8 / 8a", 750, 1350),
    (r"\bpixel\s*7\s*pro\b", "Pixel 7 Pro", 700, 1250),
    (r"\bpixel\s*7\b|\bpixel\s*7a\b", "Pixel 7 / 7a", 500, 920),
    (r"\bpixel\s*6\s*pro\b", "Pixel 6 Pro", 450, 820),
    (r"\bpixel\s*6\b|\bpixel\s*6a\b", "Pixel 6 / 6a", 340, 620),
    (r"\boneplus\s*11\b|\boneplus\s*12\b", "OnePlus 11/12", 850, 1600),
    (r"\boneplus\s*10\s*pro\b|\boneplus\s*10\s*t\b", "OnePlus 10 Pro/10T", 500, 950),
    (r"\boneplus\s*9\s*pro\b|\boneplus\s*9\b", "OnePlus 9 / 9 Pro", 380, 720),
    (r"\boneplus\s*nord\s*(2|3)\b", "OnePlus Nord 2/3", 320, 620),

    # --- NOTHING & REALME ---
    (r"\bnothing\s*phone\s*\(?2a?\)?\b", "Nothing Phone (2/2a)", 550, 1050),
    (r"\bnothing\s*phone\s*\(?1\)?\b", "Nothing Phone (1)", 400, 750),
    (r"\brealme\s*gt\s*(neo\s*5|3|5)\b", "Realme GT Series", 550, 1050),
    (r"\brealme\s*(11|12)\s*pro\b", "Realme 11/12 Pro", 380, 750),
    (r"\brealme\s*c(55|67)\b", "Realme C55/C67", 160, 340),

    # --- HONOR & HUAWEI ---
    (r"\bhonor\s*magic\s*(5|6)\s*pro\b", "Honor Magic 5/6 Pro", 1100, 2000),
    (r"\bhonor\s*200\b", "Honor 200", 550, 1050),
    (r"\bhonor\s*90\b", "Honor 90", 420, 800),
    (r"\bhonor\s*70\b|\bhonor\s*50\b", "Honor 50/70", 250, 480),
    (r"\bhonor\s*20\s*pro\b|\bhonor\s*20\b", "Honor 20 / 20 Pro", 120, 260),
    (r"\bhonor\s*8x\b|\bhonor\s*9x\b", "Honor 8X / 9X", 65, 150),
    (r"\bhuawei\s*p(50|60)\s*pro\b", "Huawei P50/P60 Pro", 750, 1400),
    (r"\bhuawei\s*p30\s*pro\b", "Huawei P30 Pro", 190, 430),

    # --- SONY & ASUS & MOTO ---
    (r"\bxperia\s*1\s*(iv|v)\b", "Sony Xperia 1 IV/V", 900, 1700),
    (r"\bxperia\s*1\s*(ii|iii)\b", "Sony Xperia 1 II/III", 380, 750),
    (r"\bxperia\s*5\s*(ii|iii|iv)\b", "Sony Xperia 5 II/III/IV", 390, 750),
    (r"\bzenfone\s*(8|9|10)\b", "Asus Zenfone 8/9/10", 650, 1300),
    (r"\brog\s*phone\s*(5|6|7)\b", "Asus ROG Phone 5/6/7", 750, 1450),
    (r"\bmoto\s*edge\s*(30|40)\b", "Moto Edge 30/40", 380, 750),
    (r"\bmoto\s*g(54|84)\b", "Moto G54/G84", 230, 460),
]

PRICE_MATRIX: List[Tuple[re.Pattern, str, float, float]] = []
for pat_str, name, max_p, mkt_p in RAW_MODELS_DATA:
    PRICE_MATRIX.append((re.compile(pat_str, re.I), name, float(max_p), float(mkt_p)))

STOP_WORDS = [
    "чехол", "чехлы", "бампер", "стекло", "пленка", "гидрогель",
    "коробка от", "пустая коробка", "запчасти", "на запчасти", "донор",
    "icloud", "айклауд", "заблокирован", "байпас", "bypass", "парол",
    "r-sim", "rsim", "рсим", "демо", "demo", "не включается", "артефакт",
    "рассрочка", "кредит", "разбор", "дисплей от", "плата от",
]

seen_ad_ids: Set[str] = set()
is_first_run: bool = True
subscribers: Set[str] = set()


def load_subscribers() -> Set[str]:
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
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(subs), f, ensure_ascii=False, indent=2)
    except Exception as err:
        logger.error(f"[STORAGE] Ошибка сохранения {SUBSCRIBERS_FILE}: {err}")


subscribers = load_subscribers()
logger.info(f"[STORAGE] Загружено подписчиков: {len(subscribers)}")

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

    for word in STOP_WORDS:
        if word in full_text:
            return None

    raw_price = item.get("price_byn", "0")
    if not str(raw_price).isdigit():
        return None
    price_byn = int(raw_price) / 100.0

    for pattern, model_name, max_price, market_price in PRICE_MATRIX:
        if pattern.search(subject):
            if 30.0 <= price_byn <= max_price:
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
                logger.warning(f"[API] Статус Kufar: {response.status}")
                return []
            data = await response.json()
            return data.get("ads", [])
    except Exception as exc:
        logger.error(f"[API ERROR] {exc}")
        return []

async def broadcast_sweet_deal(bot: Bot, item: dict, deal: dict) -> None:
    global subscribers
    if not subscribers:
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
        f"⚠ <i>Проверяйте телефон лично перед покупкой (оригинальность, блокировки, состояние)!</i>"
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
        except Exception as exc:
            logger.error(f"[BROADCAST ERROR] {chat_id}: {exc}")

    if unreachable_users:
        subscribers -= unreachable_users
        save_subscribers(subscribers)

async def monitoring_worker(bot: Bot) -> None:
    global is_first_run
    logger.info("[WORKER] Мониторинг запущен.")

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
                    logger.info(f"[WORKER] Инициализация: закэшировано {len(seen_ad_ids)} лотов.")
                    is_first_run = False
                else:
                    logger.info(f"[WORKER] Проверено: {len(ads)}. Находок: {sweet_deals_found}")

            except Exception as e:
                logger.error(f"[WORKER ERROR] {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def health_check_handler(request: web.Request) -> web.Response:
    return web.Response(
        text=f"OK - Kufar Hunter Active. Subscribers: {len(subscribers)}",
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
        logger.info(f"[NEW SUB] Пользователь {user_id} добавлен.")

    await message.answer(
        f"🎯 <b>Охотник за низом рынка Kufar активен!</b>\n\n"
        f"Я в реальном времени мониторю все ликвидные смартфоны с дисконтом от 30 BYN:\n"
        f"• <b>Apple:</b> iPhone от 7/8/X/11 до 16 Pro Max\n"
        f"• <b>Poco:</b> X3/X4/X5/X6, F-серия, M-серия, C65\n"
        f"• <b>Redmi:</b> Note 8–13 Pro, Redmi 9/10/12/13C\n"
        f"• <b>Xiaomi:</b> T-серия, флагманы 12/13/14\n"
        f"• <b>Samsung:</b> серия S (S10-S24), серия A (A12-A55), Note, Z Flip/Fold\n"
        f"• <b>Infinix, Tecno, Google Pixel, OnePlus, Nothing, Realme, Honor, Sony, Asus</b>\n\n"
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
    logger.info(f"[WEB] Keep-alive сервер запущен на порту {WEB_PORT}")

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
