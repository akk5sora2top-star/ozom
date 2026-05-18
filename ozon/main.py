import asyncio
import logging
import httpx
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ================= НАСТРОЙКИ БОТА =================
BOT_TOKEN = "СЮДА_ВСТАВЬ_ТОКЕН_ОТ_BOTFATHER"
CHECK_INTERVAL = 1800  # Интервал авто-проверки в секундах (1800 сек = 30 минут)

# Список ваших прокси (формат: http://логин:пароль@ip:порт или http://ip:порт)
# Лучше использовать резидентские или мобильные прокси РФ
PROXY_LIST = [
    "http://user1:pass1@123.45.67.89:8000",
    "http://user2:pass2@98.76.54.32:8000",
]
# ==================================================

# Временная база данных в оперативной памяти (после перезапуска очистится).
# Для надежности на Fly.io в будущем можно подключить SQLite или PostgreSQL.
# Структура: {chat_id: {"track_id": "...", "last_status": "..."}}
tracking_database = {}

# Включаем логирование, чтобы видеть работу бота в консоли Fly.io
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Состояния для ввода трек-номера
class TrackStates(StatesGroup):
    waiting_for_track = State()


async def check_proxy(proxy: str) -> bool:
    """Проверяет, работает ли прокси через тестовый запрос"""
    async with httpx.AsyncClient(proxies=proxy, timeout=5.0) as client:
        try:
            response = await client.get("https://httpbin.org/ip")
            if response.status_code == 200:
                logging.info(f"🟢 Прокси {proxy} активен.")
                return True
        except Exception:
            logging.warning(f"🔴 Прокси {proxy} не отвечает.")
    return False


async def get_working_proxy() -> str | None:
    """Перебирает список прокси и возвращает первый рабочий"""
    for proxy in PROXY_LIST:
        if await check_proxy(proxy):
            return proxy
    return None


async def fetch_ozon_status(track_id: str, proxy: str | None = None) -> str:
    """
    Функция запроса статуса посылки.
    ВАЖНОЕ ПРИМЕЧАНИЕ: Напрямую Ozon блокирует обычные HTTP-запросы с помощью Cloudflare.
    Если у вас Ozon Global (заказ из-за рубежа), у него есть международный трек (типа CEL...), 
    его проще всего отслеживать, подключив сюда бесплатное API от Track24 или 17track.
    Ниже написан каркас. Для демонстрации работы он возвращает имитацию статуса.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    proxies = {"all://": proxy} if proxy else None

    try:
        # Сюда вы встроите реальный запрос к API трекера или парсеру.
        # Пример: response = await client.get(f"https://api.track24.ru/tracking?code={track_id}", headers=headers)
        async with httpx.AsyncClient(proxies=proxies, headers=headers, timeout=10.0) as client:
            await asyncio.sleep(0.5)  # Имитация сетевой задержки
            
            # ДЕМО-ЛОГИКА (замените на ваш парсер):
            # Возвращаем "статус", как будто он успешно спарсен
            return "В пути: Сортировочный центр Москва"
            
    except Exception as e:
        logging.error(f"Ошибка при запросе статуса для {track_id}: {e}")
        return "Ошибка обновления (сервер недоступен)"


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот для отслеживания посылок Ozon.\n\n"
        "🔹 /track — Начать отслеживание посылки\n"
        "🔹 /status — Проверить статус прямо сейчас\n\n"
        "Я буду автоматически проверять статус в фоновом режиме и пришлю уведомление, если он изменится!",
        parse_mode="HTML"
    )


@dp.message(Command("track"))
async def cmd_track(message: Message, state: FSMContext):
    await message.answer("Пришлите номер вашего заказа или трек-номер отправления:")
    await state.set_state(TrackStates.waiting_for_track)


@dp.message(TrackStates.waiting_for_track)
async def process_track_id(message: Message, state: FSMContext):
    track_id = message.text.strip()
    chat_id = message.chat.id
    
    await message.answer("🔍 Ищу рабочий прокси и запрашиваю текущий статус...")
    proxy = await get_working_proxy()
    
    status = await fetch_ozon_status(track_id, proxy)
    
    # Сохраняем в нашу базу данных в памяти
    tracking_database[chat_id] = {
        "track_id": track_id,
        "last_status": status
    }
    
    await state.clear()
    await message.answer(
        f"✅ <b>Отслеживание успешно запущено!</b>\n\n"
        f"📦 Номер: <code>{track_id}</code>\n"
        f"📍 Статус: <b>{status}</b>\n\n"
        f"Если этап доставки изменится, я сразу напишу сюда.",
        parse_mode="HTML"
    )


@dp.message(Command("status"))
async def cmd_status(message: Message):
    chat_id = message.chat.id
    if chat_id not in tracking_database:
        await message.answer("⚠️ Вы еще не добавили посылку. Нажмите /track")
        return
        
    track_id = tracking_database[chat_id]["track_id"]
    await message.answer("🔄 Обновляю данные, подождите...")
    
    proxy = await get_working_proxy()
    status = await fetch_ozon_status(track_id, proxy)
    
    tracking_database[chat_id]["last_status"] = status
    await message.answer(
        f"📦 Заказ: <code>{track_id}</code>\n"
        f"📊 Статус: <b>{status}</b>",
        parse_mode="HTML"
    )


async def auto_check_tracker():
    """Фоновая функция, которая работает параллельно и проверяет статусы"""
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        logging.info("🤖 Запуск фонового сканирования посылок...")
        
        if not tracking_database:
            continue
            
        proxy = await get_working_proxy()
        
        # Перебираем всех пользователей в базе данных
        for chat_id, data in list(tracking_database.items()):
            track_id = data["track_id"]
            old_status = data["last_status"]
            
            new_status = await fetch_ozon_status(track_id, proxy)
            
            # Если статус изменился и это не ошибка сети
            if new_status != old_status and "Ошибка" not in new_status:
                tracking_database[chat_id]["last_status"] = new_status
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"🔔 <b>ЭТАП ДОСТАВКИ ИЗМЕНИЛСЯ!</b>\n\n"
                             f"📦 Заказ: <code>{track_id}</code>\n"
                             f"❌ Было: {old_status}\n"
                             f"✅ Стало: <b>{new_status}</b>",
                        parse_mode="HTML"
                    )
                    logging.info(f"Уведомление об изменении отправлено пользователю {chat_id}")
                except Exception as e:
                    logging.error(f"Не удалось отправить сообщение пользователю {chat_id}: {e}")


async def main():
    # Запускаем фоновую задачу автоматической проверки параллельно с ботом
    asyncio.create_task(auto_check_tracker())
    # Запускаем поллинг (прием сообщений)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
