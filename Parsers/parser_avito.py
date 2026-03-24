import csv
import time
import random
import re
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class AvitoParser:
    def __init__(self):
        self.setup_driver()
        self.results = []

    def setup_driver(self):
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--lang=ru')

        self.driver = webdriver.Chrome(options=chrome_options)

    def random_delay(self, min_sec=2, max_sec=4):
        time.sleep(random.uniform(min_sec, max_sec))

    def extract_price(self, text):
        if not text:
            return 0
        numbers = re.findall(r'(\d+)', text.replace(' ', ''))
        return int(numbers[0]) if numbers else 0

    def extract_guests_count(self):
        """Извлечение количества гостей из параметров объявления"""
        guests = 0

        # Способ 1: ищем в параметрах по атрибуту
        try:
            # Ищем элемент с количеством гостей в параметрах
            params = self.driver.find_elements(By.CSS_SELECTOR, '[data-marker="item-view/item-params"] li')
            for param in params:
                param_text = param.text.lower()
                if 'гост' in param_text or 'человек' in param_text or 'мест' in param_text:
                    # Извлекаем число
                    numbers = re.findall(r'(\d+)', param_text)
                    if numbers:
                        guests = int(numbers[0])
                        logger.info(f"  Количество гостей найдено в параметрах: {guests}")
                        return guests
        except:
            pass

        # Способ 2: ищем в описании
        try:
            desc = self.driver.find_element(By.CSS_SELECTOR, '[data-marker="item-view/item-description"]').text.lower()
            # Ищем паттерны типа "до 4 человек", "4 гостя", "вместимость 4 человека"
            patterns = [
                r'до\s*(\d+)\s*(?:человек|гост|мест)',
                r'(\d+)\s*(?:человек|гост|мест)',
                r'вместимость\s*[:\s]*(\d+)',
                r'максимум\s*(\d+)\s*(?:человек|гост|мест)'
            ]

            for pattern in patterns:
                match = re.search(pattern, desc)
                if match:
                    guests = int(match.group(1))
                    logger.info(f"  Количество гостей найдено в описании: {guests}")
                    return guests
        except:
            pass

        # Способ 3: ищем в заголовке
        try:
            title = self.driver.find_element(By.CSS_SELECTOR, 'h1').text.lower()
            match = re.search(r'(\d+)[-\s]*(?:х|и)?\s*(?:местный|мест|комнат)', title)
            if match:
                guests = int(match.group(1))
                logger.info(f"  Количество гостей найдено в заголовке: {guests}")
                return guests
        except:
            pass

        return guests

    def get_property_type(self, title):
        title = title.lower()
        if any(word in title for word in ['дом', 'коттедж', 'дача']):
            return 'house'
        elif 'квартир' in title:
            return 'apartment'
        elif any(word in title for word in ['комнат', 'койко']):
            return 'room'
        else:
            return 'other'

    def extract_coordinates(self):
        """Извлечение координат из страницы объявления"""
        lat, lon = 0.0, 0.0

        # Способ 1: из data-атрибутов карты
        try:
            map_element = self.driver.find_element(By.CSS_SELECTOR, '[data-marker="item-view/map"]')
            if map_element:
                data_lat = map_element.get_attribute('data-map-lat')
                data_lon = map_element.get_attribute('data-map-lon')
                if data_lat and data_lon:
                    lat, lon = float(data_lat), float(data_lon)
                    return lat, lon
        except:
            pass

        # Способ 2: из src iframe с картой
        try:
            map_element = self.driver.find_element(By.CSS_SELECTOR, '[data-marker="item-view/map"]')
            map_src = map_element.get_attribute('src')
            if map_src and 'll=' in map_src:
                coords = map_src.split('ll=')[1].split('&')[0]
                if '%2C' in coords:
                    lon, lat = coords.split('%2C')
                else:
                    lon, lat = coords.split(',')
                lat, lon = float(lat), float(lon)
                return lat, lon
        except:
            pass

        # Способ 3: из скриптов с координатами
        try:
            time.sleep(1)
            page_source = self.driver.page_source

            json_match = re.search(r'"geo":\s*\{\s*"latitude":\s*([\d.]+),\s*"longitude":\s*([\d.]+)', page_source)
            if json_match:
                lat, lon = float(json_match.group(1)), float(json_match.group(2))
                return lat, lon

            data_match = re.search(r'data-map-lat="([\d.]+)".*?data-map-lon="([\d.]+)"', page_source)
            if data_match:
                lat, lon = float(data_match.group(1)), float(data_match.group(2))
                return lat, lon

        except:
            pass

        return lat, lon

    def get_total_pages(self):
        """Получение общего количества страниц"""
        try:
            pagination = self.driver.find_elements(By.CSS_SELECTOR, '[data-marker*="pagination-button"]')
            if pagination:
                last_page = pagination[-1].text
                if last_page.isdigit():
                    return int(last_page)

            page_links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="p="]')
            pages = []
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
        """Проверка наличия блока с объявлениями из других регионов"""
        try:
            page_text = self.driver.find_element(By.TAG_NAME, 'body').text
            page_text_lower = page_text.lower()

            stop_phrases = [
                'объявления в других городах',
                'объявлений есть в других городах',
                'есть в других городах',
                'объявлений в других городах',
                'показать объявления из других городов',
                'найдено в других городах',
                'объявление в других городах',
            ]

            for phrase in stop_phrases:
                if phrase in page_text_lower:
                    return True

            if re.search(r'\d+\s*объявлени[яй]\s+есть\s+в\s+других\s+город(ах|а)?', page_text_lower):
                return True

            return False

        except Exception as e:
            logger.debug(f"Ошибка проверки блока других регионов: {e}")
            return False

    def get_listing_urls(self, category_url, category_name):
        """Получение ссылок, собирая объявления ДО обнаружения стоп-фразы"""
        urls = []

        logger.info(f"Загрузка первой страницы...")
        self.driver.get(category_url)
        self.random_delay(3, 5)

        try:
            close_btn = self.driver.find_element(By.CSS_SELECTOR, '[data-marker="popup-close"]')
            close_btn.click()
            self.random_delay(1, 2)
        except:
            pass

        total_pages = self.get_total_pages()
        logger.info(f"Всего страниц: {total_pages}")

        # Флаг для отслеживания обнаружения стоп-фразы
        stop_collecting = False

        for page in range(1, total_pages + 1):
            if page > 1:
                page_url = f"{category_url}?p={page}"
                logger.info(f"Загрузка страницы {page}...")
                self.driver.get(page_url)
                self.random_delay(3, 5)

            # Проверяем наличие стоп-фразы на текущей странице
            has_stop = self.has_other_regions_block()
            if has_stop:
                logger.info(f"⚠️ На странице {page} обнаружена стоп-фраза")

            # Собираем объявления с текущей страницы
            items = self.driver.find_elements(By.CSS_SELECTOR, '[data-marker="item"]')

            page_urls = []
            for item in items:
                try:
                    link = item.find_element(By.CSS_SELECTOR, '[data-marker="item-title"]').get_attribute('href')
                    if link and 'avito.ru' in link:
                        page_urls.append(link)
                except:
                    continue

            urls.extend(page_urls)
            logger.info(f"Страница {page}: собрано {len(page_urls)} объявлений")

            # Если нашли стоп-фразу, прекращаем сбор дальше
            if has_stop:
                logger.info(f"🛑 Останавливаем сбор после страницы {page}")
                break

            # Проверяем следующую страницу
            if page < total_pages:
                try:
                    next_button = self.driver.find_element(By.CSS_SELECTOR, '[data-marker*="pagination/next"]')
                    if not next_button.is_enabled():
                        logger.info("Кнопка 'Далее' неактивна, завершаем пагинацию")
                        break
                except:
                    pass

            self.random_delay(1, 2)

        logger.info(f"Всего собрано URL: {len(urls)}")
        return urls

    def parse_listing(self, url):
        """Парсинг одного объявления"""
        try:
            self.driver.get(url)
            self.random_delay(3, 6)

            try:
                close_btn = self.driver.find_element(By.CSS_SELECTOR, '[data-marker="popup-close"]')
                close_btn.click()
                time.sleep(1)
            except:
                pass

            # Название
            title = ''
            try:
                title = self.driver.find_element(By.CSS_SELECTOR,
                                                 'h1, [data-marker="item-view/title-info"]').text.strip()
            except:
                pass

            # Цена
            price = 0
            try:
                meta_price = self.driver.find_element(By.CSS_SELECTOR, 'meta[property="product:price:amount"]')
                price = int(meta_price.get_attribute('content'))
            except:
                try:
                    price_elem = self.driver.find_element(By.CSS_SELECTOR,
                                                          '[itemprop="price"], [data-marker="item-view/item-price"]')
                    price = self.extract_price(price_elem.text)
                except:
                    pass

            # Адрес
            address = ''
            try:
                address = self.driver.find_element(By.CSS_SELECTOR,
                                                   '[data-marker="item-view/item-address"]').text.strip()
                if not address:
                    address = self.driver.find_element(By.CSS_SELECTOR,
                                                       '[class*="address"]').text.strip()
            except:
                try:
                    address = self.driver.find_element(By.CSS_SELECTOR,
                                                       '[class*="address"]').text.strip()
                except:
                    pass

            # Координаты
            lat, lon = self.extract_coordinates()

            # Количество гостей
            guests = self.extract_guests_count()

            # Тип
            prop_type = self.get_property_type(title)

            return {
                'title': title,
                'price': price,
                'address': address,
                'latitude': lat,
                'longitude': lon,
                'guests': guests,
                'type': prop_type,
                'url': url
            }

        except Exception as e:
            logger.error(f"Ошибка при парсинге {url}: {e}")
            return None

    def save_to_csv(self, filename='avito_properties.csv'):
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f,
                                    fieldnames=['title', 'price', 'address', 'latitude', 'longitude', 'guests', 'type',
                                                'url'])
            writer.writeheader()
            writer.writerows(self.results)

        logger.info(f"\n✅ Данные сохранены в {filename}")
        logger.info(f"Всего сохранено: {len(self.results)} объектов")

    def run(self):
        categories = {
            'Квартиры': 'https://www.avito.ru/baykalsk/kvartiry/sdam/posutochno',
            'Дома': 'https://www.avito.ru/baykalsk/doma_dachi_kottedzhi/sdam/posutochno',
            'Комнаты': 'https://www.avito.ru/baykalsk/komnaty/sdam/posutochno'
        }

        logger.info("=" * 50)
        logger.info("ПАРСЕР AVITO")
        logger.info("=" * 50)

        all_urls = []

        for cat_name, cat_url in categories.items():
            logger.info(f"\n--- {cat_name} ---")
            urls = self.get_listing_urls(cat_url, cat_name)
            all_urls.extend(urls)
            logger.info(f"Всего найдено в категории: {len(urls)} объявлений")

        logger.info(f"\nВсего найдено: {len(all_urls)} объявлений")
        logger.info("=" * 50)

        for i, url in enumerate(all_urls, 1):
            logger.info(f"[{i}/{len(all_urls)}] Парсинг...")

            data = self.parse_listing(url)

            if data and data['price'] > 0:
                self.results.append(data)
                addr_preview = data['address'][:30] + '...' if len(data['address']) > 30 else data['address']
                logger.info(
                    f"  ✓ {data['title'][:30]}... {data['price']} ₽, гостей: {data['guests']}, адрес: {addr_preview}")
            else:
                logger.info(f"  ✗ Пропущено (нет цены)")

            self.random_delay(1, 2)

        self.save_to_csv()
        self.driver.quit()


if __name__ == "__main__":
    parser = AvitoParser()
    parser.run()