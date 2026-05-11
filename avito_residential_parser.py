"""
Парсер Avito для посуточной аренды жилья (квартиры, дома, комнаты) в Байкальске.
Сохраняет данные в avito_properties.csv.
"""

import csv
import time
import random
import re
import json
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class AvitoParser:
    def __init__(self):
        self.setup_driver()
        self.results = []
        self.filename = '../data/avito_properties.csv'

        # Создаём файл с заголовками, если он отсутствует
        if not os.path.exists(self.filename):
            with open(self.filename, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f,
                                        fieldnames=['title', 'price', 'address', 'latitude', 'longitude', 'guests',
                                                    'type', 'url'])
                writer.writeheader()

    def setup_driver(self):
        chrome_options = Options()
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--lang=ru')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def random_delay(self, min_sec=5, max_sec=10):
        time.sleep(random.uniform(min_sec, max_sec))

    def extract_price(self, text):
        if not text:
            return 0
        numbers = re.findall(r'(\d+)', text.replace('\u2009', '').replace(' ', ''))
        return int(numbers[0]) if numbers else 0

    def extract_address(self):
        """Извлечение адреса из объявления."""
        address = ''

        # Попытка через data-marker
        try:
            address_element = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-marker="item-view/item-address"]'))
            )
            address = address_element.text.strip()
            if address:
                logger.info(f"  Адрес найден (data-marker): {address[:50]}...")
                return re.sub(r'\s+', ' ', address)
        except:
            pass

        # Поиск по классам, содержащим "address"
        try:
            address_selectors = [
                'span[class*="address"]',
                'div[class*="address"]',
                'a[class*="address"]',
                '[class*="style-item-address"]',
                '[class*="address"]',
                'div[data-marker*="address"]'
            ]
            for selector in address_selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    text = elem.text.strip()
                    if text and len(text) > 5:
                        address = re.sub(r'\s+', ' ', text)
                        logger.info(f"  Адрес найден (по классу {selector}): {address[:50]}...")
                        return address
        except:
            pass

        # JSON-LD
        try:
            scripts = self.driver.find_elements(By.CSS_SELECTOR, 'script[type="application/ld+json"]')
            for script in scripts:
                try:
                    data = json.loads(script.get_attribute('innerHTML'))
                    if isinstance(data, dict):
                        if 'address' in data:
                            if isinstance(data['address'], dict):
                                address = data['address'].get('streetAddress', '')
                                if not address:
                                    address = (data['address'].get('addressLocality', '') +
                                               ', ' + data['address'].get('streetAddress', ''))
                            elif isinstance(data['address'], str):
                                address = data['address']
                        elif 'location' in data and isinstance(data['location'], dict):
                            address = data['location'].get('address', '')
                        if address:
                            logger.info(f"  Адрес найден (JSON-LD): {address[:50]}...")
                            return address
                except:
                    continue
        except:
            pass

        # Поиск в JSON-данных страницы
        try:
            page_source = self.driver.page_source
            patterns = [
                r'"address":"([^"]+)"',
                r'"address"\s*:\s*"([^"]+)"',
                r'"item-address"[^>]*>([^<]+)<',
                r'"addressLocality":"([^"]+)"'
            ]
            for pattern in patterns:
                matches = re.findall(pattern, page_source)
                for match in matches:
                    if match and len(match) > 5 and ('Байкальск' in match or 'ул' in match or 'микрорайон' in match):
                        address = match
                        logger.info(f"  Адрес найден (RegEx): {address[:50]}...")
                        return address
        except:
            pass

        # Текст страницы
        try:
            body_text = self.driver.find_element(By.TAG_NAME, 'body').text
            lines = body_text.split('\n')
            for line in lines:
                line = line.strip()
                if 'Байкальск' in line and any(
                        word in line.lower() for word in ['ул', 'улица', 'микрорайон', 'мкр', 'дом']):
                    address = line
                    logger.info(f"  Адрес найден (текст страницы): {address[:50]}...")
                    return address
        except:
            pass

        logger.warning("  Адрес не найден")
        return address

    def extract_guests_count(self):
        """Определение количества гостей (вместимости)."""
        guests = 0

        # Параметры объявления
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-marker="item-view/item-params"]'))
            )
            params = self.driver.find_elements(By.CSS_SELECTOR, '[data-marker="item-view/item-params"] li')
            for param in params:
                param_text = param.text.lower()
                if any(word in param_text for word in ['гост', 'человек', 'мест', 'спальн']):
                    numbers = re.findall(r'(\d+)', param_text)
                    if numbers:
                        guests = int(numbers[0])
                        logger.info(f"  Количество гостей найдено в параметрах: {guests}")
                        return guests
        except:
            pass

        # Описание
        try:
            desc_element = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-marker="item-view/item-description"]'))
            )
            desc = desc_element.text.lower()
            patterns = [
                r'до\s*(\d+)\s*(?:человек|гост|мест)',
                r'(\d+)\s*(?:человек|гост|мест)',
                r'вместимость\s*[:\s]*(\d+)',
                r'максимум\s*(\d+)\s*(?:человек|гост|мест)',
                r'(\d+)[-\s]*(?:х|и)?\s*(?:местный|мест)'
            ]
            for pattern in patterns:
                match = re.search(pattern, desc)
                if match:
                    guests = int(match.group(1))
                    logger.info(f"  Количество гостей найдено в описании: {guests}")
                    return guests
        except:
            pass

        # Заголовок
        try:
            title = self.driver.find_element(By.CSS_SELECTOR, 'h1').text.lower()
            patterns = [
                r'на\s*(\d+)\s*человек',
                r'(\d+)[-\s]*(?:х|и)?\s*мест'
            ]
            for pattern in patterns:
                match = re.search(pattern, title)
                if match:
                    guests = int(match.group(1))
                    logger.info(f"  Количество гостей найдено в заголовке: {guests}")
                    return guests
        except:
            pass

        return 2  # значение по умолчанию

    def extract_coordinates(self):
        """Извлечение координат из карточки объявления."""
        lat, lon = 0.0, 0.0

        try:
            time.sleep(2)
            page_source = self.driver.page_source
            json_patterns = [
                r'"coordinates":\s*\{\s*"lat":\s*([\d.]+),\s*"lng":\s*([\d.]+)',
                r'"latitude":\s*([\d.]+),\s*"longitude":\s*([\d.]+)',
                r'"lat":\s*([\d.]+),\s*"lon":\s*([\d.]+)',
                r'center=\[([\d.]+),([\d.]+)\]',
                r'"geo":\s*\{\s*"lat":\s*([\d.]+),\s*"lng":\s*([\d.]+)',
                r'"map":\s*\{\s*"lat":\s*([\d.]+),\s*"lng":\s*([\d.]+)'
            ]
            for pattern in json_patterns:
                match = re.search(pattern, page_source)
                if match and len(match.groups()) == 2:
                    lat, lon = float(match.group(1)), float(match.group(2))
                    logger.info(f"  Координаты найдены в JSON: {lat}, {lon}")
                    return lat, lon
        except:
            pass

        # data-атрибуты карты
        try:
            map_elements = self.driver.find_elements(By.CSS_SELECTOR, '[data-map-lat], [data-map-lon]')
            for elem in map_elements:
                data_lat = elem.get_attribute('data-map-lat')
                data_lon = elem.get_attribute('data-map-lon')
                if data_lat and data_lon:
                    lat, lon = float(data_lat), float(data_lon)
                    logger.info(f"  Координаты найдены в data-атрибутах: {lat}, {lon}")
                    return lat, lon
        except:
            pass

        # iframe с картой
        try:
            iframes = self.driver.find_elements(By.TAG_NAME, 'iframe')
            for iframe in iframes:
                src = iframe.get_attribute('src')
                if src and ('yandex' in src or 'maps' in src):
                    coord_match = re.search(r'll=([\d.]+),([\d.]+)', src)
                    if coord_match:
                        lon, lat = float(coord_match.group(1)), float(coord_match.group(2))
                        logger.info(f"  Координаты найдены в URL карты: {lat}, {lon}")
                        return lat, lon
        except:
            pass

        return lat, lon

    def get_property_type(self, title):
        title = title.lower()
        if any(word in title for word in ['дом', 'коттедж', 'дача', 'таунхаус']):
            return 'house'
        elif any(word in title for word in ['квартир', 'студи']):
            return 'apartment'
        elif any(word in title for word in ['комнат', 'койко', 'койка']):
            return 'room'
        else:
            return 'other'

    def get_total_pages(self):
        """Определение общего числа страниц пагинации."""
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-marker*="pagination"]'))
            )
            pagination_buttons = self.driver.find_elements(By.CSS_SELECTOR, 'a[data-marker*="pagination-button"]')
            pages = []
            for button in pagination_buttons:
                try:
                    page_num = int(button.text)
                    pages.append(page_num)
                except:
                    pass
            if pages:
                return max(pages)

            page_links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="p="]')
            for link in page_links:
                href = link.get_attribute('href')
                match = re.search(r'p=(\d+)', href)
                if match:
                    pages.append(int(match.group(1)))
            if pages:
                return max(pages)
            return 1
        except Exception as e:
            logger.debug(f"Ошибка при определении количества страниц: {e}")
            return 1

    def has_other_regions_block(self):
        """Проверяет наличие блока «Объявления в других городах»."""
        try:
            page_text = self.driver.find_element(By.TAG_NAME, 'body').text.lower()
            stop_phrases = [
                'объявления в других городах',
                'объявлений в других городах',
                'есть в других городах',
                'показать объявления из других городов',
                'найдено в других городах',
                'показывать объявления из других городов'
            ]
            for phrase in stop_phrases:
                if phrase in page_text:
                    logger.info(f"Найдена стоп-фраза: {phrase}")
                    return True
            return False
        except Exception as e:
            logger.debug(f"Ошибка проверки блока других регионов: {e}")
            return False

    def get_listing_urls(self, category_url, category_name):
        """Сбор ссылок на объявления с пагинацией и остановкой на сторонних городах."""
        urls = []
        logger.info(f"Загрузка первой страницы {category_name}...")
        self.driver.get(category_url)
        self.random_delay(5, 8)

        # Закрываем возможные попапы
        try:
            close_buttons = self.driver.find_elements(By.CSS_SELECTOR,
                                                      '[data-marker="popup-close"], .modal-close, button[class*="close"]')
            for btn in close_buttons:
                if btn.is_displayed():
                    btn.click()
                    time.sleep(1)
        except:
            pass

        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        total_pages = self.get_total_pages()
        logger.info(f"Всего страниц: {total_pages}")

        for page in range(1, total_pages + 1):
            if page > 1:
                page_url = f"{category_url}?p={page}"
                logger.info(f"Загрузка страницы {page}...")
                self.driver.get(page_url)
                self.random_delay(5, 8)
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)

            # Сбор ссылок
            items = self.driver.find_elements(By.CSS_SELECTOR, '[data-marker="item"]')
            if not items:
                items = self.driver.find_elements(By.CSS_SELECTOR,
                                                  'div[data-marker*="item"], article[data-marker*="item"], div[class*="iva-item"]')
            page_urls = []
            for item in items:
                try:
                    link = None
                    selectors = [
                        '[data-marker="item-title"]',
                        'a[data-marker*="title"]',
                        'a[class*="title"]',
                        'h3 a',
                        'a[href*="/baykalsk/"]'
                    ]
                    for selector in selectors:
                        try:
                            link_element = item.find_element(By.CSS_SELECTOR, selector)
                            link = link_element.get_attribute('href')
                            if link and 'avito.ru' in link:
                                break
                        except:
                            continue
                    if link and link not in page_urls:
                        page_urls.append(link)
                except Exception as e:
                    continue

            urls.extend(page_urls)
            logger.info(f"Страница {page}: собрано {len(page_urls)} объявлений")

            # Проверка блока других городов
            if self.has_other_regions_block():
                logger.info(f"⚠️ На странице {page} обнаружена стоп-фраза. Прекращаем сбор на следующих страницах.")
                break

            # Проверка кнопки «Далее»
            try:
                next_button = self.driver.find_element(By.CSS_SELECTOR,
                                                       '[data-marker*="pagination/next"], a[class*="pagination-next"]')
                if 'disabled' in next_button.get_attribute('class') or not next_button.is_enabled():
                    logger.info("Кнопка 'Далее' неактивна, завершаем пагинацию")
                    break
            except:
                if page < total_pages:
                    try:
                        next_url = f"{category_url}?p={page + 1}"
                        self.driver.get(next_url)
                        time.sleep(3)
                        if "404" in self.driver.title or "не найдена" in self.driver.page_source:
                            break
                    except:
                        break

        logger.info(f"Всего собрано URL в категории {category_name}: {len(urls)}")
        return urls

    def parse_listing(self, url):
        """Парсинг одного объявления."""
        try:
            logger.info(f"Переход к объявлению: {url}")
            self.driver.get(url)
            self.random_delay(4, 7)

            try:
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'h1, [data-marker="item-view/title-info"]'))
                )
            except TimeoutException:
                logger.warning(f"Таймаут загрузки страницы {url}")
                return None

            # Закрываем попапы
            try:
                close_buttons = self.driver.find_elements(By.CSS_SELECTOR,
                                                          '[data-marker="popup-close"], .modal-close')
                for btn in close_buttons:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(1)
            except:
                pass

            # Название
            title = ''
            title_selectors = [
                'h1',
                '[data-marker="item-view/title-info"]',
                'span[class*="title"]',
                'div[class*="title"]'
            ]
            for selector in title_selectors:
                try:
                    title_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    title = title_elem.text.strip()
                    if title:
                        break
                except:
                    continue

            # Цена
            price = 0
            price_selectors = [
                '[itemprop="price"]',
                '[data-marker="item-view/item-price"]',
                'span[class*="price"]',
                'div[class*="price"]',
                'meta[property="product:price:amount"]'
            ]
            for selector in price_selectors:
                try:
                    if selector == 'meta[property="product:price:amount"]':
                        price_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        price = int(price_elem.get_attribute('content'))
                    else:
                        price_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        price = self.extract_price(price_elem.text)
                    if price > 0:
                        break
                except:
                    continue

            address = self.extract_address()
            lat, lon = self.extract_coordinates()
            guests = self.extract_guests_count()
            prop_type = self.get_property_type(title)

            result = {
                'title': title,
                'price': price,
                'address': address,
                'latitude': lat,
                'longitude': lon,
                'guests': guests,
                'type': prop_type,
                'url': url
            }
            logger.info(f"  Успешно распаршено: {title[:50]}...")
            return result

        except Exception as e:
            logger.error(f"Ошибка при парсинге {url}: {e}")
            return None

    def save_to_csv(self, data=None, append=True):
        """Сохранение данных в CSV (поддерживает дозапись)."""
        if data is None:
            data = self.results
        if not data:
            logger.warning("Нет данных для сохранения")
            return

        mode = 'a' if append and os.path.exists(self.filename) else 'w'
        with open(self.filename, mode, newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f,
                                    fieldnames=['title', 'price', 'address', 'latitude', 'longitude', 'guests', 'type',
                                                'url'])
            if mode == 'w':
                writer.writeheader()
            writer.writerows(data)
        logger.info(f"💾 Данные сохранены в {self.filename} (всего: {len(data)} записей)")

    def run(self):
        """Основной метод запуска парсера."""
        categories = {
            'Квартиры': 'https://www.avito.ru/baykalsk/kvartiry/sdam/posutochno',
            'Дома': 'https://www.avito.ru/baykalsk/doma_dachi_kottedzhi/sdam/posutochno',
            'Комнаты': 'https://www.avito.ru/baykalsk/komnaty/sdam/posutochno'
        }

        logger.info("=" * 50)
        logger.info("ПАРСЕР AVITO (посуточная аренда)")
        logger.info("=" * 50)

        all_urls = []
        for cat_name, cat_url in categories.items():
            logger.info(f"\n--- {cat_name} ---")
            urls = self.get_listing_urls(cat_url, cat_name)
            all_urls.extend(urls)
            logger.info(f"Всего найдено в категории: {len(urls)} объявлений")

        logger.info(f"\nВсего найдено: {len(all_urls)} объявлений")
        logger.info("=" * 50)

        successful_parsed = 0
        batch_results = []

        for i, url in enumerate(all_urls, 1):
            logger.info(f"\n[{i}/{len(all_urls)}] Парсинг объявления...")
            data = self.parse_listing(url)
            if data:
                batch_results.append(data)
                successful_parsed += 1
                addr_preview = data['address'][:30] + '...' if len(data['address']) > 30 else data['address']
                logger.info(
                    f"  ✓ {data['title'][:30]}... {data['price']} ₽, гостей: {data['guests']}, адрес: {addr_preview}")
                if len(batch_results) >= 5:
                    self.save_to_csv(batch_results, append=True)
                    logger.info(f"  💾 Промежуточное сохранение ({len(batch_results)} объявлений)")
                    batch_results = []
            else:
                logger.info(f"  ✗ Не удалось распарсить объявление")
            self.random_delay(2, 4)

        if batch_results:
            self.save_to_csv(batch_results, append=True)
            logger.info(f"  💾 Финальное сохранение ({len(batch_results)} объявлений)")

        self.results = []
        self.driver.quit()
        logger.info(f"\n✅ Парсинг завершен! Успешно обработано: {successful_parsed} объявлений")
        logger.info(f"📁 Данные сохранены в файл: {self.filename}")


if __name__ == "__main__":
    parser = AvitoParser()
    parser.run()