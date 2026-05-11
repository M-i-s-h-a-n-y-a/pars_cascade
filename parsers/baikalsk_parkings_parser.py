"""
Парсер автомобильных парковок Байкальска (Яндекс.Карты)
Дедупликация по ID организации, сбор координат из URL карточки.
Выходной файл: baikal_parkings.json
"""

import time
import json
import logging
import re
from datetime import datetime
from typing import List, Dict, Optional, Set
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import random

# ==================== НАСТРОЙКИ ====================
CITY = "Байкальск"
CITY_CENTER = (51.517, 104.120)          # широта, долгота центра города
SEARCH_RADIUS_KM = 15
MAX_PARKINGS = 50
OUTPUT_FILE = "../data/baikal_parkings.json"
HEADLESS = False
SEARCH_QUERY = "Автомобильная парковка Байкальск"

DEG_PER_KM_LAT = 1.0 / 111.0
DEG_PER_KM_LON = 1.0 / (111.0 * 0.6225)  # cos(51.5°)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Регулярное выражение для извлечения ID организации из URL
# Пример: /org/parkovka/987654321/ -> "parkovka/987654321"
ORG_ID_PATTERN = re.compile(r'/org/([^/]+/\d+)')


class ParkingParser:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.driver = None
        self.seen_org_ids: Set[str] = set()
        self.results: List[Dict] = []

    def setup_driver(self):
        """Запуск браузера Chrome с заданными опциями."""
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

    def _extract_org_id(self, url: str) -> Optional[str]:
        """Извлекает 'slug/id' организации из URL."""
        m = ORG_ID_PATTERN.search(url)
        return m.group(1) if m else None

    def _extract_coords_from_url(self, url: str) -> Optional[tuple]:
        """Извлекает (lat, lon) из параметра ll=долгота%2Cширота."""
        match = re.search(r'll=([\d.-]+)%2C([\d.-]+)', url)
        if match:
            return float(match.group(2)), float(match.group(1))
        return None

    def _is_inside_city(self, lat: float, lon: float) -> bool:
        """Проверяет, попадает ли координата в заданный радиус вокруг центра города."""
        lat_diff = abs(lat - CITY_CENTER[0])
        lon_diff = abs(lon - CITY_CENTER[1])
        return (lat_diff <= SEARCH_RADIUS_KM * DEG_PER_KM_LAT and
                lon_diff <= SEARCH_RADIUS_KM * DEG_PER_KM_LON)

    def _collect_unique_ids(self) -> List[str]:
        """Прокручивает боковую панель и собирает уникальные ID организаций."""
        logger.info(f"Поиск: '{SEARCH_QUERY}'")
        self.driver.get("https://yandex.ru/maps/")
        time.sleep(3)

        # Принять куки
        try:
            btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Согласен')]"))
            )
            btn.click()
            time.sleep(1)
        except:
            pass

        search_input = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Поиск']"))
        )
        search_input.clear()
        search_input.send_keys(SEARCH_QUERY)
        search_input.send_keys(Keys.RETURN)
        time.sleep(5)

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/org/']"))
            )
        except:
            logger.warning("Результаты не появились вовремя.")
            return []

        unique_org_ids = set()
        no_new_count = 0
        max_scrolls = 20

        for _ in range(max_scrolls):
            links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/org/']")
            for link in links:
                href = link.get_attribute('href')
                if not href:
                    continue
                org_id = self._extract_org_id(href)
                if org_id:
                    unique_org_ids.add(org_id)

            logger.info(f"Уникальных организаций в боковой панели: {len(unique_org_ids)}")
            if len(unique_org_ids) >= MAX_PARKINGS:
                break

            # Кнопка «Показать ещё»
            try:
                more_btn = self.driver.find_element(By.XPATH,
                    "//button[contains(text(), 'Показать ещё') or contains(text(), 'Показать еще')]")
                if more_btn.is_displayed():
                    more_btn.click()
                    time.sleep(2)
                    continue
            except:
                pass

            # Прокрутка боковой панели
            try:
                panel = self.driver.find_element(By.CSS_SELECTOR,
                    "div.scroll__container, ul.search-list-view__list, div.search-list-view")
                prev_scroll = self.driver.execute_script("return arguments[0].scrollTop", panel)
                self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", panel)
                time.sleep(2)
                new_scroll = self.driver.execute_script("return arguments[0].scrollTop", panel)
                if new_scroll == prev_scroll:
                    no_new_count += 1
                else:
                    no_new_count = 0
            except:
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                no_new_count += 1

            if no_new_count >= 3:
                break

        logger.info(f"Итого уникальных ID: {len(unique_org_ids)}")
        return list(unique_org_ids)

    def _parse_parking_page(self, org_id: str) -> Optional[Dict]:
        """Заходит на карточку парковки по ID, получает название и координаты."""
        if org_id in self.seen_org_ids:
            return None

        url = f"https://yandex.ru/maps/org/{org_id}/"
        try:
            logger.info(f"Обрабатываю: {url}")
            self.driver.get(url)
            time.sleep(3)

            current_url = self.driver.current_url

            # Название из <h1>
            name = ""
            try:
                name = self.driver.find_element(By.TAG_NAME, "h1").text.strip()
            except:
                pass
            if not name:
                logger.warning(f"Нет названия для {org_id}")
                return None
            name = re.sub(r'\s+', ' ', name)

            # Координаты
            coords = self._extract_coords_from_url(current_url)
            if not coords:
                logger.warning(f"Нет координат в URL: {current_url}")
                return None

            lat, lon = coords
            if not self._is_inside_city(lat, lon):
                logger.info(f"  Пропущено (вне радиуса): {name}")
                return None

            self.seen_org_ids.add(org_id)
            return {
                "name": name,
                "url": current_url,
                "latitude": lat,
                "longitude": lon
            }

        except Exception as e:
            logger.debug(f"Ошибка при обработке {org_id}: {e}")
            return None

    def run(self):
        """Основной рабочий процесс."""
        try:
            self.setup_driver()

            # Шаг 1 – сбор уникальных ID организаций
            org_ids = self._collect_unique_ids()
            if not org_ids:
                logger.error("Не найдено ни одной парковки")
                return

            # Шаг 2 – обход каждой парковки по ID
            for idx, oid in enumerate(org_ids, 1):
                if len(self.results) >= MAX_PARKINGS:
                    break
                logger.info(f"[{idx}/{len(org_ids)}]")
                data = self._parse_parking_page(oid)
                if data:
                    self.results.append(data)
                    logger.info(f"  ✓ {data['name']} | {data['latitude']}, {data['longitude']}")
                else:
                    logger.info("  ✗ Пропущено")
                time.sleep(random.uniform(1.0, 1.5))

            # Сохранение
            output = {
                "city": CITY,
                "search_radius_km": SEARCH_RADIUS_KM,
                "total": len(self.results),
                "parkings": self.results,
                "parsed_at": datetime.now().isoformat()
            }
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            logger.info(f"Сохранено {len(self.results)} парковок в {OUTPUT_FILE}")
            print(f"\nГотово! Найдено {len(self.results)} парковок в окрестностях {CITY}.")

        except Exception as e:
            logger.error(f"Критическая ошибка: {e}", exc_info=True)
        finally:
            if self.driver:
                self.driver.quit()


if __name__ == "__main__":
    parser = ParkingParser(headless=HEADLESS)
    parser.run()