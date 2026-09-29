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
    "cat": "17010",          # Раздел: Телефоны
    "sort": "lst.d",         # Сортировка: Свежие первыми
    "size": "30",            # 30 последних объявлений
}

subscribers: Set[str] = set()


def load_subscribers() -> Set[str]:
    """Загружает список chat_id подписчиков из файла."""
    loaded = set()
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    loaded = set(str(uid) for uid in data)
        except Exception as err:
            logger.error(f"[STORAGE] Ошибка чтения {SUBSCRIBERS_FILE}: {err}")

    # Гарантируем, что ваш аккаунт всегда подписан по умолчанию
    if DEFAULT_ADMIN_CHAT_ID:
        loaded.add(str(DEFAULT_ADMIN_CHAT_ID))
    return loaded


def save_subscribers(subs: Set[str]) -> None:
    """Сохраняет текущий список подписчиков на диск."""
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(subs), f, ensure_ascii=False, indent=2)
    except Exception as err:
        logger.error(f"[STORAGE] Ошибка сохранения {SUBSCRIBERS_FILE}: {err}")


subscribers = load_subscribers()
logger.info(f"[STORAGE] Активных подписчиков при старте: {len(subscribers)} ({subscribers})")

PRICE_MATRIX: List[Tuple[re.Pattern, str, float, float]] = [
    # iPhone
    (re.compile(r"\b15\s*pro\s*max\b", re.I), "iPhone 15 Pro Max", 1900, 3100),
    (re.compile(r"\b15\s*pro\b", re.I), "iPhone 15 Pro", 1600, 2600),
    (re.compile(r"\b15\s*plus\b", re.I), "iPhone 15 Plus", 1300, 2200),
    (re.compile(r"\biphone\s*15\b|\bайфон\s*15\b", re.I), "iPhone 15", 1200, 2000),

    (re.compile(r"\b14\s*pro\s*max\b", re.I), "iPhone 14 Pro Max", 1500, 2500),
    (re.compile(r"\b14\s*pro\b", re.I), "iPhone 14 Pro", 1300, 2100),
    (re.compile(r"\b14\s*plus\b", re.I), "iPhone 14 Plus", 1000, 1700),
    (re.compile(r"\biphone\s*14\b|\bайфон\s*14\b", re.I), "iPhone 14", 950, 1600),

    (re.compile(r"\b13\s*pro\s*max\b", re.I), "iPhone 13 Pro Max", 1200, 1900),
    (re.compile(r"\b13\s*pro\b", re.I), "iPhone 13 Pro", 950, 1600),
    (re.compile(r"\b13\s*mini\b", re.I), "iPhone 13 mini", 650, 1200),
    (re.compile(r"\biphone\s*13\b|\bайфон\s*13\b", re.I), "iPhone 13", 750, 1350),

    (re.compile(r"\b12\s*pro\s*max\b", re.I), "iPhone 12 Pro Max", 800, 1400),
    (re.compile(r"\b12\s*pro\b", re.I), "iPhone 12 Pro", 650, 1200),
    (re.compile(r"\b12\s*mini\b", re.I), "iPhone 12 mini", 400, 800),
    (re.compile(r"\biphone\s*12\b|\bайфон\s*12\b", re.I), "iPhone 12", 500, 950),

    (re.compile(r"\b11\s*pro\s*max\b", re.I), "iPhone 11 Pro Max", 550, 950),
    (re.compile(r"\b11\s*pro\b", re.I), "iPhone 11 Pro", 450, 800),
    (re.compile(r"\biphone\s*11\b|\bайфон\s*11\b", re.I), "iPhone 11", 350, 650),

    (re.compile(r"\b(iphone\s*)?(xr|xs\s*max|xs)\b", re.I), "iPhone XR/XS", 250, 480),

    # Samsung Флагманы
    (re.compile(r"\bs23\s*ultra\b", re.I), "Samsung S23 Ultra", 1300, 2200),
    (re.compile(r"\bs23\b", re.I), "Samsung S23", 900, 1500),
    (re.compile(r"\bs22\s*ultra\b", re.I), "Samsung S22 Ultra", 900, 1600),
    (re.compile(r"\bs22\b", re.I), "Samsung S22", 650, 1100),
    (re.compile(r"\bs21\s*ultra\b", re.I), "Samsung S21 Ultra", 650, 1100),
    (re.compile(r"\bs21\b", re.I), "Samsung S21", 450, 800),
]

