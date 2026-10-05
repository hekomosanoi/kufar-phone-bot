import os
import re
import json
import random
import asyncio
import logging
from typing import Dict, Any, List, Optional
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("kufar_hunter")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

PRICE_BENCHMARKS = {
    # Apple
    "iphone 11": 550, "iphone 11 pro": 750, "iphone 11 pro max": 900,
    "iphone 12 mini": 680, "iphone 12": 820, "iphone 12 pro": 1150, "iphone 12 pro max": 1350,
    "iphone 13 mini": 1050, "iphone 13": 1300, "iphone 13 pro": 1650, "iphone 13 pro max": 1850,
    "iphone 14": 1650, "iphone 14 plus": 1800, "iphone 14 pro": 2200, "iphone 14 pro max": 2500,
    "iphone 15": 2100, "iphone 15 pro": 2800, "iphone 15 pro max": 3200,
    # Samsung
    "samsung galaxy s20 fe": 450, "samsung galaxy s21": 650, "samsung galaxy s21 ultra": 1000,
    "samsung galaxy s22": 950, "samsung galaxy s22 ultra": 1450, "samsung galaxy s23": 1400,
    "samsung galaxy a52": 320, "samsung galaxy a53": 420, "samsung galaxy a54": 580, "samsung galaxy a55": 750,
    # Xiaomi / POCO
    "redmi note 10 pro": 280, "redmi note 11 pro": 380, "redmi note 12 pro": 490, "redmi note 13 pro": 650,
    "poco x3 pro": 280, "poco x4 pro": 420, "poco x5 pro": 550, "poco x6 pro": 780,
    "poco f3": 420, "poco f4": 560, "poco f5": 750,
    # Google Pixel
    "google pixel 6": 550, "google pixel 6a": 480, "google pixel 7": 750, "google pixel 7a": 680,
}

CRITICAL_DEFECT_WORDS = [
    "на запчасти", "под восстановление", "не включается", "кирпич", "утопленник", "залит",
    "пароль", "icloud", "заблокирован", "байпас", "bypass", "mdm", "lost", "frp",
    "mi аккаунт", "mi account", "huawei id", "knox", "лизинг", "рассрочк", "сплит",
    "пятно на экране", "черное пятно", "полосы на экране", "зеленая полоса", "не работает сеть",
    "отвал процессора", "bootloop", "вечный ребут", "перезагружается сам", "ldu", "demo unit"
]

NON_CRITICAL_DEFECT_WORDS = [
    "царапины", "потертости", "потертость", "скол на корпусе", "трещина на задней",
    "трещина задней", "акб 7", "акб 8", "батарея 7", "батарея 8", "трещина на защитном"
]

COMPLETION_MISSING_WORDS = [
    "без коробки", "нет коробки", "коробка утеряна", "без зарядки",
    "нет зарядки", "без блока", "без шнура", "только телефон", "без чека"
]

DATA_FILE = "users_data.json"

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}, "seen_ids": []}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения БД: {e}")

db = load_data()

def parse_price_byn(ad: dict) -> float:
    try:
        price_usd = ad.get("price_usd", "0")
        price_byn_raw = ad.get("price_byn", "0")
        if price_byn_raw and int(price_byn_raw) > 0:
            return float(price_byn_raw) / 100.0
        return float(price_usd) / 100.0 * 3.27
    except Exception:
        return 0.0

def detect_device_category(title: str, desc: str) -> str:
    text = f"{title} {desc}".lower()
    if any(k in text for k in ["iphone", "айфон", "apple", "ios"]):
        return "apple"
    return "android"

def extract_memory_config(text: str) -> str:
    match = re.search(r"(\b\d{1,2}\s*[\/\+]\s*\d{2,4}\s*(?:gb|гб)?\b|\b(?:32|64|128|256|512|1024)\s*(?:gb|гб)\b)", text, re.I)
    return match.group(0).upper().strip() if match else ""

