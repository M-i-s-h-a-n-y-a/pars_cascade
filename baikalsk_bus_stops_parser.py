"""
Парсер автобусных остановок и ж/д станций Байкальска (Яндекс.Карты)
Сбор data-id из боковой панели, переход в карточку, извлечение названия и координат.
Выходной файл: baikal_transport_stops.json
"""

import time
import json
import logging
import re
from datetime import datetime
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
CITY_CENTER = (51.517, 104.120)          # широта, долгота центра города
SEARCH_RADIUS_KM = 15
MAX_RESULTS = 100
OUTPUT_FILE = "../data/baikal_transport_stops.json"
HEADLESS = False

# Раздельные запросы для разных типов
BUS_QUERIES = [
    "Байкальск остановки "
]

RAILWAY_QUERIES = [
     "Байкальск Железнодорожные станции "

]

DEG_PER_KM_LAT = 1.0 / 111.0
DEG_PER_KM_LON = 1.0 / (111.0 * 0.6225)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TransportStopParser:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.driver = None
        self.results: List[Dict] = []
        self.seen_ids: Set[str] = set()
        self.known_coords: Set[Tuple[float, float]] = set()
        self.stop_types = {}  # Для хранения типа остановки (bus/railway)

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
        """Извлекает (lat, lon) из параметра ll=долгота%2Cширота."""
        match = re.search(r'll=([\d.-]+)%2C([\d.-]+)', url)
        if match:
            return float(match.group(2)), float(match.group(1))
        return None

    def _parse_coordinates(self, coord_str: str) -> Optional[Tuple[float, float]]:
        """Резервный парсер координат из data-coordinates."""
        cleaned = re.sub(r'[^\d.,\-]', '', coord_str)
        parts = cleaned.split(',')
        if len(parts) == 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                return lat, lon
            except ValueError:
                pass
        return None

    def _is_inside_city(self, lat: float, lon: float) -> bool:
        lat_diff = abs(lat - CITY_CENTER[0])
        lon_diff = abs(lon - CITY_CENTER[1])
        return (lat_diff <= SEARCH_RADIUS_KM * DEG_PER_KM_LAT and
                lon_diff <= SEARCH_RADIUS_KM * DEG_PER_KM_LON)

    def _detect_stop_type(self, name: str, query: str = "") -> str:
        """Определяет тип остановки по названию и поисковому запросу."""
        name_lower = name.lower()
        query_lower = query.lower()

        # Ключевые слова для ж/д
        railway_keywords = [
            'станция', 'вокзал', 'платформа', 'остановочный пункт',
            'жд', 'ж/д', 'железнодорожн', 'депо', 'ржд', 'путь'
        ]

        # Ключевые слова для автобусов
        bus_keywords = [
            'остановка', 'автобус', 'автостанция', 'маршрут'
        ]

        # Проверяем название
        for keyword in railway_keywords:
            if keyword in name_lower:
                return "railway"

        for keyword in bus_keywords:
            if keyword in name_lower:
                return "bus"

        # Проверяем запрос
        if any(keyword in query_lower for keyword in railway_keywords):
            return "railway"
        if any(keyword in query_lower for keyword in bus_keywords):
            return "bus"

        return "unknown"

    def _collect_ids_from_panel(self, query: str, stop_type: str = "unknown") -> List[str]:
        """
        Выполняет поиск, прокручивает боковую панель и собирает все data-id.
        """
        logger.info(f"Поиск ID для запроса: '{query}' (тип: {stop_type})")
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
        # Добавляем небольшую паузу между вводом текста
        for char in query:
            search_input.send_keys(char)
            time.sleep(0.05)
        time.sleep(1)
        search_input.send_keys(Keys.RETURN)

        # Ждём появления результатов
        try:
            # Для ж/д станций может быть другой селектор
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "[data-coordinates], [data-id], .search-list-view__list, .card-feature-view"))
            )
        except TimeoutException:
            logger.warning(f"Не появились результаты для запроса: {query}")
            return []

        time.sleep(3)  # Увеличиваем ожидание для сложных запросов
        collected_ids = set()
        scroll_attempts = 0
        max_scrolls = 20  # Уменьшаем для каждого запроса

        while len(collected_ids) < MAX_RESULTS and scroll_attempts < max_scrolls:
            # Ищем разные возможные селекторы
            elements = self.driver.find_elements(By.CSS_SELECTOR,
                "[data-id][data-coordinates], [data-id]")
            prev_count = len(collected_ids)

            for el in elements:
                try:
                    data_id = el.get_attribute("data-id")
                    if data_id and data_id not in collected_ids:
                        # Сохраняем тип для этого ID
                        self.stop_types[data_id] = stop_type
                        collected_ids.add(data_id)
                except StaleElementReferenceException:
                    continue

            logger.info(f"Собрано data-id: {len(collected_ids)} (новых: {len(collected_ids) - prev_count})")

            if len(collected_ids) == prev_count:
                # Пробуем найти кнопку "Показать ещё"
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

        logger.info(f"Всего найдено data-id для запроса '{query}': {len(collected_ids)}")
        return list(collected_ids)

    def _process_card(self, data_id: str) -> Optional[Dict]:
        """Открывает карточку организации и извлекает название и координаты."""
        if data_id in self.seen_ids:
            return None

        url = f"https://yandex.ru/maps/org/{data_id}/"
        logger.info(f"Открываю карточку: {url}")
        try:
            self.driver.get(url)
            time.sleep(2)

            # Название из <h1> или других элементов
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
                # Пробуем найти название в URL
                name_match = re.search(r'/org/[^/]+/([^/?]+)', self.driver.current_url)
                if name_match:
                    name = name_match.group(1).replace('-', ' ').title()
                else:
                    logger.warning(f"Нет названия в карточке {data_id}")
                    return None

            name = re.sub(r'\s+', ' ', name).strip()

            # Координаты
            coords = None

            # Способ 1: из URL
            coords = self._extract_coords_from_url(self.driver.current_url)

            # Способ 2: из meta-тегов
            if not coords:
                try:
                    meta_coords = self.driver.find_element(By.CSS_SELECTOR,
                        "meta[property='og:latitude'], meta[name='latitude']")
                    lat = meta_coords.get_attribute("content")
                    meta_coords_lon = self.driver.find_element(By.CSS_SELECTOR,
                        "meta[property='og:longitude'], meta[name='longitude']")
                    lon = meta_coords_lon.get_attribute("content")
                    if lat and lon:
                        coords = (float(lat), float(lon))
                except:
                    pass

            # Способ 3: из JSON-LD или data-атрибутов
            if not coords:
                try:
                    map_element = self.driver.find_element(By.CSS_SELECTOR,
                        "[data-coordinates], .ymaps-2-1-79-map")
                    coord_str = map_element.get_attribute("data-coordinates") or \
                               map_element.get_attribute("data-coords")
                    if coord_str:
                        coords = self._parse_coordinates(coord_str)
                except:
                    pass

            if not coords:
                logger.warning(f"Не удалось определить координаты для {data_id} - {name}")
                return None

            lat, lon = coords
            if not self._is_inside_city(lat, lon):
                logger.info(f"  {name} – вне радиуса ({lat:.5f}, {lon:.5f})")
                return None

            # Дедупликация
            coord_key = (round(lat, 5), round(lon, 5))
            if coord_key in self.known_coords:
                logger.info(f"  Дубль: {name}")
                return None

            # Определяем тип остановки
            stop_type = self.stop_types.get(data_id, "unknown")
            if stop_type == "unknown":
                stop_type = self._detect_stop_type(name)

            self.seen_ids.add(data_id)
            self.known_coords.add(coord_key)

            return {
                "name": name,
                "type": stop_type,
                "url": url,
                "latitude": lat,
                "longitude": lon
            }

        except Exception as e:
            logger.debug(f"Ошибка обработки {data_id}: {e}")
            return None

    def run(self):
        try:
            self.setup_driver()
            all_ids = set()

            # Собираем автобусные остановки
            logger.info("=== Сбор автобусных остановок ===")
            for query in BUS_QUERIES:
                ids = self._collect_ids_from_panel(query, "bus")
                all_ids.update(ids)
                logger.info(f"Накоплено ID (автобусы): {len(all_ids)}")
                if len(all_ids) >= MAX_RESULTS:
                    break
                time.sleep(2)

            # Собираем ж/д станции
            logger.info("=== Сбор железнодорожных станций ===")
            for query in RAILWAY_QUERIES:
                ids = self._collect_ids_from_panel(query, "railway")
                all_ids.update(ids)
                logger.info(f"Накоплено ID (всего): {len(all_ids)}")
                if len(all_ids) >= MAX_RESULTS:
                    break
                time.sleep(2)

            logger.info(f"Всего уникальных ID для обработки: {len(all_ids)}")
            id_list = list(all_ids)

            # Обрабатываем карточки
            for idx, data_id in enumerate(id_list, 1):
                if len(self.results) >= MAX_RESULTS:
                    break
                logger.info(f"[{idx}/{len(id_list)}] Обрабатываю ID {data_id}")
                data = self._process_card(data_id)
                if data:
                    self.results.append(data)
                    logger.info(f"  ✓ [{data['type']}] {data['name']} | {data['latitude']}, {data['longitude']}")
                else:
                    logger.info("  ✗ Пропущено")
                time.sleep(random.uniform(1.0, 2.0))

            # Сохранение с группировкой по типам
            bus_stops = [s for s in self.results if s['type'] == 'bus']
            railway_stops = [s for s in self.results if s['type'] == 'railway']
            other_stops = [s for s in self.results if s['type'] not in ('bus', 'railway')]

            output = {
                "city": CITY,
                "search_radius_km": SEARCH_RADIUS_KM,
                "total": len(self.results),
                "statistics": {
                    "bus_stops": len(bus_stops),
                    "railway_stations": len(railway_stops),
                    "other": len(other_stops)
                },
                "stops": self.results,
                "bus_stops": bus_stops,
                "railway_stations": railway_stops,
                "parsed_at": datetime.now().isoformat()
            }

            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            logger.info(f"Сохранено в {OUTPUT_FILE}")
            print(f"\n=== Результаты ===")
            print(f"Автобусные остановки: {len(bus_stops)}")
            print(f"Ж/д станции: {len(railway_stops)}")
            print(f"Другие объекты: {len(other_stops)}")
            print(f"Всего: {len(self.results)}")

            if railway_stops:
                print("\nНайденные ж/д станции:")
                for station in railway_stops:
                    print(f"  - {station['name']} ({station['latitude']}, {station['longitude']})")

        except Exception as e:
            logger.error(f"Критическая ошибка: {e}", exc_info=True)
        finally:
            if self.driver:
                self.driver.quit()


if __name__ == "__main__":
    parser = TransportStopParser(headless=HEADLESS)
    parser.run()