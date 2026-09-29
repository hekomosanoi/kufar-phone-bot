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
        logger.info("Bot application interrupted manually.")