def analyze_phone_offer(ad: dict) -> Optional[Dict[str, Any]]:
    title = ad.get("subject", "")
    desc = ad.get("body", "")
    full_text = f"{title} {desc}".lower()
    price = parse_price_byn(ad)
    if price <= 0:
        return None

    # Отсекаем фатальные поломки и блокировки
    for bad_word in CRITICAL_DEFECT_WORDS:
        if bad_word in full_text:
            return None

    minor_defects = [w for w in NON_CRITICAL_DEFECT_WORDS if w in full_text]
    missing_items = [w for w in COMPLETION_MISSING_WORDS if w in full_text]

    matched_model = None
    market_price = 0
    for model_key, bench_price in PRICE_BENCHMARKS.items():
        if model_key in full_text:
            matched_model = model_key
            market_price = bench_price
            break

    is_profitable = False
    margin = 0.0
    discount_pct = 0.0
    suspiciously_cheap = False

    if market_price > 0:
        margin = market_price - price
        discount_pct = (margin / market_price) * 100.0
        if discount_pct >= 20.0 and margin >= 70.0:
            is_profitable = True
        if discount_pct >= 65.0:
            suspiciously_cheap = True
    else:
        # Для моделей без точного бенчмарка пропускаем в общий поток
        is_profitable = True

    return {
        "id": str(ad.get("ad_id", "")),
        "title": title,
        "price": price,
        "link": ad.get("ad_link", ""),
        "location": ad.get("account_parameters", [{}])[0].get("v", "Беларусь"),
        "os_type": detect_device_category(title, desc),
        "memory": extract_memory_config(f"{title} {desc}"),
        "minor_defects": minor_defects,
        "missing_items": missing_items,
        "market_price": market_price,
        "margin": margin,
        "discount_pct": discount_pct,
        "is_profitable": is_profitable,
        "suspiciously_cheap": suspiciously_cheap
    }

async def fetch_kufar_phones() -> List[dict]:
    url = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
    params = {
        "cat": "17010",
        "typ": "sell",
        "size": "30",
        "sort": "lst.d"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.kufar.by",
        "Referer": "https://www.kufar.by/",
        "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ads = data.get("ads", [])
                    logger.info(f"[API] Успешный опрос. Получено объявлений: {len(ads)}")
                    return ads
                elif resp.status == 403:
                    logger.warning("[API] Статус Kufar: 403 (Блокировка Cloudflare). Ожидание...")
                    return []
                else:
                    logger.warning(f"[API] Статус Kufar: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"[API] Ошибка запроса к Kufar: {e}")
            return []

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
dp = Dispatcher()

def get_main_keyboard(user_id: str) -> InlineKeyboardMarkup:
    u = db["users"].get(user_id, {"min_price": 80, "max_price": 700, "os_filter": "all", "allow_minor": True})
    os_label = {"all": "🌐 Все", "android": "🤖 Только Android", "apple": "🍏 Только Apple"}.get(u.get("os_filter", "all"), "🌐 Все")
    minor_label = "✅ Допустимы" if u.get("allow_minor", True) else "❌ Только идеал"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💰 Бюджет: {u['min_price']} - {u['max_price']} BYN", callback_data="change_budget")],
        [InlineKeyboardButton(text=f"📱 ОС: {os_label}", callback_data="toggle_os")],
        [InlineKeyboardButton(text=f"🔧 Мелкие следы: {minor_label}", callback_data="toggle_minor")],
        [InlineKeyboardButton(text="🔍 Ручная проверка сейчас", callback_data="manual_check_btn")]
    ])

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = str(msg.from_user.id)
    if uid not in db["users"]:
        db["users"][uid] = {"min_price": 80, "max_price": 700, "os_filter": "all", "allow_minor": True}
        save_data(db)
    await msg.answer("🔥 <b>Радар телефонов Kufar запущен!</b>\n\nМониторю объявления, отсекаю блокировки и считаю профит.", parse_mode="HTML", reply_markup=get_main_keyboard(uid))

@dp.message(Command("check"))
async def cmd_check(msg: types.Message):
    await msg.answer("⏳ Опрашиваю Kufar...")
    ads = await fetch_kufar_phones()
    if ads:
        await msg.answer(f"✅ Связь с Kufar отличная! Получено свежих объявлений: {len(ads)}")
    else:
        await msg.answer("⚠️ Kufar вернул пустой список или сработал лимит. Повторите через минуту.")

@dp.callback_query(F.data == "manual_check_btn")
async def cb_manual_check(callback: types.CallbackQuery):
    await callback.answer("Запрашиваю Kufar...")
    ads = await fetch_kufar_phones()
    if ads:
        await callback.message.answer(f"✅ Связь работает, получено: {len(ads)} объявлений.")
    else:
        await callback.message.answer("⚠️ Ответ от Kufar пока пуст (лимит защиты). Попробуйте чуть позже.")

@dp.callback_query(F.data == "toggle_os")
async def cb_toggle_os(callback: types.CallbackQuery):
    uid = str(callback.from_user.id)
    u = db["users"].get(uid, {"min_price": 80, "max_price": 700, "os_filter": "all", "allow_minor": True})
    states = ["all", "android", "apple"]
    u["os_filter"] = states[(states.index(u.get("os_filter", "all")) + 1) % 3]
    db["users"][uid] = u
    save_data(db)
    await callback.message.edit_reply_markup(reply_markup=get_main_keyboard(uid))
    await callback.answer()

