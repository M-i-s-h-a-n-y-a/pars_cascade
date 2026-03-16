import telebot
import time
import json
import threading
import logging
from telebot import types
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
from datetime import datetime, timedelta
import hashlib

logging.basicConfig(level=logging.INFO)


class AvitoParser:
    def __init__(self, bot, chat_id, url, parser_id):
        self.url = url
        self.bot = bot
        self.chat_id = chat_id
        self.parser_id = parser_id
        self.running = True
        self.processed_ads = set()
        self.first_run = True
        self.session = self.create_session()

        # Извлекаем параметры из URL
        self.search_params = self.parse_url(url)
        logging.info(f"Парсер {self.parser_id} создан: {self.search_params}")

    def create_session(self):
        """Создание сессии с заголовками как у браузера"""
        session = requests.Session()

        # Реалистичные заголовки
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        })

        return session

    def parse_url(self, url):
        """Извлечение параметров поиска из URL"""
        params = {}

        # Парсим URL
        import urllib.parse
        parsed = urllib.parse.urlparse(url)

        # Извлекаем путь
        path_parts = parsed.path.split('/')

        # Определяем регион
        if len(path_parts) > 1:
            params['region'] = path_parts[1]

        # Определяем категорию
        if 'telefony' in url:
            params['category'] = 'telefony'
        elif 'noutbuki' in url:
            params['category'] = 'noutbuki'
        elif 'avtomobili' in url:
            params['category'] = 'avtomobili'

        # Извлекаем поисковый запрос
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            params['query'] = query_params['q'][0]

        # Фильтры
        params['s'] = '104'  # Только частные

        return params

    def fetch_ads(self):
        """Получение объявлений через API Avito"""
        try:
            # Формируем запрос к API
            api_url = "https://www.avito.ru/web/1/main/items"

            # Параметры запроса
            params = {
                'forceLocation': False,
                'lastStamp': 0,
                'limit': 30,
                'categoryId': 24,  # Телефоны (может меняться)
                'params[504]': 3914,  # Apple
                's': 104,  # Только частные
                'sort': 507,  # Сортировка по дате
            }

            # Добавляем поисковый запрос
            if 'query' in self.search_params:
                params['q'] = self.search_params['query']

            # Выполняем запрос
            response = self.session.get(api_url, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json()
                return self.parse_api_response(data)
            else:
                logging.error(f"Ошибка API: {response.status_code}")
                return []

        except Exception as e:
            logging.error(f"Ошибка при запросе к API: {e}")
            return []

    def parse_api_response(self, data):
        """Парсинг ответа API"""
        ads = []

        try:
            items = data.get('items', [])

            for item in items:
                try:
                    # Извлекаем данные
                    title = item.get('title', 'Без названия')
                    price = item.get('price', {}).get('value', 'Цена не указана')
                    if price != 'Цена не указана':
                        price = f"{price} ₽"

                    # Дата публикации
                    time_value = item.get('sortTime', 0)
                    if time_value:
                        pub_date = datetime.fromtimestamp(time_value / 1000)
                        time_str = self.format_date(pub_date)
                    else:
                        time_str = 'Дата неизвестна'

                    # Ссылка
                    link = f"https://www.avito.ru{item.get('uri', '')}"

                    # ID объявления
                    ad_id = str(item.get('id', ''))

                    ads.append({
                        'title': title,
                        'price': price,
                        'date': time_str,
                        'datetime': pub_date if time_value else None,
                        'link': link,
                        'id': ad_id
                    })

                except Exception as e:
                    logging.error(f"Ошибка парсинга элемента: {e}")
                    continue

        except Exception as e:
            logging.error(f"Ошибка парсинга ответа: {e}")

        return ads

    def format_date(self, dt):
        """Форматирование даты"""
        now = datetime.now()
        delta = now - dt

        if delta.total_seconds() < 60:
            return "только что"
        elif delta.total_seconds() < 3600:
            minutes = int(delta.total_seconds() / 60)
            return f"{minutes} минут назад"
        elif delta.total_seconds() < 7200:
            return "1 час назад"
        elif delta.total_seconds() < 86400 and dt.date() == now.date():
            return f"сегодня в {dt.strftime('%H:%M')}"
        elif delta.total_seconds() < 172800 and dt.date() == (now - timedelta(days=1)).date():
            return f"вчера в {dt.strftime('%H:%M')}"
        else:
            return dt.strftime('%d.%m.%Y')

    def is_new_ad(self, date_str, ad_dt):
        """Проверка, является ли объявление новым (до 30 минут)"""
        if not ad_dt:
            return False

        now = datetime.now()
        delta = now - ad_dt

        # Новое, если прошло меньше 30 минут
        return delta.total_seconds() < 1800

    def safe_send_message(self, text):
        """Безопасная отправка сообщения"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.bot.send_message(
                    self.chat_id,
                    text,
                    parse_mode=None,
                    disable_web_page_preview=False,
                    timeout=30
                )
                return True
            except Exception as e:
                logging.error(f"Ошибка отправки (попытка {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
        return False

    def parse(self):
        """Основной цикл парсинга"""
        logging.info(f"Парсер {self.parser_id}: Запущен")

        while self.running:
            try:
                # Получаем объявления
                ads = self.fetch_ads()

                if not ads:
                    logging.warning(f"Парсер {self.parser_id}: Нет объявлений")
                    time.sleep(30)
                    continue

                logging.info(f"Парсер {self.parser_id}: Получено {len(ads)} объявлений")

                # Обрабатываем объявления
                new_ads_found = 0
                for ad in ads[:10]:  # Проверяем первые 10
                    try:
                        # Создаем ID для отслеживания дубликатов
                        ad_hash = hashlib.md5(f"{ad['id']}_{ad['title']}".encode()).hexdigest()

                        # Проверяем дубликаты
                        if ad_hash in self.processed_ads:
                            continue

                        # Первый запуск - только запоминаем
                        if self.first_run:
                            self.processed_ads.add(ad_hash)
                            logging.info(f"Парсер {self.parser_id}: Запомнено: {ad['title']} ({ad['date']})")
                            continue

                        # Проверяем, новое ли объявление
                        if self.is_new_ad(ad['date'], ad.get('datetime')):
                            self.processed_ads.add(ad_hash)

                            # Формируем сообщение
                            message = f"🔔 НОВОЕ ОБЪЯВЛЕНИЕ! 🔔\n\n"
                            message += f"📱 {ad['title']}\n"
                            message += f"💰 {ad['price']}\n"
                            message += f"⏰ {ad['date']}\n"
                            message += f"🔗 {ad['link']}\n"
                            message += f"\n#{self.parser_id}"

                            # Отправляем
                            if self.safe_send_message(message):
                                new_ads_found += 1
                                logging.info(f"Парсер {self.parser_id}: Отправлено новое: {ad['title']}")
                                time.sleep(1)  # Задержка между отправками

                    except Exception as e:
                        logging.error(f"Парсер {self.parser_id}: Ошибка обработки: {e}")

                # Первый запуск завершен
                if self.first_run and not self.first_run_completed:
                    self.first_run = False
                    logging.info(
                        f"Парсер {self.parser_id}: Первый запуск завершен, запомнено {len(self.processed_ads)} объявлений")
                    self.safe_send_message(f"✅ Парсер {self.parser_id} готов! Отслеживаю новые объявления...")

                # Ограничиваем размер множества обработанных
                if len(self.processed_ads) > 200:
                    self.processed_ads = set(list(self.processed_ads)[-150:])

                if new_ads_found > 0:
                    logging.info(f"Парсер {self.parser_id}: Найдено новых: {new_ads_found}")

                # Пауза между проверками
                time.sleep(30)  # Проверяем каждые 30 секунд

            except Exception as e:
                logging.error(f"Парсер {self.parser_id}: Ошибка в цикле: {e}")
                time.sleep(60)


def create_telegram_session():
    """Создание сессии с повышенными таймаутами"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


TOKEN = '8698763672:AAHmNU2cfMdtviz5tCuB-d2pADEgYWKiIzU'
bot = telebot.TeleBot(TOKEN)
bot.session = create_telegram_session()

chat_data = {}
parsers = {}


def load_chat_data():
    global chat_data
    try:
        if os.path.exists('chat_data.txt') and os.path.getsize('chat_data.txt') > 0:
            with open('chat_data.txt', 'r', encoding='utf-8') as file:
                chat_data = json.load(file)
                logging.info(f"Данные чатов загружены")
        else:
            chat_data = {}
            save_chat_data()
    except Exception as e:
        logging.error(f"Ошибка загрузки данных: {e}")
        chat_data = {}


def save_chat_data():
    try:
        with open('chat_data.txt', 'w', encoding='utf-8') as file:
            json.dump(chat_data, file, indent=2, ensure_ascii=False)
        logging.info("Данные сохранены")
    except Exception as e:
        logging.error(f"Ошибка сохранения: {e}")


def safe_bot_send_message(chat_id, text, reply_markup=None):
    """Безопасная отправка сообщения"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            bot.send_message(
                chat_id,
                text,
                reply_markup=reply_markup,
                parse_mode=None,
                timeout=30
            )
            return True
        except Exception as e:
            logging.error(f"Ошибка отправки (попытка {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
    return False


def show_help(chat_id):
    """Показать помощь"""
    help_text = """
📚 Доступные команды:

/start - Показать меню
/add [ссылка] - Добавить ссылку
/list - Список ссылок
/delete [номер] - Удалить ссылку
/stopall - Остановить все парсеры
/status - Статус

Пример:
/add https://www.avito.ru/moskva/telefony?q=iphone
    """
    safe_bot_send_message(chat_id, help_text)


@bot.message_handler(commands=['start'])
def start(message):
    chat_id = str(message.chat.id)

    if chat_id not in chat_data:
        chat_data[chat_id] = {
            'urls': [],
            'name': message.chat.title if hasattr(message.chat, 'title') else 'Чат'
        }
        save_chat_data()

    welcome_text = f"""
👋 Привет! Я бот для отслеживания новых объявлений на Avito.

📌 **Как использовать:**
1. Скопируйте ссылку с поиском на Avito
2. Добавьте командой: /add [ссылка]
3. Я буду присылать новые объявления в этот чат

🔍 **Пример ссылки:**
https://www.avito.ru/moskva/telefony?q=iphone+13

ℹ️ ID чата: {chat_id}
    """

    safe_bot_send_message(chat_id, welcome_text)
    show_help(chat_id)


@bot.message_handler(commands=['add'])
def add_url(message):
    chat_id = str(message.chat.id)

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        safe_bot_send_message(chat_id,
                              "❌ Укажите ссылку после команды\nПример: /add https://www.avito.ru/moskva/telefony?q=iphone")
        return

    url = parts[1].strip()

    if not url.startswith(('http://', 'https://')):
        safe_bot_send_message(chat_id, "❌ Некорректная ссылка")
        return

    if 'avito.ru' not in url:
        safe_bot_send_message(chat_id, "❌ Это не ссылка Avito")
        return

    if chat_id not in chat_data:
        chat_data[chat_id] = {'urls': []}

    if url in chat_data[chat_id]['urls']:
        safe_bot_send_message(chat_id, "⚠️ Эта ссылка уже отслеживается")
        return

    # Добавляем ссылку
    chat_data[chat_id]['urls'].append(url)
    parser_id = f"Парсер{len(chat_data[chat_id]['urls'])}"

    save_chat_data()

    safe_bot_send_message(
        chat_id,
        f"✅ Ссылка добавлена!\n"
        f"🆔 {parser_id}\n\n"
        f"📢 Запоминаю текущие объявления..."
    )

    # Запускаем парсер
    start_parsing(chat_id, url, parser_id)


@bot.message_handler(commands=['list'])
def list_urls(message):
    chat_id = str(message.chat.id)

    if chat_id not in chat_data or not chat_data[chat_id]['urls']:
        safe_bot_send_message(chat_id, "📭 Нет отслеживаемых ссылок")
        return

    text = "📋 **Ваши ссылки:**\n\n"
    for i, url in enumerate(chat_data[chat_id]['urls'], 1):
        # Сокращаем ссылку для красоты
        if len(url) > 50:
            short = url[:50] + "..."
        else:
            short = url
        text += f"{i}. `{short}`\n"

    # Считаем активные парсеры
    active = sum(1 for k in parsers.keys() if k.startswith(f"{chat_id}_"))
    text += f"\n🟢 Активных парсеров: {active}"
    text += f"\n\nДля удаления: /delete НОМЕР"

    safe_bot_send_message(chat_id, text)


@bot.message_handler(commands=['delete'])
def delete_url(message):
    chat_id = str(message.chat.id)

    parts = message.text.split()
    if len(parts) < 2:
        safe_bot_send_message(chat_id, "❌ Укажите номер ссылки\nПример: /delete 1")
        return

    try:
        idx = int(parts[1]) - 1
    except ValueError:
        safe_bot_send_message(chat_id, "❌ Введите число")
        return

    if chat_id not in chat_data or idx >= len(chat_data[chat_id]['urls']):
        safe_bot_send_message(chat_id, "❌ Неверный номер")
        return

    url = chat_data[chat_id]['urls'].pop(idx)
    save_chat_data()

    # Останавливаем парсер
    for key in list(parsers.keys()):
        if key.startswith(f"{chat_id}_") and url in key:
            parsers[key].running = False
            del parsers[key]
            break

    safe_bot_send_message(chat_id, "✅ Ссылка удалена")


@bot.message_handler(commands=['stopall'])
def stop_all(message):
    chat_id = str(message.chat.id)
    stopped = 0

    for key in list(parsers.keys()):
        if key.startswith(f"{chat_id}_"):
            parsers[key].running = False
            del parsers[key]
            stopped += 1

    safe_bot_send_message(chat_id, f"✅ Остановлено парсеров: {stopped}")


@bot.message_handler(commands=['status'])
def status(message):
    chat_id = str(message.chat.id)

    urls_count = len(chat_data.get(chat_id, {}).get('urls', []))
    active = sum(1 for k in parsers.keys() if k.startswith(f"{chat_id}_"))

    text = f"📊 **Статус:**\n"
    text += f"📝 Всего ссылок: {urls_count}\n"
    text += f"🟢 Активных парсеров: {active}"

    safe_bot_send_message(chat_id, text)


@bot.message_handler(commands=['help'])
def help_command(message):
    show_help(message.chat.id)


def start_parsing(chat_id, url, parser_id):
    """Запуск парсера в отдельном потоке"""
    parser = AvitoParser(bot, chat_id, url, parser_id)
    parser_key = f"{chat_id}_{url}"
    parsers[parser_key] = parser
    thread = threading.Thread(target=parser.parse)
    thread.daemon = True
    thread.start()
    logging.info(f"Запущен {parser_id} для чата {chat_id}")


if __name__ == "__main__":
    load_chat_data()
    logging.info("Бот запущен")

    # Запускаем все сохраненные парсеры
    for chat_id, data in chat_data.items():
        for i, url in enumerate(data.get('urls', []), 1):
            parser_id = f"Парсер{i}"
            start_parsing(chat_id, url, parser_id)

    # Запускаем бота с обработкой ошибок
    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=30)
        except Exception as e:
            logging.error(f"Ошибка polling: {e}")
            time.sleep(10)
