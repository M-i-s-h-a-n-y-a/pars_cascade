"""
Парсер пляжей Байкальска (Яндекс.Карты).
Дедупликация по ID организации, точные координаты из URL карточки.
Выходной файл: baikal_beaches.json
"""

import time
import json
import logging
import re
import os
from typing import List, Dict, Optional, Set, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
import random

# ==================== НАСТРОЙКИ ====================
CITY = "Байкальск"
CITY_CENTER = (51.517, 104.120)
SEARCH_RADIUS_KM = 15
MAX_BEACHES = 50

# Папка для сохранения
OUTPUT_DIR = "output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "baikal_beaches.json")
BACKUP_FILE = os.path.join(OUTPUT_DIR, "baikal_beaches_backup.json")
ID_FILE = os.path.join(OUTPUT_DIR, "beach_ids.json")

HEADLESS = False

# Расширенные поисковые запросы для пляжей
SEARCH_QUERIES = [
    "пляж Байкальск",
    "пляжи Байкальск",
    "место для купания Байкальск",
    "берег Байкала Байкальск",
    "набережная Байкальск"
]

DEG_PER_KM_LAT = 1.0 / 111.0
DEG_PER_KM_LON = 1.0 / (111.0 * 0.6225)  # cos(51.5°)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('beach_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BeachesParser:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.driver = None
        self.seen_ids: Set[str] = set()
        self.known_coords: Set[Tuple[float, float]] = set()
        self.results: List[Dict] = []

        # Создаём папку для сохранения
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        logger.info(f"Директория для сохранения: {os.path.abspath(OUTPUT_DIR)}")

    def setup_driver(self):
        options = webdriver.ChromeOptions()
        if self.headless:
            options.add_argument('--headless')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        self.driver = webdriver.Chrome(options=options)
        logger.info("Драйвер Chrome запущен")

    def _extract_coords_from_url(self, url: str) -> Optional[Tuple[float, float]]:
        """
        Извлекает координаты из URL.
        Поддерживает форматы:
        - ll=lon%2Clat
        - ll=lon,lat
        - ?ll=lon,lat&z=...
        """
        # Формат с URL-кодированием
        match = re.search(r'll=([\d.-]+)%2C([\d.-]+)', url)
        if match:
            lon, lat = float(match.group(1)), float(match.group(2))
            return lat, lon

        # Обычный формат с запятой
        match = re.search(r'll=([\d.-]+),([\d.-]+)', url)
        if match:
            lon, lat = float(match.group(1)), float(match.group(2))
            return lat, lon

        # Формат с pt (точка на карте)
        match = re.search(r'pt=([\d.-]+),([\d.-]+)', url)
        if match:
            lon, lat = float(match.group(1)), float(match.group(2))
            return lat, lon

        return None

    def _parse_coordinates(self, coord_str: str) -> Optional[Tuple[float, float]]:
        """Парсинг координат из data-атрибутов."""
        cleaned = re.sub(r'[^\d.,\-]', '', coord_str)
        parts = cleaned.split(',')
        if len(parts) >= 2:
            try:
                # В data-coordinates обычно формат "lon, lat"
                lon = float(parts[0])
                lat = float(parts[1])
                return lat, lon
            except ValueError:
                pass
        return None

    def _is_inside_city(self, lat: float, lon: float) -> bool:
        """Проверка, что точка в радиусе от центра города."""
        lat_diff = abs(lat - CITY_CENTER[0])
        lon_diff = abs(lon - CITY_CENTER[1])
        return (lat_diff <= SEARCH_RADIUS_KM * DEG_PER_KM_LAT and
                lon_diff <= SEARCH_RADIUS_KM * DEG_PER_KM_LON)

    def _get_share_link_coordinates(self) -> Optional[Tuple[float, float]]:
        """
        Нажимает кнопку 'Поделиться' и забирает точные координаты из ссылки.
        Возвращает (lat, lon) или None при ошибке.
        """
        try:
            # Ждём полной загрузки страницы
            time.sleep(2)

            # Ищем кнопку "Поделиться" (расширенный список селекторов)
            share_btn = None
            selectors = [
                "//button[contains(@class, 'card-share')]",
                "//button[contains(@class, 'share-button')]",
                "//div[contains(@class, 'card-share')]//button",
                "//button[@aria-label='Поделиться']",
                "//button[.//span[text()='Поделиться']]",
                "//button[contains(text(),'Поделиться')]",
                "//div[contains(@class,'share')]//button",
                "//a[contains(@class,'share')]",
            ]

            for selector in selectors:
                try:
                    share_btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    if share_btn:
                        break
                except:
                    continue

            if not share_btn:
                logger.debug("Кнопка 'Поделиться' не найдена")
                return None

            # Скроллим к кнопке и кликаем
            self.driver.execute_script("arguments[0].scrollIntoView(true);", share_btn)
            time.sleep(0.5)
            share_btn.click()
            logger.debug("Клик по 'Поделиться' выполнен")

            # Ждём появления модального окна с ссылкой
            input_selectors = [
                "//div[contains(@class,'share-popup')]//input[@type='text']",
                "//input[contains(@class,'share-link__input')]",
                "//input[contains(@class,'share-input')]",
                "//div[contains(@class,'share')]//input",
                "//input[@value and contains(@value,'yandex')]",
            ]

            input_field = None
            for selector in input_selectors:
                try:
                    input_field = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    if input_field:
                        break
                except:
                    continue

            if not input_field:
                logger.debug("Поле с ссылкой не найдено")
                return None

            share_url = input_field.get_attribute('value')
            if not share_url:
                logger.debug("Поле с ссылкой пустое")
                return None

            logger.debug(f"Share URL: {share_url}")

            # Закрываем попап (может мешать)
            try:
                close_btn = self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'share-popup')]//button[contains(@class,'close')] | //button[@aria-label='Закрыть']")
                close_btn.click()
                time.sleep(0.5)
            except:
                pass

            coords = self._extract_coords_from_url(share_url)
            if coords:
                logger.info(f"Координаты из share-ссылки: {coords}")
                return coords
            else:
                logger.debug(f"Не удалось извлечь координаты из: {share_url}")
                return None

        except Exception as e:
            logger.debug(f"Ошибка при получении share-координат: {e}")
            return None

    def _get_coordinates_from_page(self) -> Optional[Tuple[float, float]]:
        """
        Получает координаты разными способами в порядке надёжности.
        """
        current_url = self.driver.current_url

        # Способ 1: Из URL текущей страницы (самый надёжный)
        coords = self._extract_coords_from_url(current_url)
        if coords:
            logger.debug(f"Координаты из URL страницы: {coords}")
            return coords

        # Способ 2: Кнопка "Поделиться" (точные координаты объекта)
        coords = self._get_share_link_coordinates()
        if coords:
            return coords

        # Способ 3: Meta-теги
        try:
            for meta_selector in [
                "meta[property='og:latitude']",
                "meta[name='latitude']",
                "meta[itemprop='latitude']"
            ]:
                try:
                    meta_lat = self.driver.find_element(By.CSS_SELECTOR, meta_selector)
                    lat = meta_lat.get_attribute("content")
                    if lat:
                        # Ищем соответствующий тег с долготой
                        for lon_selector in [
                            "meta[property='og:longitude']",
                            "meta[name='longitude']",
                            "meta[itemprop='longitude']"
                        ]:
                            try:
                                meta_lon = self.driver.find_element(By.CSS_SELECTOR, lon_selector)
                                lon = meta_lon.get_attribute("content")
                                if lon:
                                    coords = (float(lat), float(lon))
                                    logger.debug(f"Координаты из meta-тегов: {coords}")
                                    return coords
                            except:
                                continue
                except:
                    continue
        except:
            pass

        # Способ 4: Data-атрибуты на карте
        try:
            map_elements = self.driver.find_elements(By.CSS_SELECTOR,
                "[data-coordinates], [data-coords], [data-lat][data-lon]")
            for el in map_elements:
                coord_str = el.get_attribute("data-coordinates") or el.get_attribute("data-coords")
                if coord_str:
                    coords = self._parse_coordinates(coord_str)
                    if coords:
                        return coords

                lat = el.get_attribute("data-lat")
                lon = el.get_attribute("data-lon")
                if lat and lon:
                    return (float(lat), float(lon))
        except:
            pass

        # Способ 5: JavaScript объект карты
        try:
            js_coords = self.driver.execute_script("""
                if (window.map && window.map.getCenter) {
                    var center = window.map.getCenter();
                    return [center[0], center[1]];
                }
                return null;
            """)
            if js_coords:
                return (float(js_coords[0]), float(js_coords[1]))
        except:
            pass

        return None

    def _save_results(self, is_final: bool = False):
        """Сохранение результатов в JSON."""
        try:
            output = {
                "city": CITY,
                "search_radius_km": SEARCH_RADIUS_KM,
                "total": len(self.results),
                "beaches": self.results,
                "parsed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "is_final": is_final
            }

            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            logger.info(f"✓ Сохранено {len(self.results)} пляжей в {OUTPUT_FILE}")
            print(f"  → Файл: {os.path.abspath(OUTPUT_FILE)}")

            # Бэкап каждые 3 записи
            if len(self.results) % 3 == 0:
                with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
                    json.dump(output, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
            # Аварийное сохранение
            try:
                emergency_file = "beaches_emergency.json"
                with open(emergency_file, 'w', encoding='utf-8') as f:
                    json.dump({"beaches": self.results}, f, ensure_ascii=False, indent=2)
                logger.info(f"Аварийное сохранение: {emergency_file}")
            except:
                pass

    def _collect_ids_from_panel(self, query: str) -> List[str]:
        """
        Выполняет поиск, прокручивает боковую панель и собирает все data-id организаций.
        """
        logger.info(f"Поиск: '{query}'")
        self.driver.get("https://yandex.ru/maps/")
        time.sleep(3)

        # Cookie
        try:
            btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(text(), 'Согласен') or contains(text(), 'Принять')]"))
            )
            btn.click()
            time.sleep(1)
        except:
            pass

        search_input = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Поиск']"))
        )
        search_input.clear()
        # Медленный ввод для надёжности
        for char in query:
            search_input.send_keys(char)
            time.sleep(0.05)
        time.sleep(1)
        search_input.send_keys(Keys.RETURN)

        # Ждём результаты
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "a[href*='/org/'], [data-id], .search-list-view__list"))
            )
        except TimeoutException:
            logger.warning(f"Результаты не появились для: {query}")
            return []

        time.sleep(3)

        collected_ids = set()
        scroll_attempts = 0
        max_scrolls = 15

        # Паттерн для извлечения ID из href
        id_pattern = re.compile(r'/org/([^/?]+)')

        while len(collected_ids) < MAX_BEACHES and scroll_attempts < max_scrolls:
            # Ищем элементы разными способами
            elements = []

            # Способ 1: ссылки с /org/
            elements.extend(self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/org/']"))

            # Способ 2: элементы с data-id
            elements.extend(self.driver.find_elements(By.CSS_SELECTOR, "[data-id]"))

            prev_count = len(collected_ids)

            for el in elements:
                try:
                    # Пробуем получить ID из href
                    href = el.get_attribute('href')
                    if href:
                        match = id_pattern.search(href)
                        if match:
                            org_id = match.group(1)
                            if org_id and org_id not in collected_ids:
                                collected_ids.add(org_id)
                                continue

                    # Пробуем data-id
                    data_id = el.get_attribute("data-id")
                    if data_id and data_id not in collected_ids:
                        collected_ids.add(data_id)

                except StaleElementReferenceException:
                    continue

            new_count = len(collected_ids) - prev_count
            logger.info(f"Собрано ID: {len(collected_ids)} (+{new_count})")

            if new_count == 0:
                # Пробуем "Показать ещё"
                try:
                    more_btn = self.driver.find_element(By.XPATH,
                        "//button[contains(text(), 'Показать ещё') or contains(text(), 'Показать еще')]")
                    if more_btn.is_displayed():
                        more_btn.click()
                        time.sleep(2)
                        continue
                except:
                    pass

                # Прокрутка
                try:
                    panel = self.driver.find_element(By.CSS_SELECTOR,
                        "div.scroll__container, ul.search-list-view__list, div.search-list-view")
                    self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", panel)
                    time.sleep(2)
                except:
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
                scroll_attempts += 1
            else:
                scroll_attempts = 0

        logger.info(f"Всего ID из запроса '{query}': {len(collected_ids)}")
        return list(collected_ids)

    def _process_beach_page(self, org_id: str) -> Optional[Dict]:
        """Открывает страницу пляжа и извлекает данные."""
        if org_id in self.seen_ids:
            return None

        url = f"https://yandex.ru/maps/org/{org_id}/"
        logger.info(f"Обрабатываю: {url}")

        try:
            self.driver.get(url)
            time.sleep(3)

            # Извлекаем название
            name = ""
            name_selectors = [
                "h1",
                "h1.card-title-view__title",
                "h1.orgpage-header-view__header",
                ".card-title-view__title",
                ".orgpage-header-view__title"
            ]

            for selector in name_selectors:
                try:
                    name_el = self.driver.find_element(By.CSS_SELECTOR, selector)
                    name = name_el.text.strip()
                    if name:
                        break
                except:
                    continue

            if not name:
                # Пробуем извлечь из URL
                name_match = re.search(r'/org/[^/]+/([^/?]+)', self.driver.current_url)
                if name_match:
                    name = name_match.group(1).replace('-', ' ').title()
                else:
                    logger.warning(f"Нет названия для {org_id}")
                    return None

            name = re.sub(r'\s+', ' ', name).strip()

            # Получаем координаты (основное исправление)
            coords = self._get_coordinates_from_page()

            if not coords:
                logger.warning(f"Координаты не получены для: {name}")
                return None

            lat, lon = coords

            # Проверяем, что в радиусе
            if not self._is_inside_city(lat, lon):
                logger.info(f"  {name} – вне радиуса ({lat:.5f}, {lon:.5f})")
                return None

            # Дедупликация по координатам
            coord_key = (round(lat, 5), round(lon, 5))
            if coord_key in self.known_coords:
                logger.info(f"  Дубль по координатам: {name}")
                return None

            self.seen_ids.add(org_id)
            self.known_coords.add(coord_key)

            # Пытаемся определить тип пляжа
            beach_type = "beach"
            name_lower = name.lower()
            if any(word in name_lower for word in ['дикий', 'необорудованный']):
                beach_type = "wild_beach"
            elif any(word in name_lower for word in ['оборудованный', 'благоустроенный', 'городской']):
                beach_type = "equipped_beach"
            elif 'набережная' in name_lower:
                beach_type = "promenade"

            return {
                "name": name,
                "type": beach_type,
                "url": url,
                "latitude": lat,
                "longitude": lon
            }

        except Exception as e:
            logger.debug(f"Ошибка обработки {org_id}: {e}")
            return None

    def run(self):
        try:
            self.setup_driver()
            all_ids = set()

            # Собираем ID по всем запросам
            logger.info("=== Сбор ID пляжей ===")
            for query in SEARCH_QUERIES:
                ids = self._collect_ids_from_panel(query)
                all_ids.update(ids)
                logger.info(f"Накоплено ID: {len(all_ids)}")

                # Сохраняем промежуточные ID
                with open(ID_FILE, 'w', encoding='utf-8') as f:
                    json.dump({"total": len(all_ids), "ids": list(all_ids)}, f, ensure_ascii=False, indent=2)

                if len(all_ids) >= MAX_BEACHES:
                    break
                time.sleep(2)

            if not all_ids:
                logger.error("Не найдено ни одного пляжа!")
                return

            logger.info(f"\n=== Обработка страниц ===")
            logger.info(f"Всего ID: {len(all_ids)}")
            id_list = list(all_ids)

            # Обрабатываем каждый пляж
            for idx, org_id in enumerate(id_list, 1):
                if len(self.results) >= MAX_BEACHES:
                    break

                logger.info(f"[{idx}/{len(id_list)}] ID: {org_id}")
                data = self._process_beach_page(org_id)

                if data:
                    self.results.append(data)
                    logger.info(f"  ✓ [{data['type']}] {data['name']}")
                    logger.info(f"    Координаты: {data['latitude']:.6f}, {data['longitude']:.6f}")

                    # Сохраняем после каждого найденного пляжа
                    self._save_results(is_final=False)
                else:
                    logger.info("  ✗ Пропущено")

                time.sleep(random.uniform(1.0, 2.0))

            # Финальное сохранение
            logger.info("\n=== Финальное сохранение ===")
            self._save_results(is_final=True)

            # Вывод статистики
            print(f"\n{'='*50}")
            print(f"РЕЗУЛЬТАТЫ ПАРСИНГА ПЛЯЖЕЙ")
            print(f"{'='*50}")
            print(f"Всего найдено: {len(self.results)}")

            # Группировка по типам
            types_count = {}
            for beach in self.results:
                t = beach.get('type', 'unknown')
                types_count[t] = types_count.get(t, 0) + 1

            for beach_type, count in types_count.items():
                type_names = {
                    'beach': 'Пляжи',
                    'wild_beach': 'Дикие пляжи',
                    'equipped_beach': 'Оборудованные пляжи',
                    'promenade': 'Набережные'
                }
                print(f"{type_names.get(beach_type, beach_type)}: {count}")

            print(f"\nФайл сохранён: {os.path.abspath(OUTPUT_FILE)}")

            # Выводим список всех пляжей
            if self.results:
                print(f"\nСписок найденных пляжей:")
                for i, beach in enumerate(self.results, 1):
                    print(f"  {i}. {beach['name']}")
                    print(f"     Координаты: {beach['latitude']:.6f}, {beach['longitude']:.6f}")
                    print(f"     URL: {beach['url']}")

        except Exception as e:
            logger.error(f"Критическая ошибка: {e}", exc_info=True)
            print("\n!!! Ошибка! Сохраняю собранные данные...")
            self._save_results(is_final=False)
            raise
        finally:
            if self.driver:
                self.driver.quit()
                logger.info("Драйвер закрыт")


if __name__ == "__main__":
    print(f"Парсер пляжей для города {CITY}")
    print(f"Результаты: {os.path.abspath(OUTPUT_FILE)}")
    print("Для остановки нажмите Ctrl+C\n")

    parser = BeachesParser(headless=HEADLESS)
    parser.run()