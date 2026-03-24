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


class AvitoCommercialParser:
    def __init__(self, overwrite=True):
        self.setup_driver()
        self.results = []
        self.filename = 'avito_commercial.csv'
        self.overwrite = overwrite

        # Если нужно перезаписать файл - удаляем существующий
        if overwrite and os.path.exists(self.filename):
            try:
                os.remove(self.filename)
                logger.info(f"🗑️ Существующий файл {self.filename} удален для перезаписи")
            except Exception as e:
                logger.warning(f"Не удалось удалить файл {self.filename}: {e}")

        # Создаем файл с заголовками, если его нет
        if not os.path.exists(self.filename):
            with open(self.filename, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f,
                                        fieldnames=['title', 'price', 'address', 'latitude', 'longitude', 'area',
                                                    'commercial_type', 'purpose', 'url'])
                writer.writeheader()

    def setup_driver(self):
        chrome_options = Options()
        # Убираем headless для отладки, потом можно вернуть
        # chrome_options.add_argument('--headless')
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

    def extract_area(self):
        """Извлечение площади помещения"""
        area = 0.0

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-marker="item-view/item-params"]'))
            )
            params = self.driver.find_elements(By.CSS_SELECTOR, '[data-marker="item-view/item-params"] li')
            for param in params:
                param_text = param.text.lower()
                if any(word in param_text for word in ['м²', 'м2', 'площадь', 'кв.м']):
                    numbers = re.findall(r'(\d+(?:[.,]\d+)?)', param_text)
                    if numbers:
                        area = float(numbers[0].replace(',', '.'))
                        logger.info(f"  Площадь найдена в параметрах: {area} м²")
                        return area
        except:
            pass

        try:
            desc_element = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-marker="item-view/item-description"]'))
            )
            desc = desc_element.text.lower()
            patterns = [
                r'площадь\s*[:\s]*(\d+(?:[.,]\d+)?)\s*м²',
                r'(\d+(?:[.,]\d+)?)\s*м²',
                r'(\d+(?:[.,]\d+)?)\s*кв\.?м',
                r'общая площадь\s*[:\s]*(\d+(?:[.,]\d+)?)'
            ]

            for pattern in patterns:
                match = re.search(pattern, desc)
                if match:
                    area = float(match.group(1).replace(',', '.'))
                    logger.info(f"  Площадь найдена в описании: {area} м²")
                    return area
        except:
            pass

        try:
            title = self.driver.find_element(By.CSS_SELECTOR, 'h1').text.lower()
            match = re.search(r'(\d+(?:[.,]\d+)?)\s*м²', title)
            if match:
                area = float(match.group(1).replace(',', '.'))
                logger.info(f"  Площадь найдена в заголовке: {area} м²")
                return area
        except:
            pass

        return area

    def extract_commercial_type(self, title):
        """Определение типа коммерческой недвижимости"""
        title_lower = title.lower()

        commercial_types = {
            'office': ['офис', 'бизнес-центр', 'офисное', 'коворкинг'],
            'retail': ['магазин', 'торговый', 'павильон', 'бутик', 'салон', 'торговая площадь'],
            'warehouse': ['склад', 'складское', 'ангар', 'производственный'],
            'cafe': ['кафе', 'ресторан', 'столовая', 'общепит', 'бар', 'кофейня'],
            'service': ['сервис', 'услуги', 'салон красоты', 'автосервис', 'стоматология', 'медицинский'],
            'free': ['свободного назначения', 'free', 'любое назначение']
        }

        for com_type, keywords in commercial_types.items():
            if any(keyword in title_lower for keyword in keywords):
                return com_type
        return 'other'

    def extract_purpose(self, title):
        """Определение цели использования (аренда/продажа)"""
        title_lower = title.lower()

        if 'сдам' in title_lower or 'аренд' in title_lower or 'сдается' in title_lower:
            return 'rent'
        elif 'прода' in title_lower or 'продается' in title_lower:
            return 'sale'
        else:
            return 'unknown'

    def extract_address(self):
        """Улучшенное извлечение адреса"""
        address = ''

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
                                    address = data['address'].get('addressLocality', '') + ', ' + data['address'].get(
                                        'streetAddress', '')
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
                    if match and len(match) > 5:
                        address = match
                        logger.info(f"  Адрес найден (RegEx): {address[:50]}...")
                        return address
        except:
            pass

        logger.warning("  Адрес не найден")
        return address

    def extract_coordinates(self):
        """Улучшенное извлечение координат из страницы объявления"""
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

    def get_total_pages(self):
        """Получение общего количества страниц"""
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

    def find_other_cities_block(self):
        """Находит блок с объявлениями из других городов и возвращает его элемент"""
        try:
            xpath_expressions = [
                "//*[contains(text(), 'объявления в других городах')]",
                "//*[contains(text(), 'есть в других городах')]",
                "//*[contains(text(), 'показать объявления из других городов')]",
                "//*[contains(text(), 'найдено в других городах')]",
                "//*[contains(text(), 'Объявления в других городах')]"
            ]

            for xpath in xpath_expressions:
                elements = self.driver.find_elements(By.XPATH, xpath)
                for elem in elements:
                    if elem.is_displayed():
                        logger.info(f"Найден блок других городов с текстом: {elem.text[:50]}")
                        return elem
        except Exception as e:
            logger.debug(f"Ошибка при поиске блока других городов: {e}")

        return None

    def get_listing_urls(self, category_url, category_name):
        """Получение ссылок на объявления (только из Байкальска)"""
        urls = []

        logger.info(f"Загрузка первой страницы {category_name}...")
        self.driver.get(category_url)
        self.random_delay(5, 8)

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

            other_cities_block = self.find_other_cities_block()

            items = self.driver.find_elements(By.CSS_SELECTOR, '[data-marker="item"]')

            if not items:
                items = self.driver.find_elements(By.CSS_SELECTOR,
                                                  'div[data-marker*="item"], article[data-marker*="item"], div[class*="iva-item"]')

            page_urls = []

            for item in items:
                if other_cities_block:
                    try:
                        is_after = self.driver.execute_script(
                            "return arguments[0].compareDocumentPosition(arguments[1]) & 4",
                            other_cities_block, item
                        )
                        if is_after:
                            continue
                    except Exception as e:
                        logger.debug(f"Ошибка при сравнении позиций: {e}")

                try:
                    link = None
                    selectors = [
                        '[data-marker="item-title"]',
                        'a[data-marker*="title"]',
                        'a[class*="title"]',
                        'h3 a'
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
                        url_parts = link.split('/')
                        if len(url_parts) > 3:
                            city = url_parts[3]
                            if city == 'baykalsk':
                                page_urls.append(link)
                except Exception as e:
                    logger.debug(f"Ошибка при извлечении ссылки: {e}")
                    continue

            urls.extend(page_urls)
            logger.info(f"Страница {page}: собрано {len(page_urls)} объявлений из Байкальска")

            if other_cities_block:
                logger.info(f"⚠️ На странице {page} найден блок 'объявления в других городах'. Прекращаем сбор.")
                break

            if len(page_urls) == 0:
                logger.info(f"📭 На странице {page} нет объявлений из Байкальска. Прекращаем сбор.")
                break

            try:
                next_button = self.driver.find_element(By.CSS_SELECTOR,
                                                       '[data-marker*="pagination/next"], a[class*="pagination-next"]')
                if 'disabled' in next_button.get_attribute('class') or not next_button.is_enabled():
                    logger.info("Кнопка 'Далее' неактивна, завершаем пагинацию")
                    break
            except:
                pass

        logger.info(f"Всего собрано URL в категории {category_name}: {len(urls)}")
        return urls

    def parse_listing(self, url):
        """Парсинг одного объявления"""
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

            try:
                close_buttons = self.driver.find_elements(By.CSS_SELECTOR,
                                                          '[data-marker="popup-close"], .modal-close')
                for btn in close_buttons:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(1)
            except:
                pass

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
            area = self.extract_area()
            commercial_type = self.extract_commercial_type(title)
            purpose = self.extract_purpose(title)

            result = {
                'title': title,
                'price': price,
                'address': address,
                'latitude': lat,
                'longitude': lon,
                'area': area,
                'commercial_type': commercial_type,
                'purpose': purpose,
                'url': url
            }

            logger.info(f"  Успешно распаршено: {title[:50]}...")
            return result

        except Exception as e:
            logger.error(f"Ошибка при парсинге {url}: {e}")
            return None

    def save_to_csv(self, data=None, append=True):
        """Сохранение данных в CSV"""
        if data is None:
            data = self.results

        if not data:
            logger.warning("Нет данных для сохранения")
            return

        mode = 'a' if append and os.path.exists(self.filename) else 'w'

        with open(self.filename, mode, newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f,
                                    fieldnames=['title', 'price', 'address', 'latitude', 'longitude', 'area',
                                                'commercial_type', 'purpose', 'url'])
            if mode == 'w':
                writer.writeheader()
            writer.writerows(data)

        logger.info(f"💾 Данные сохранены в {self.filename} (всего: {len(data)} записей)")

    def run(self):
        """Основной метод запуска парсера"""
        categories = {
            'Коммерческая недвижимость (аренда)': 'https://www.avito.ru/baykalsk/kommercheskaya_nedvizhimost/sdam',
            'Коммерческая недвижимость (продажа)': 'https://www.avito.ru/baykalsk/kommercheskaya_nedvizhimost/prodam'
        }

        logger.info("=" * 50)
        logger.info("ПАРСЕР КОММЕРЧЕСКОЙ НЕДВИЖИМОСТИ AVITO")
        logger.info("=" * 50)

        if self.overwrite:
            logger.info("🔄 Режим перезаписи: старые данные будут удалены")
        else:
            logger.info("📝 Режим добавления: новые данные будут добавлены к существующим")

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
                logger.info(
                    f"  ✓ {data['title'][:30]}... {data['price']} ₽, площадь: {data['area']} м², тип: {data['commercial_type']}")

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
    parser = AvitoCommercialParser(overwrite=True)
    parser.run()