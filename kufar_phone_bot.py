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
    (re.compile(r"\biphone\s*se\s*(2020|2)\b", re.I), "iPhone SE 2020", 200, 420),
    (re.compile(r"\b(iphone\s*)?(xr|xs\s*max|xs)\b", re.I), "iPhone XR/XS", 250, 480),
    (re.compile(r"\biphone\s*x\b|\bайфон\s*х\b|\bайфон\s*10\b", re.I), "iPhone X", 170, 360),
    (re.compile(r"\biphone\s*8\s*plus\b|\bайфон\s*8\s*плюс\b", re.I), "iPhone 8 Plus", 140, 290),
    (re.compile(r"\biphone\s*8\b|\bайфон\s*8\b", re.I), "iPhone 8", 100, 220),
    (re.compile(r"\biphone\s*7\s*plus\b|\bайфон\s*7\s*плюс\b", re.I), "iPhone 7 Plus", 90, 190),
    (re.compile(r"\biphone\s*7\b|\bайфон\s*7\b", re.I), "iPhone 7", 65, 140),

    # ==================== POCO ====================
    (re.compile(r"\bpoco\s*f6\s*pro\b", re.I), "Poco F6 Pro", 750, 1400),
    (re.compile(r"\bpoco\s*f6\b", re.I), "Poco F6", 600, 1100),
    (re.compile(r"\bpoco\s*f5\s*pro\b", re.I), "Poco F5 Pro", 550, 1050),
    (re.compile(r"\bpoco\s*f5\b", re.I), "Poco F5", 450, 850),
    (re.compile(r"\bpoco\s*f4\s*gt\b", re.I), "Poco F4 GT", 400, 800),
    (re.compile(r"\bpoco\s*f4\b", re.I), "Poco F4", 350, 700),
    (re.compile(r"\bpoco\s*f3\b", re.I), "Poco F3", 260, 520),

    (re.compile(r"\bpoco\s*x6\s*pro\b", re.I), "Poco X6 Pro", 450, 850),
    (re.compile(r"\bpoco\s*x6\b", re.I), "Poco X6", 350, 650),
    (re.compile(r"\bpoco\s*x5\s*pro\b", re.I), "Poco X5 Pro", 300, 600),
    (re.compile(r"\bpoco\s*x5\b", re.I), "Poco X5", 240, 480),
    (re.compile(r"\bpoco\s*x4\s*gt\b", re.I), "Poco X4 GT", 270, 550),
    (re.compile(r"\bpoco\s*x4\s*pro\b", re.I), "Poco X4 Pro", 220, 450),
    (re.compile(r"\bpoco\s*x3\s*pro\b", re.I), "Poco X3 Pro", 140, 300),
    (re.compile(r"\bpoco\s*x3(\s*nfc)?\b", re.I), "Poco X3 / NFC", 110, 250),
    (re.compile(r"\bpoco\s*x3\s*gt\b", re.I), "Poco X3 GT", 150, 320),

    (re.compile(r"\bpoco\s*m6\s*pro\b", re.I), "Poco M6 Pro", 260, 520),
    (re.compile(r"\bpoco\s*m5s?\b", re.I), "Poco M5 / M5s", 140, 300),
    (re.compile(r"\bpoco\s*m4\s*pro\b", re.I), "Poco M4 Pro", 140, 290),
    (re.compile(r"\bpoco\s*m3\s*pro\b", re.I), "Poco M3 Pro", 100, 230),
    (re.compile(r"\bpoco\s*m3\b", re.I), "Poco M3", 80, 180),
    (re.compile(r"\bpoco\s*c65\b", re.I), "Poco C65", 140, 300),

    # ==================== REDMI NOTE ====================
    (re.compile(r"\bredmi\s*note\s*13\s*pro\s*\+\b", re.I), "Redmi Note 13 Pro+", 480, 900),
    (re.compile(r"\bredmi\s*note\s*13\s*pro\b", re.I), "Redmi Note 13 Pro", 380, 750),
    (re.compile(r"\bredmi\s*note\s*13\b", re.I), "Redmi Note 13", 240, 500),

    (re.compile(r"\bredmi\s*note\s*12\s*pro\s*\+\b", re.I), "Redmi Note 12 Pro+", 340, 650),
    (re.compile(r"\bredmi\s*note\s*12\s*pro\b", re.I), "Redmi Note 12 Pro", 260, 550),
    (re.compile(r"\bredmi\s*note\s*12s?\b", re.I), "Redmi Note 12/12S", 180, 380),

    (re.compile(r"\bredmi\s*note\s*11\s*pro\s*(\+|5g)?\b", re.I), "Redmi Note 11 Pro", 220, 460),
    (re.compile(r"\bredmi\s*note\s*11s?\b", re.I), "Redmi Note 11/11S", 140, 320),

    (re.compile(r"\bredmi\s*note\s*10\s*pro\b", re.I), "Redmi Note 10 Pro", 150, 330),
    (re.compile(r"\bredmi\s*note\s*10s?\b", re.I), "Redmi Note 10/10S", 110, 250),

    (re.compile(r"\bredmi\s*note\s*9\s*pro\b", re.I), "Redmi Note 9 Pro", 100, 230),
    (re.compile(r"\bredmi\s*note\s*9s\b", re.I), "Redmi Note 9S", 90, 210),
    (re.compile(r"\bredmi\s*note\s*9\b", re.I), "Redmi Note 9", 75, 180),

    (re.compile(r"\bredmi\s*note\s*8\s*pro\b", re.I), "Redmi Note 8 Pro", 85, 200),
    (re.compile(r"\bredmi\s*note\s*8t?\b", re.I), "Redmi Note 8/8T", 70, 160),

    # ==================== REDMI ЧИСЛОВАЯ СЕРИЯ (БЮДЖЕТНИКИ) ====================
    (re.compile(r"\bredmi\s*13c\b", re.I), "Redmi 13C", 140, 300),
    (re.compile(r"\bredmi\s*12\b", re.I), "Redmi 12", 150, 320),
    (re.compile(r"\bredmi\s*10c\b", re.I), "Redmi 10C", 110, 240),
    (re.compile(r"\bredmi\s*10\b", re.I), "Redmi 10", 110, 230),
    (re.compile(r"\bredmi\s*9t\b", re.I), "Redmi 9T", 80, 190),
    (re.compile(r"\bredmi\s*9\b", re.I), "Redmi 9", 65, 160),
    (re.compile(r"\bredmi\s*9c\b", re.I), "Redmi 9C", 50, 130),
    (re.compile(r"\bredmi\s*9a\b", re.I), "Redmi 9A", 45, 110),

    # ==================== XIAOMI ФЛАГМАНЫ И T-СЕРИЯ ====================
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
    (re.compile(r"\bxiaomi\s*12x\b", re.I), "Xiaomi 12X", 400, 780),
    (re.compile(r"\bxiaomi\s*12\b|\bсяоми\s*12\b", re.I), "Xiaomi 12", 450, 850),
    (re.compile(r"\bxiaomi\s*11\s*t\s*pro\b", re.I), "Xiaomi 11T Pro", 380, 750),
    (re.compile(r"\bxiaomi\s*11\s*t\b", re.I), "Xiaomi 11T", 290, 600),
    (re.compile(r"\bmi\s*10\s*t\s*pro\b", re.I), "Xiaomi Mi 10T Pro", 220, 480),
    (re.compile(r"\bmi\s*10\s*t\b", re.I), "Xiaomi Mi 10T", 180, 390),

    # ==================== SAMSUNG GALAXY S & NOTE ====================
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
    (re.compile(r"\bs21\s*\+\b|\bs21\s*plus\b", re.I), "Samsung S21+", 520, 920),
    (re.compile(r"\bs21\s*fe\b", re.I), "Samsung S21 FE", 420, 750),
    (re.compile(r"\bs21\b", re.I), "Samsung S21", 450, 800),

    (re.compile(r"\bs20\s*ultra\b", re.I), "Samsung S20 Ultra", 390, 720),
    (re.compile(r"\bs20\s*\+\b|\bs20\s*plus\b", re.I), "Samsung S20+", 320, 620),
    (re.compile(r"\bs20\s*fe\b", re.I), "Samsung S20 FE", 280, 520),
    (re.compile(r"\bs20\b", re.I), "Samsung S20", 280, 520),

    (re.compile(r"\bnote\s*20\s*ultra\b", re.I), "Samsung Note 20 Ultra", 550, 950),
    (re.compile(r"\bnote\s*20\b", re.I), "Samsung Note 20", 380, 690),
    (re.compile(r"\bnote\s*10\s*plus\b|\bnote\s*10\s*\+\b", re.I), "Samsung Note 10+", 290, 600),
    (re.compile(r"\bnote\s*10\b", re.I), "Samsung Note 10", 220, 460),
    (re.compile(r"\bs10\s*plus\b|\bs10\s*\+\b", re.I), "Samsung S10+", 220, 470),
    (re.compile(r"\bs10e?\b", re.I), "Samsung S10/S10e", 170, 380),

    # ==================== SAMSUNG GALAXY Z (FOLD / FLIP) ====================
    (re.compile(r"\bz\s*flip\s*5\b", re.I), "Samsung Z Flip 5", 900, 1600),
    (re.compile(r"\bz\s*flip\s*4\b", re.I), "Samsung Z Flip 4", 550, 1050),
    (re.compile(r"\bz\s*flip\s*3\b", re.I), "Samsung Z Flip 3", 380, 700),
    (re.compile(r"\bz\s*fold\s*5\b", re.I), "Samsung Z Fold 5", 1600, 2700),
    (re.compile(r"\bz\s*fold\s*4\b", re.I), "Samsung Z Fold 4", 1100, 1900),
    (re.compile(r"\bz\s*fold\s*3\b", re.I), "Samsung Z Fold 3", 750, 1350),

    # ==================== SAMSUNG GALAXY A-СЕРИЯ ====================
    (re.compile(r"\ba55\b", re.I), "Samsung A55", 520, 920),
    (re.compile(r"\ba54\b", re.I), "Samsung A54", 390, 720),
    (re.compile(r"\ba53\b", re.I), "Samsung A53", 280, 520),
    (re.compile(r"\ba52s\b", re.I), "Samsung A52s", 250, 480),
    (re.compile(r"\ba52\b", re.I), "Samsung A52", 220, 420),
    (re.compile(r"\ba51\b", re.I), "Samsung A51", 130, 290),
    (re.compile(r"\ba50\b", re.I), "Samsung A50", 90, 200),
    (re.compile(r"\ba73\b", re.I), "Samsung A73", 380, 700),
    (re.compile(r"\ba72\b", re.I), "Samsung A72", 280, 520),
    (re.compile(r"\ba71\b", re.I), "Samsung A71", 160, 340),
    (re.compile(r"\ba35\b", re.I), "Samsung A35", 420, 750),
    (re.compile(r"\ba34\b", re.I), "Samsung A34", 310, 580),
    (re.compile(r"\ba33\b", re.I), "Samsung A33", 220, 430),
    (re.compile(r"\ba25\b", re.I), "Samsung A25", 280, 520),
    (re.compile(r"\ba24\b", re.I), "Samsung A24", 210, 410),
    (re.compile(r"\ba15\b", re.I), "Samsung A15", 190, 380),
    (re.compile(r"\ba14\b", re.I), "Samsung A14", 150, 320),
    (re.compile(r"\ba13\b|\ba12\b", re.I), "Samsung A12/A13", 110, 240),

    # ==================== INFINIX (НАРОДНЫЙ СПРОС) ====================
    (re.compile(r"\binfinix\s*gt\s*20\s*pro\b", re.I), "Infinix GT 20 Pro", 450, 850),
    (re.compile(r"\binfinix\s*gt\s*10\s*pro\b", re.I), "Infinix GT 10 Pro", 300, 600),
    (re.compile(r"\binfinix\s*note\s*40\s*pro\b", re.I), "Infinix Note 40 Pro", 360, 700),
    (re.compile(r"\binfinix\s*note\s*40\b", re.I), "Infinix Note 40", 270, 520),
    (re.compile(r"\binfinix\s*note\s*30\s*pro\b", re.I), "Infinix Note 30 Pro", 240, 480),
    (re.compile(r"\binfinix\s*note\s*30\b", re.I), "Infinix Note 30", 170, 360),
    (re.compile(r"\binfinix\s*zero\s*30\b", re.I), "Infinix Zero 30", 320, 640),
    (re.compile(r"\binfinix\s*hot\s*40\s*pro\b", re.I), "Infinix Hot 40 Pro", 190, 390),
    (re.compile(r"\binfinix\s*hot\s*30\b", re.I), "Infinix Hot 30", 140, 290),

    # ==================== TECNO ====================
    (re.compile(r"\btecno\s*camon\s*30\s*pro\b", re.I), "Tecno Camon 30 Pro", 480, 920),
    (re.compile(r"\btecno\s*camon\s*30\b", re.I), "Tecno Camon 30", 290, 580),
    (re.compile(r"\btecno\s*camon\s*20\s*pro\b", re.I), "Tecno Camon 20 Pro", 220, 450),
    (re.compile(r"\btecno\s*pova\s*6\s*pro\b", re.I), "Tecno Pova 6 Pro", 360, 700),
    (re.compile(r"\btecno\s*pova\s*5\s*pro\b", re.I), "Tecno Pova 5 Pro", 240, 480),
    (re.compile(r"\btecno\s*pova\s*5\b", re.I), "Tecno Pova 5", 190, 390),
    (re.compile(r"\btecno\s*spark\s*20\s*pro\b", re.I), "Tecno Spark 20 Pro", 190, 380),
    (re.compile(r"\btecno\s*spark\s*10\s*pro\b", re.I), "Tecno Spark 10 Pro", 140, 290),

    # ==================== MOTOROLA ====================
    (re.compile(r"\bmoto\s*edge\s*40\s*pro\b", re.I), "Moto Edge 40 Pro", 750, 1400),
    (re.compile(r"\bmoto\s*edge\s*40\s*neo\b", re.I), "Moto Edge 40 Neo", 380, 720),
    (re.compile(r"\bmoto\s*edge\s*40\b", re.I), "Moto Edge 40", 440, 850),
    (re.compile(r"\bmoto\s*edge\s*30\s*fusion\b", re.I), "Moto Edge 30 Fusion", 350, 700),
    (re.compile(r"\bmoto\s*edge\s*30\b", re.I), "Moto Edge 30", 280, 550),
    (re.compile(r"\bmoto\s*g84\b", re.I), "Moto G84", 280, 550),
    (re.compile(r"\bmoto\s*g54\b", re.I), "Moto G54", 190, 380),

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
    (re.compile(r"\boneplus\s*9\s*rt\b|\boneplus\s*9r\b", re.I), "OnePlus 9R/RT", 330, 650),
    (re.compile(r"\boneplus\s*9\b", re.I), "OnePlus 9", 350, 680),
    (re.compile(r"\boneplus\s*8\s*pro\b|\boneplus\s*8\s*t\b", re.I), "OnePlus 8 Pro/8T", 280, 580),
    (re.compile(r"\boneplus\s*nord\s*3\b", re.I), "OnePlus Nord 3", 420, 800),
    (re.compile(r"\boneplus\s*nord\s*2t?\b", re.I), "OnePlus Nord 2/2T", 280, 540),

    # ==================== NOTHING PHONE ====================
    (re.compile(r"\bnothing\s*phone\s*\(?2\)?\b", re.I), "Nothing Phone (2)", 750, 1350),
    (re.compile(r"\bnothing\s*phone\s*\(?2a\)?\b", re.I), "Nothing Phone (2a)", 450, 850),
    (re.compile(r"\bnothing\s*phone\s*\(?1\)?\b", re.I), "Nothing Phone (1)", 400, 750),

    # ==================== REALME ====================
    (re.compile(r"\brealme\s*gt\s*neo\s*5\b", re.I), "Realme GT Neo 5", 550, 1050),
    (re.compile(r"\brealme\s*gt\s*5\b", re.I), "Realme GT 5", 650, 1200),
    (re.compile(r"\brealme\s*gt\s*3\b", re.I), "Realme GT 3", 600, 1100),
    (re.compile(r"\brealme\s*12\s*pro\s*\+\b", re.I), "Realme 12 Pro+", 500, 950),
    (re.compile(r"\brealme\s*12\s*pro\b", re.I), "Realme 12 Pro", 420, 800),
    (re.compile(r"\brealme\s*11\s*pro\s*\+\b", re.I), "Realme 11 Pro+", 400, 750),
    (re.compile(r"\brealme\s*11\s*pro\b", re.I), "Realme 11 Pro", 330, 650),
    (re.compile(r"\brealme\s*10\s*pro\s*\+\b", re.I), "Realme 10 Pro+", 300, 600),
    (re.compile(r"\brealme\s*10\b", re.I), "Realme 10", 190, 390),
    (re.compile(r"\brealme\s*9\s*pro\s*\+\b", re.I), "Realme 9 Pro+", 260, 520),
    (re.compile(r"\brealme\s*8\s*pro\b", re.I), "Realme 8 Pro", 170, 360),
    (re.compile(r"\brealme\s*8\b", re.I), "Realme 8", 140, 300),
    (re.compile(r"\brealme\s*gt\s*master\b", re.I), "Realme GT Master", 190, 420),
    (re.compile(r"\brealme\s*c67\b|\brealme\s*c55\b", re.I), "Realme C55/C67", 160, 340),

    # ==================== HONOR & HUAWEI ====================
    (re.compile(r"\bhonor\s*magic\s*6\s*pro\b", re.I), "Honor Magic 6 Pro", 1500, 2600),
    (re.compile(r"\bhonor\s*magic\s*5\s*pro\b", re.I), "Honor Magic 5 Pro", 1000, 1800),
    (re.compile(r"\bhonor\s*200\s*pro\b", re.I), "Honor 200 Pro", 800, 1500),
    (re.compile(r"\bhonor\s*200\b", re.I), "Honor 200", 550, 1050),
    (re.compile(r"\bhonor\s*90\b", re.I), "Honor 90", 420, 800),
    (re.compile(r"\bhonor\s*70\b", re.I), "Honor 70", 300, 580),
    (re.compile(r"\bhonor\s*50\b", re.I), "Honor 50", 200, 440),
    (re.compile(r"\bhonor\s*x9a\b|\bhonor\s*x9b\b", re.I), "Honor X9a/b", 280, 550),
    (re.compile(r"\bhonor\s*x8b?\b", re.I), "Honor X8/X8b", 170, 360),
    (re.compile(r"\bhonor\s*20\s*pro\b", re.I), "Honor 20 Pro", 130, 290),
    (re.compile(r"\bhonor\s*20\b", re.I), "Honor 20", 100, 220),
    (re.compile(r"\bhonor\s*9x\b", re.I), "Honor 9X", 75, 170),
    (re.compile(r"\bhonor\s*8x\b", re.I), "Honor 8X", 60, 140),

    (re.compile(r"\bhuawei\s*pura\s*70\b", re.I), "Huawei Pura 70", 1200, 2100),
    (re.compile(r"\bhuawei\s*p60\s*pro\b", re.I), "Huawei P60 Pro", 950, 1700),
    (re.compile(r"\bhuawei\s*p50\s*pro\b", re.I), "Huawei P50 Pro", 550, 1050),
    (re.compile(r"\bhuawei\s*p40\s*pro\b", re.I), "Huawei P40 Pro", 350, 700),
    (re.compile(r"\bhuawei\s*p30\s*pro\b", re.I), "Huawei P30 Pro", 190, 430),
    (re.compile(r"\bhuawei\s*p30\b", re.I), "Huawei P30", 130, 280),
    (re.compile(r"\bhuawei\s*mate\s*50\s*pro\b", re.I), "Huawei Mate 50 Pro", 750, 1400),
    (re.compile(r"\bhuawei\s*nova\s*11\b", re.I), "Huawei Nova 11", 330, 650),
    (re.compile(r"\bhuawei\s*nova\s*10\b", re.I), "Huawei Nova 10", 250, 500),
    (re.compile(r"\bhuawei\s*nova\s*9\b", re.I), "Huawei Nova 9", 190, 390),

    # ==================== SONY XPERIA (ФЛАГМАНЫ) ====================
    (re.compile(r"\bxperia\s*1\s*v\b", re.I), "Sony Xperia 1 V", 1200, 2200),
    (re.compile(r"\bxperia\s*1\s*iv\b", re.I), "Sony Xperia 1 IV", 750, 1400),
    (re.compile(r"\bxperia\s*1\s*iii\b", re.I), "Sony Xperia 1 III", 450, 850),
    (re.compile(r"\bxperia\s*1\s*ii\b", re.I), "Sony Xperia 1 II", 290, 580),
    (re.compile(r"\bxperia\s*5