STOP_WORDS: List[str] = [
    "чехол", "чехлы", "бампер", "стекло", "пленка", "гидрогель",
    "коробка от", "пустая коробка", "запчасти", "на запчасти", "донор",
    "icloud", "айклауд", "заблокирован", "байпас", "bypass", "парол",
    "r-sim", "rsim", "рсим", "демо", "demo", "не включается"
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


def analyze_phone_deal(item: dict) -> Optional[dict]:
    subject = str(item.get("subject", "")).lower()
    body = str(item.get("body", "")).lower()
    full_text = f"{subject} {body}"

    # 1. Проверяем стоп-слова
    for word in STOP_WORDS:
        if word in full_text:
            return None

    # 2. Получаем цену в BYN
    raw_price = item.get("price_byn", "0")
    if not str(raw_price).isdigit():
        return None
    price_byn = int(raw_price) / 100.0

    # 3. Сверяем с матрицей цен выкупа
    for pattern, model_name, max_price, market_price in PRICE_MATRIX:
        if pattern.search(subject):
            if price_byn <= max_price and price_byn >= 70:
                profit = market_price - price_byn
                discount_pct = int(((market_price - price_byn) / market_price) * 100)
                return {
                    "model": model_name,
                    "price_byn": price_byn,
                    "market_price": market_price,
                    "profit": profit,
                    "discount_pct": discount_pct,
                }
            else:
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
        logger.error(f"[API ERROR] Ошибка запроса: {exc}")
        return []


async def broadcast_sweet_deal(bot: Bot, item: dict, deal: dict) -> None:
    """Рассылает найденный лот всем активным подписчикам."""
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
        f"⚠️ <i>Проверьте телефон на оригинальность и iCloud перед покупкой!</i>"
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
            await asyncio.sleep(0.05)  # Небольшая пауза между пользователями
        except TelegramForbiddenError:
            # Пользователь заблокировал бота
            unreachable_users.add(chat_id)
            logger.info(f"[BROADCAST] Пользователь {chat_id} заблокировал бота, удаляем из рассылки.")
        except Exception as exc:
            logger.error(f"[BROADCAST ERROR] Не удалось отправить {chat_id}: {exc}")

    if unreachable_users:
        subscribers -= unreachable_users
        save_subscribers(subscribers)

    logger.info(f"[BROADCAST SENT] Лот {deal['model']} ({deal['price_byn']} BYN) разослан {len(subscribers)} подписчикам!")

async def monitoring_worker(bot: Bot) -> None:
    global is_first_run
    logger.info("[WORKER] Охотник за низом рынка запущен.")

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
                    logger.info(f"[WORKER] Инициализация. В памяти сохранено {len(seen_ad_ids)} текущих объявлений.")
                    is_first_run = False
                else:
                    logger.info(f"[WORKER] Проверено лотов: {len(ads)}. Жирных находок: {sweet_deals_found}")

            except Exception as e:
                logger.error(f"[WORKER CRITICAL ERROR] Ошибка цикла: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def health_check_handler(request: web.Request) -> web.Response:
    return web.Response(
        text=f"OK - Kufar Hunter Alive. Subscribers: {len(subscribers)}",
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
        f"🎯 <b>Вы успешно подписаны на уведомления!</b>\n\n"
        f"Я отслеживаю iPhone (от 11 до 15 Pro Max) и флагманы Samsung по низу рынка.\n"
        f"Как только появится жирный лот с дисконтом — вам сразу придет оповещение.\n\n"
        f"👥 Всего подписчиков в системе: <b>{len(subscribers)}</b>\n"
        f"🔕 Если захотите отписаться: /stop",
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command("stop"))
async def handle_stop(message: types.Message) -> None:
    user_id = str(message.chat.id)
    if user_id in subscribers:
        subscribers.remove(user_id)
        save_subscribers(subscribers)
        logger.info(f"[UNSUBSCRIBE] Пользователь отписался: {user_id}")
        await message.answer("🔕 <b>Вы отписались от уведомлений.</b> Чтобы возобновить, нажмите /start", parse_mode=ParseMode.HTML)
    else:
        await message.answer("Вы и так не были подписаны. Чтобы включить рассылку, нажмите /start")

async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не указан!")
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
