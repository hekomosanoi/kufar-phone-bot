import asyncio
import logging
import os
import sys
from typing import Dict, List, Set

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
logger = logging.getLogger("kufar_bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "7805601948")
WEB_PORT = int(os.getenv("PORT", 10000))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", 30))

KUFAR_SEARCH_API_URL = "https://api.kufar.by/search-api/v1/search/rendered-paginated"

DEFAULT_PARAMS: Dict[str, str] = {
    "cat": "17010",          # Мобильные телефоны
    "prc": "r:80,5000",      # Цена от 80 до 5000 BYN (отсекает копеечные аксессуары и шнуры)
    "sort": "lst.d",         # Сортировка: самые новые первыми
    "size": "30",            # Смотрим последние 30 объявлений
}

# Минус-слова проверяем ТОЛЬКО В НАЗВАНИИ, чтобы не резать телефоны с чехлом/коробкой в комплекте
TITLE_EXCLUDE_KEYWORDS: List[str] = [
    "чехол", "чехлы", "бампер", "накладка",
    "стекло", "пленка", "гидрогель",
    "запчасти", "на запчасти", "донор", "под восстановление",
    "дисплей", "экран", "матрица", "корпус",
    "коробка от", "пустая коробка"
]

seen_ad_ids: Set[str] = set()
is_first_run: bool = True


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


def passes_filters(item: dict) -> bool:
    subject = str(item.get("subject", "")).lower()

    # Фильтруем ТОЛЬКО по заголовку
    for stop_word in TITLE_EXCLUDE_KEYWORDS:
        if stop_word in subject:
            logger.info(f"[FILTER] Пропущен лот '{subject}' (стоп-слово: '{stop_word}')")
            return False

    return True


async def fetch_kufar_ads(session: aiohttp.ClientSession) -> List[dict]:
    try:
        async with session.get(
            KUFAR_SEARCH_API_URL,
            params=DEFAULT_PARAMS,
            headers=get_request_headers(),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            status = response.status
            if status != 200:
                logger.warning(f"[PARSER] Kufar ответил HTTP статусом: {status}")
                return []

            data = await response.json()
            items = data.get("ads", [])
            return items

    except asyncio.TimeoutError:
        logger.error("[PARSER ERROR] Таймаут соединения с Kufar API.")
        return []
    except Exception as exc:
        logger.error(f"[PARSER ERROR] Ошибка при запросе: {exc}")
        return []


async def notify_ad(bot: Bot, item: dict, chat_id: str) -> None:
    ad_id = str(item.get("ad_id", ""))
    subject = item.get("subject", "Без названия")
    ad_link = item.get("ad_link", f"https://www.kufar.by/item/{ad_id}")

    price_byn = item.get("price_byn", "0")
    price_usd = item.get("price_usd", "")
    
    if str(price_byn).isdigit() and int(price_byn) > 0:
        price_str = f"<b>{int(price_byn) / 100:.2f} BYN</b>"
    else:
        price_str = "Договорная"

    if price_usd and str(price_usd).isdigit():
        price_str += f" (~${int(price_usd) / 100:.0f})"

    parameters = item.get("ad_parameters", [])
    location = "Беларусь"
    for param in parameters:
        if param.get("p") == "area":
            location = param.get("vl", location)

    message_text = (
        f"📱 <b>Новый телефон на Kufar!</b>\n\n"
        f"📌 <b>{subject}</b>\n"
        f"💰 Цена: {price_str}\n"
        f"📍 Локация: {location}\n"
    )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть объявление", url=ad_link)]
        ]
    )

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message_text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        logger.info(f"[TELEGRAM] >>> УСПЕШНО ОТПРАВЛЕН ЛОТ: {subject} ({price_str})")
    except Exception as exc:
        logger.error(f"[TELEGRAM ERROR] Не удалось отправить сообщение: {exc}")


async def monitoring_worker(bot: Bot) -> None:
    global is_first_run
    logger.info("[WORKER] Воркер мониторинга Kufar запущен.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                ads = await fetch_kufar_ads(session)
                new_count = 0

                for item in reversed(ads):
                    ad_id = str(item.get("ad_id", ""))
                    if not ad_id:
                        continue

                    if ad_id not in seen_ad_ids:
                        seen_ad_ids.add(ad_id)

                        if not is_first_run:
                            if passes_filters(item):
                                await notify_ad(bot, item, TARGET_CHAT_ID)
                                new_count += 1
                                await asyncio.sleep(1.2)

                if is_first_run:
                    logger.info(f"[WORKER] Инициализация: кэшировано {len(seen_ad_ids)} текущих лотов.")
                    is_first_run = False
                else:
                    logger.info(f"[WORKER] Проверка завершена. Получено лотов из API: {len(ads)}, отправлено в TG: {new_count}")

            except Exception as e:
                logger.error(f"[WORKER ERROR] Сбой в цикле мониторинга: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def health_check_handler(request: web.Request) -> web.Response:
    return web.Response(text="OK - Kufar Bot Alive", content_type="text/plain")


def create_web_application() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health_check_handler)
    app.router.add_get("/healthz", health_check_handler)
    return app


dp = Dispatcher()


@dp.message(CommandStart())
async def handle_start(message: types.Message) -> None:
    await message.answer(
        f"👋 <b>Kufar Monitor активен!</b>\n\n"
        f"Ваш Chat ID: <code>{message.chat.id}</code>\n"
        f"Фильтры:\n"
        f"• Категория: Мобильные телефоны\n"
        f"• Цена: от 80 до 5000 BYN\n"
        f"• Проверка каждые 30 секунд.",
        parse_mode=ParseMode.HTML,
    )


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("ОШИБКА: TELEGRAM_BOT_TOKEN не задан!")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    app = create_web_application()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    logger.info(f"[WEB] Веб-сервер слушает порт {WEB_PORT}")

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
