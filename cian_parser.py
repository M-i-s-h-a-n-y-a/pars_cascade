import csv
import time
import random
import re
import json
import requests
from urllib.parse import urlparse, parse_qs, quote
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException, \
    WebDriverException

import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────
#   Настройки геокодера (рекомендуется geocode.maps.co)
# ────────────────────────────────────────────────

# Получите бесплатный ключ здесь: https://geocode.maps.co/
# (регистрация занимает ~30 секунд)
GEOCODE_API_KEY = "ee93e32a-f3a6-4905-8252-d2a1852cbe35"  # ← замените на реальный ключ

# Если хотите использовать Nominatim вместо этого — закомментируйте ключ и раскомментируйте geopy ниже
USE_NOMINATIM = False

if USE_NOMINATIM:
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter


class BaikalskCianParser:
    def __init__(self):
        self.setup_driver()
        self.results = []
        self.visited_urls = set()
        self.seen_offers = set()
        self.max_pages_per_category = 50
        self.current_page_num = 1

        self.geocoded_count = 0
        self.failed_geocode = 0

        self.category_stats = {
            'apartments': 0,
            'houses': 0,
            'rooms': 0,
            'total': 0
        }

        if USE_NOMINATIM:
            self.geolocator = Nominatim(user_agent="baikalsk_cian_parser/1.0 (your_email@example.com)")
            self.geocode = RateLimiter(self.geolocator.geocode, min_delay_seconds=1.2, max_retries=3)
        else:
            self.geocode = None  # будем использовать requests

    def setup_driver(self):
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--lang=ru')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument(
            '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-software-rasterizer')
        chrome_options.add_argument('--disable-extensions')

        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.set_page_load_timeout(30)
        self.wait = WebDriverWait(self.driver, 10)

    def random_delay(self, min_sec=3, max_sec=7):
        time.sleep(random.uniform(min_sec, max_sec))

    def safe_get(self, url, retries=3):
        for attempt in range(retries):
            try:
                self.driver.get(url)
                return True
            except WebDriverException as e:
                logger.warning(f"Ошибка загрузки (попытка {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(5)
        logger.error(f"Не удалось загрузить {url} после {retries} попыток")
        return False

    def extract_price(self, text):
        if not text:
            return 0
        numbers = re.findall(r'(\d+[ \d]*)', text.replace(' ', ''))
        if numbers:
            price_str = numbers[0].replace(' ', '')
            price_str = re.sub(r'[^\d]', '', price_str)
            return int(price_str) if price_str.isdigit() else 0
        return 0

    def extract_guests_count(self, card_element):
        guests = 1
        try:
            card_text = card_element.text.lower()
            patterns = [
                r'(\d+)\s*гост', r'(\d+)\s*чел', r'до\s*(\d+)\s*чел',
                r'(\d+)\s*мест', r'(\d+)[-\s]?местный', r'(\d+)\s*человек'
            ]
            for pattern in patterns:
                match = re.search(pattern, card_text)
                if match:
                    guests = int(match.group(1))
                    break
        except:
            pass
        return guests

    def get_property_type(self, title, url):
        title_lower = title.lower()
        url_lower = url.lower()
        if any(word in title_lower for word in ['дом', 'коттедж', 'дача', 'таунхаус']):
            return 'house'
        elif any(word in title_lower for word in ['квартир', 'апартамент']):
            return 'apartment'
        elif any(word in title_lower for word in ['комнат', 'койко-место']):
            return 'room'
        elif 'студи' in title_lower:
            return 'studio'
        if '/rent/flat/' in url_lower:
            return 'apartment'
        elif '/rent/house/' in url_lower or '/rent/suburban/' in url_lower:
            return 'house'
        elif '/rent/room/' in url_lower:
            return 'room'
        return 'other'

    def extract_coordinates_from_page(self):
        lat, lon = 0.0, 0.0
        try:
            page_source = self.driver.page_source
            json_ld_pattern = r'<script type="application/ld\+json">(.*?)</script>'
            json_ld_matches = re.findall(json_ld_pattern, page_source, re.DOTALL)
            for json_str in json_ld_matches:
                try:
                    data = json.loads(json_str)
                    if isinstance(data, dict):
                        if 'geo' in data:
                            geo = data['geo']
                            if 'latitude' in geo and 'longitude' in geo:
                                lat = float(geo['latitude'])
                                lon = float(geo['longitude'])
                                if lat != 0 and lon != 0:
                                    return lat, lon
                        elif 'latitude' in data and 'longitude' in data:
                            lat = float(data['latitude'])
                            lon = float(data['longitude'])
                            return lat, lon
                except:
                    pass

            # window._cianConfig
            config_pattern = r'window\._cianConfig\s*=\s*(\[.*?\]);'
            config_match = re.search(config_pattern, page_source, re.DOTALL)
            if config_match:
                try:
                    config_data = config_match.group(1)
                    offers_pattern = r'"offers":\s*(\[.*?\])'
                    offers_match = re.search(offers_pattern, config_data, re.DOTALL)
                    if offers_match:
                        offers_data = offers_match.group(1)
                        coord_pattern = r'"geo":\s*\{[^}]*"lat":\s*([\d.]+)[^}]*"lng":\s*([\d.]+)'
                        coord_matches = re.findall(coord_pattern, offers_data)
                        if coord_matches:
                            lat, lon = float(coord_matches[0][0]), float(coord_matches[0][1])
                            return lat, lon
                except:
                    pass

        except Exception as e:
            logger.debug(f"Ошибка извлечения координат со страницы: {e}")
        return lat, lon

    def extract_coordinates_from_card(self, card_element):
        lat, lon = 0.0, 0.0
        try:
            html = card_element.get_attribute('outerHTML')
            patterns = [
                r'"coordinates":\s*\{\s*"lat":\s*([\d.]+),\s*"lng":\s*([\d.]+)\s*\}',
                r'"lat":\s*([\d.]+),\s*"lng":\s*([\d.]+)',
                r'"latitude":\s*([\d.]+),\s*"longitude":\s*([\d.]+)',
                r'\[([\d.]+),\s*([\d.]+)\]'
            ]
            for pat in patterns:
                m = re.search(pat, html, re.IGNORECASE)
                if m:
                    if '[' in pat:
                        lon, lat = float(m.group(1)), float(m.group(2))
                    else:
                        lat, lon = float(m.group(1)), float(m.group(2))
                    if lat != 0 and lon != 0:
                        return lat, lon
        except:
            pass
        return lat, lon

    def extract_address(self, card_element):
        address_parts = []
        selectors = [
            "div[data-name='GeoLabel'] a",
            "a[class*='geo']",
            "div[class*='address']",
            "div[class*='labels'] a",
            "span[class*='location']",
            "[class*='address']"
        ]
        for sel in selectors:
            try:
                els = card_element.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    txt = el.text.strip()
                    if txt and txt not in address_parts:
                        address_parts.append(txt)
            except:
                continue
        return ", ".join(address_parts)

    def geocode_address(self, address):
        """Геокодирование адреса"""
        if not address:
            return 0.0, 0.0

        full_query = f"{address}, Байкальск, Иркутская область, Россия"

        if USE_NOMINATIM:
            try:
                loc = self.geocode(full_query)
                if loc:
                    return round(loc.latitude, 6), round(loc.longitude, 6)
            except Exception as e:
                logger.debug(f"Nominatim ошибка: {e}")
            return 0.0, 0.0

        # geocode.maps.co
        if not GEOCODE_API_KEY or GEOCODE_API_KEY == "ВАШ_КЛЮЧ_СЮДА":
            logger.warning("GEOCODE_API_KEY не задан → геокодирование отключено")
            return 0.0, 0.0

        try:
            url = f"https://geocode.maps.co/search?q={quote(full_query)}&api_key={GEOCODE_API_KEY}"
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                return 0.0, 0.0
            data = r.json()
            if isinstance(data, list) and data:
                best = data[0]
                lat = float(best.get("lat", 0))
                lon = float(best.get("lon", 0))
                if lat != 0 and lon != 0:
                    self.geocoded_count += 1
                    return round(lat, 6), round(lon, 6)
        except Exception as e:
            logger.debug(f"geocode.maps.co ошибка: {e}")

        self.failed_geocode += 1
        return 0.0, 0.0

    def parse_card(self, card_element):
        result = {
            'title': '',
            'price': 0,
            'address': '',
            'latitude': 0.0,
            'longitude': 0.0,
            'guests': 1,
            'type': 'other',
            'url': ''
        }

        try:
            # url
            try:
                link = card_element.find_element(By.CSS_SELECTOR, "a[href*='/rent/']")
                result['url'] = link.get_attribute('href')
            except:
                pass

            # title
            title_sels = [
                "span[data-mark='OfferTitle']", "h2", "a[class*='title']",
                "div[class*='title']", "[data-name='TitleComponent']"
            ]
            for sel in title_sels:
                try:
                    result['title'] = card_element.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if result['title']:
                        break
                except:
                    continue

            # price
            price_sels = [
                "span[data-testid='dailyrent-price-per-night']",
                "span[data-mark='MainPrice']", "[data-name='DailyrentPrice'] span",
                "div[class*='price'] span", "span[class*='price']", "[class*='price']"
            ]
            for sel in price_sels:
                try:
                    txt = card_element.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if txt:
                        result['price'] = self.extract_price(txt)
                        break
                except:
                    continue

            # address
            result['address'] = self.extract_address(card_element)

            # guests
            result['guests'] = self.extract_guests_count(card_element)

            # coordinates from card
            lat, lon = self.extract_coordinates_from_card(card_element)
            result['latitude'] = lat
            result['longitude'] = lon

            # type
            result['type'] = self.get_property_type(result['title'], result['url'])

            if result['type'] in ['apartment', 'house', 'room', 'studio']:
                key = 'apartments' if result['type'] in ['apartment', 'studio'] else f"{result['type']}s"
                self.category_stats[key] += 1
                self.category_stats['total'] += 1

        except Exception as e:
            logger.debug(f"Ошибка парсинга карточки: {e}")

        return result

    def get_page_number_from_url(self, url):
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if 'p' in params:
                return int(params['p'][0])
        except:
            pass
        return 1

    def has_next_page(self):
        try:
            next_btn = self.driver.find_elements(By.CSS_SELECTOR, "a[class*='button'] span[class*='text']")
            for el in next_btn:
                if 'дальше' in el.text.lower():
                    parent = el.find_element(By.XPATH, "..")
                    return parent.get_attribute('href')
        except:
            pass

        try:
            links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='p=']")
            for link in links:
                href = link.get_attribute('href')
                page = self.get_page_number_from_url(href)
                if page == self.current_page_num + 1:
                    return href
        except:
            pass

        return None

    def get_offers_from_page(self):
        try:
            self.wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "[data-name='CardComponent'], article, [data-testid='offer-card']")
            ))
        except TimeoutException:
            logger.warning("Таймаут ожидания карточек")
            return []

        page_lat, page_lon = self.extract_coordinates_from_page()
        if page_lat != 0 and page_lon != 0:
            logger.info(f"📍 Координаты страницы: {page_lat}, {page_lon}")

        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.8)

        selectors = [
            "article[data-name='CardComponent']",
            "div[data-name='CardComponent']",
            "[data-testid='offer-card']",
            "article[class*='_416c6--container']"
        ]
        cards = []
        for sel in selectors:
            cards = self.driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                logger.info(f"Найдено карточек: {len(cards)}")
                break

        page_offers = []
        for i, card in enumerate(cards, 1):
            try:
                offer = self.parse_card(card)

                # Если координат нет → сначала берём со страницы, если есть
                if offer['latitude'] == 0 and offer['longitude'] == 0:
                    if page_lat != 0 and page_lon != 0:
                        offer['latitude'] = page_lat
                        offer['longitude'] = page_lon
                    # Если и со страницы нет — геокодируем по адресу
                    elif offer['address']:
                        lat, lon = self.geocode_address(offer['address'])
                        if lat != 0 and lon != 0:
                            offer['latitude'] = lat
                            offer['longitude'] = lon
                            logger.info(f"Geocoded → {offer['address']} : {lat:.5f}, {lon:.5f}")

                offer_key = f"{offer['title']}_{offer['price']}_{offer['address']}"
                if offer_key in self.seen_offers:
                    continue
                self.seen_offers.add(offer_key)

                if offer['address'] and 'байкальск' in offer['address'].lower():
                    page_offers.append(offer)

                icon = {'apartment': '🏢', 'house': '🏠', 'room': '🚪', 'studio': '🎨', 'other': '📌'}.get(offer['type'],
                                                                                                      '📌')
                coord_ok = "📍" if offer['latitude'] != 0 else "❌"
                logger.info(f" {i}. {icon} {offer['title'][:32]}... {offer['price']} ₽  {coord_ok}")

            except StaleElementReferenceException:
                continue
            except Exception as e:
                logger.debug(f"Ошибка обработки карточки {i}: {e}")

        return page_offers

    def parse_category(self, category_name, start_url):
        logger.info("\n" + "=" * 65)
        logger.info(f"📋 {category_name.upper()}")
        logger.info("=" * 65)

        current_url = start_url
        page_counter = 0
        consecutive_duplicates = 0
        category_results = []

        while page_counter < self.max_pages_per_category:
            page_counter += 1
            self.current_page_num = page_counter
            logger.info(f"\n--- Страница {page_counter} ---   {current_url}")

            if current_url in self.visited_urls:
                consecutive_duplicates += 1
                logger.warning(f"Повтор страницы {consecutive_duplicates}/3")
                if consecutive_duplicates >= 3:
                    logger.info("🛑 Слишком много повторов → выходим из категории")
                    break
            else:
                self.visited_urls.add(current_url)
                consecutive_duplicates = 0

            if not self.safe_get(current_url):
                break

            self.random_delay(4, 8)

            try:
                offers = self.get_offers_from_page()
                if offers:
                    category_results.extend(offers)
                    logger.info(f"Добавлено {len(offers)} новых объявлений")
                else:
                    logger.info("Нет подходящих объявлений на странице")
            except Exception as e:
                logger.error(f"Ошибка на странице: {e}")
                break

            next_url = self.has_next_page()
            if not next_url:
                logger.info("Конец списка — кнопка 'Дальше' не найдена")
                break

            next_num = self.get_page_number_from_url(next_url)
            if next_num <= page_counter:
                logger.warning("Обнаружено зацикливание — выходим")
                break

            current_url = next_url
            self.random_delay(3, 6)

        logger.info(f"Категория {category_name} завершена: {len(category_results)} объявлений")
        return category_results

    def save_to_csv(self, filename=None):
        if not self.results:
            return

        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"baikalsk_cian_{ts}.csv"

        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f,
                                    fieldnames=['title', 'price', 'address', 'latitude', 'longitude', 'guests', 'type',
                                                'url'])
            writer.writeheader()
            for row in self.results:
                writer.writerow(row)

        logger.info(f"\nСохранено → {filename}")
        logger.info(f"Всего записей: {len(self.results)}")
        with_coords = sum(1 for r in self.results if r['latitude'] != 0)
        logger.info(f"С координатами: {with_coords} ({with_coords / len(self.results) * 100:.1f}%)")

    def print_statistics(self):
        if not self.results:
            return

        logger.info("\n" + "=" * 70)
        logger.info("📊 СТАТИСТИКА")
        logger.info("=" * 70)

        from collections import Counter
        types = Counter(r['type'] for r in self.results)
        logger.info("По типу:")
        for t, cnt in types.most_common():
            print(f"  {t:10} : {cnt:3d}")

        prices = [r['price'] for r in self.results if r['price'] > 0]
        if prices:
            logger.info(
                f"\nЦены (₽/сут):  min {min(prices):,}  |  avg {sum(prices) // len(prices):,}  |  max {max(prices):,}")

        logger.info(f"\nГеокодировано дополнительно: {self.geocoded_count}")
        logger.info(f"Не удалось геокодировать:   {self.failed_geocode}")

    def run(self):
        try:
            categories = {
                'Квартиры': 'https://irkutsk.cian.ru/snyat-kvartiru-posutochno-irkutskaya-oblast-slyudyanskiy-rayon-baykalskoe-baykalsk-01418719/',
                'Дома': 'https://irkutsk.cian.ru/snyat-dom-posutochno-irkutskaya-oblast-slyudyanskiy-rayon-baykalskoe-baykalsk-01418719/',
                'Комнаты': 'https://irkutsk.cian.ru/snyat-komnatu-posutochno-irkutskaya-oblast-slyudyanskiy-rayon-baykalskoe-baykalsk-01418719/'
            }

            all_results = []
            for cat_name, cat_url in categories.items():
                logger.info(f"\nСТАРТ категории: {cat_name}")
                cat_data = self.parse_category(cat_name, cat_url)
                all_results.extend(cat_data)
                if cat_name != list(categories.keys())[-1]:
                    self.random_delay(6, 10)

            self.results = all_results

            if self.results:
                self.save_to_csv()
                self.print_statistics()
            else:
                logger.info("Не найдено ни одного подходящего объявления")

        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
        finally:
            self.driver.quit()


if __name__ == "__main__":
    parser = BaikalskCianParser()
    parser.run()