@dp.callback_query(F.data == "toggle_minor")
async def cb_toggle_minor(callback: types.CallbackQuery):
    uid = str(callback.from_user.id)
    u = db["users"].get(uid, {"min_price": 80, "max_price": 700, "os_filter": "all", "allow_minor": True})
    u["allow_minor"] = not u.get("allow_minor", True)
    db["users"][uid] = u
    save_data(db)
    await callback.message.edit_reply_markup(reply_markup=get_main_keyboard(uid))
    await callback.answer()

@dp.callback_query(F.data == "change_budget")
async def cb_change_budget(callback: types.CallbackQuery):
    await callback.message.answer("Для изменения диапазона цен отправьте команду:\n<code>/budget МИН МАКС</code>\nПример: <code>/budget 100 800</code>", parse_mode="HTML")
    await callback.answer()

@dp.message(Command("budget"))
async def cmd_budget(msg: types.Message):
    uid = str(msg.from_user.id)
    parts = msg.text.strip().split()
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        p_min, p_max = int(parts[1]), int(parts[2])
        u = db["users"].get(uid, {"min_price": 80, "max_price": 700, "os_filter": "all", "allow_minor": True})
        u["min_price"] = min(p_min, p_max)
        u["max_price"] = max(p_min, p_max)
        db["users"][uid] = u
        save_data(db)
        await msg.answer(f"✅ Бюджет обновлен: <b>{u['min_price']} - {u['max_price']} BYN</b>", parse_mode="HTML", reply_markup=get_main_keyboard(uid))
    else:
        await msg.answer("⚠️ Формат: <code>/budget МИН МАКС</code>\nПример: <code>/budget 100 650</code>", parse_mode="HTML")

async def monitor_kufar_loop():
    logger.info("[WORKER] Фоновый процесс запущен.")
    while True:
        try:
            ads = await fetch_kufar_phones()
            new_found = 0
            for ad in ads:
                aid = str(ad.get("ad_id", ""))
                if not aid or aid in db["seen_ids"]:
                    continue

                db["seen_ids"].append(aid)
                new_found += 1
                if len(db["seen_ids"]) > 3000:
                    db["seen_ids"] = db["seen_ids"][-2000:]

                analysis = analyze_phone_offer(ad)
                if not analysis or not analysis["is_profitable"]:
                    continue

                for uid, prefs in db["users"].items():
                    if not (prefs["min_price"] <= analysis["price"] <= prefs["max_price"]):
                        continue
                    if prefs["os_filter"] != "all" and prefs["os_filter"] != analysis["os_type"]:
                        continue
                    if not prefs["allow_minor"] and analysis["minor_defects"]:
                        continue

                    badge = "🚨 ПОДОЗРИТЕЛЬНО НИЗКАЯ ЦЕНА (ПРОВЕРЯЙТЕ ЛИЧНО!)" if analysis["suspiciously_cheap"] else "🔥 ВЫГОДНЫЙ ВАРИАНТ"
                    market_info = f"📊 Рынок: ~{analysis['market_price']} BYN\n💰 Зазор: ~{analysis['margin']:.0f} BYN (-{analysis['discount_pct']:.0f}%)" if analysis["market_price"] > 0 else "📊 Рынок: индивидуальный"
                    items_txt = f"\n📦 Комплект: {', '.join(analysis['missing_items'])} (повод сбить цену)" if analysis["missing_items"] else ""
                    defects_txt = f"\n⚠️ Следы: {', '.join(analysis['minor_defects'])}" if analysis["minor_defects"] else ""

                    text = (
                        f"{badge}\n\n"
                        f"📱 <b>{analysis['title']}</b>\n"
                        f"💵 <b>Цена: {analysis['price']:.0f} BYN</b>\n"
                        f"📍 Город: {analysis['location']}\n"
                        f"{f'💾 Память: {analysis['memory']}' if analysis['memory'] else ''}\n"
                        f"{market_info}{defects_txt}{items_txt}\n\n"
                        f"🔗 <a href=\"{analysis['link']}\">Открыть на Kufar</a>"
                    )

                    try:
                        await bot.send_message(chat_id=int(uid), text=text, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Ошибка отправки сообщения пользователю {uid}: {e}")

            if new_found > 0:
                save_data(db)
        except Exception as e:
            logger.error(f"Ошибка в цикле парсинга: {e}")

        # Безопасный интервал с рандомизацией, чтобы Cloudflare не выдавал 403
        await asyncio.sleep(75 + random.randint(5, 15))

async def handle_ping(request):
    return web.Response(text="Kufar Bot OK (200)")

async def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не указан!")
        return

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Сервер health-check поднят на порту {PORT}")

    asyncio.create_task(monitor_kufar_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
