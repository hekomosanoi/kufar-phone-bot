import asyncio
import logging
import os
import sys
from typing import Dict, List, Optional, Set

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("kufar_monitor")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
WEB_PORT = int(os.getenv("PORT", 10000))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", 45))

# Target query parameters for Kufar search API
# You can customize category, price range, or keywords
KUFAR_SEARCH_API_URL = "https://api.kufar.by/search-api/v1/search/rendered-paginated"

DEFAULT_PARAMS: Dict[str, str] = {
    "cat": "17010",          # Electronics / Phones category (example)
    "prc": "r:100,800",      # Price range in BYN (min:max), comment or adjust as needed
    "sort": "lst.d",         # Sort by newest first (strictly descending)
    "size": "20",            # Check latest 20 items per cycle
}

# Optional keywords filtering (case-insensitive)
# Leave empty [] to track all listings in the category
INCLUDE_KEYWORDS: List[str] = []
# Stop-words to filter out spam or undesired ads
EXCLUDE_KEYWORDS: List[str] = ["чехол", "стекло", "аксессуар", "запчасти", "коробка"]

# In-memory deduplication set to prevent duplicate Telegram alerts
seen_ad_ids: Set[str] = set()
is_first_run: bool = True


def get_request_headers() -> Dict[str, str]:
    """Provides standard browser-like headers to avoid generic API blocks."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.kufar.by/",
        "Origin": "https://www.kufar.by",
    }


def passes_filters(item: dict) -> bool:
    """Evaluates whether an ad passes keyword and parameter constraints."""
    subject = str(item.get("subject", "")).lower()
    body = str(item.get("body", "")).lower()
    full_text = f"{subject} {body}"

    # Check for excluded keywords
    for stop_word in EXCLUDE_KEYWORDS:
        if stop_word.lower() in full_text:
            return False

    # Check for included keywords if specified
    if INCLUDE_KEYWORDS:
        matched = any(kw.lower() in full_text for kw in INCLUDE_KEYWORDS)
        if not matched:
            return False

    return True


async def fetch_kufar_ads(session: aiohttp.ClientSession) -> List[dict]:
    """Sends asynchronous request to Kufar search API with error diagnostics."""
    try:
        async with session.get(
            KUFAR_SEARCH_API_URL,
            params=DEFAULT_PARAMS,
            headers=get_request_headers(),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            status = response.status
            if status != 200:
                logger.warning(
                    f"[PARSER ERROR] Kufar returned non-200 HTTP code: {status}"
                )
                return []

            data = await response.json()
            items = data.get("ads", [])
            logger.info(f"[PARSER] Successfully fetched {len(items)} ads from Kufar API.")
            return items

    except asyncio.TimeoutError:
        logger.error("[PARSER ERROR] Network timeout while requesting Kufar API.")
        return []
    except Exception as exc:
        logger.error(f"[PARSER ERROR] Unexpected exception during fetch: {exc}", exc_info=True)
        return []


async def notify_ad(bot: Bot, item: dict, chat_id: str) -> None:
    """Formats and transmits the listing alert with direct link and price info."""
    ad_id = str(item.get("ad_id", ""))
    subject = item.get("subject", "Без названия")
    ad_link = item.get("ad_link", f"https://www.kufar.by/item/{ad_id}")

    # Parse pricing details
    price_byn = item.get("price_byn", "Договорная")
    price_usd = item.get("price_usd", "")
    price_str = f"<b>{int(price_byn) / 100:.2f} BYN</b>" if str(price_byn).isdigit() else str(price_byn)
    if price_usd and str(price_usd).isdigit():
        price_str += f" (~${int(price_usd) / 100:.0f})"

    # Extract location parameters if present
    parameters = item.get("ad_parameters", [])
    location = "Беларусь"
    for param in parameters:
        if param.get("p") == "area":
            location = param.get("vl", location)

    message_text = (
        f"🔥 <b>Новое объявление на Kufar!</b>\n\n"
        f"📌 <b>{subject}</b>\n"
        f"💰 Цена: {price_str}\n"
        f"📍 Локация: {location}\n"
    )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть на Kufar", url=ad_link)]
        ]
    )

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message_text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=False,
        )
        logger.info(f"[TELEGRAM] Sent alert for ad ID: {ad_id}")
    except Exception as exc:
        logger.error(f"[TELEGRAM ERROR] Failed to send message for ad {ad_id}: {exc}")


async def monitoring_worker(bot: Bot) -> None:
    """Continuous background loop for scanning and dispatching new listings."""
    global is_first_run
    logger.info("[WORKER] Monitoring background worker initialized and running.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                logger.info("[WORKER] Starting regular ad polling cycle...")
                ads = await fetch_kufar_ads(session)

                new_items_found = 0
                for item in reversed(ads):  # Process oldest to newest
                    ad_id = str(item.get("ad_id", ""))
                    if not ad_id:
                        continue

                    if ad_id not in seen_ad_ids:
                        seen_ad_ids.add(ad_id)

                        # Prevent flooding all existing ads on initial startup
                        if not is_first_run:
                            if passes_filters(item):
                                await notify_ad(bot, item, TARGET_CHAT_ID)
                                new_items_found += 1
                                await asyncio.sleep(1.0)  # Gentle spacing between sends

                if is_first_run:
                    logger.info(
                        f"[WORKER] Initial scan complete. Cached {len(seen_ad_ids)} existing ads without spamming."
                    )
                    is_first_run = False
                else:
                    logger.info(f"[WORKER] Polling cycle finished. Fresh matches sent: {new_items_found}")

            except Exception as e:
                logger.error(f"[WORKER CRITICAL ERROR] Unhandled loop failure: {e}", exc_info=True)

            logger.info(f"[WORKER] Sleeping for {CHECK_INTERVAL_SECONDS} seconds before next run.")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def health_check_handler(request: web.Request) -> web.Response:
    """Responds with HTTP 200 to keep the free Render container awake."""
    return web.Response(
        text=f"OK. Kufar Monitor is alive. Cached items: {len(seen_ad_ids)}",
        content_type="text/plain",
    )


def create_web_application() -> web.Application:
    """Configures the internal HTTP web application."""
    app = web.Application()
    app.router.add_get("/", health_check_handler)
    app.router.add_get("/healthz", health_check_handler)
    return app


dp = Dispatcher()


@dp.message(CommandStart())
async def handle_start(message: types.Message) -> None:
    """Informs the user about the bot status and reveals user/chat ID."""
    await message.answer(
        f"👋 <b>Kufar Monitor активен!</b>\n\n"
        f"Ваш Chat ID: <code>{message.chat.id}</code>\n"
        f"В базе отслежено объявлений: {len(seen_ad_ids)}\n"
        f"Используйте этот Chat ID в переменной окружения <code>TARGET_CHAT_ID</code>.",
        parse_mode=ParseMode.HTML,
    )


async def main() -> None:
    """Coordinates the simultaneous execution of web server, bot polling, and parser worker."""
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not configured! Exiting.")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # 1. Start aiohttp HTTP web server for Render keep-alive
    app = create_web_application()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    logger.info(f"[WEB] Keep-alive server running on port {WEB_PORT}")

    # 2. Launch parser monitoring task in background
    monitoring_task = asyncio.create_task(monitoring_worker(bot))

    # 3. Start aiogram bot dispatcher polling
    try:
        logger.info("[BOT] Starting Telegram bot polling...")
        # Drop previous pending updates to prevent startup burst
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        monitoring_task.cancel()
        await runner.cleanup()
        await bot.session.close()
        logger.info("[SHUTDOWN] Services successfully terminated.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot application interrupted manually.")"OnePlus", ["oneplus", "ванплас", "1+"]),
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
