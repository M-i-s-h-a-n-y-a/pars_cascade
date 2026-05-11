"""
Парсер развлечений, спорта, культуры и магазинов Байкальска из Яндекс.Карт.
Фильтрация по координатам города.
Выходной файл: yandex_maps_entertainment_filtered.json
"""

import time
import json
import logging
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import random

# ==================== НАСТРОЙКИ ====================
CITY = "Байкальск"
CITY_COORDINATES = {
    "latitude": 51.517,
    "longitude": 104.120,
    "radius_km": 15
}

CATEGORIES = {
    "culture": {
        "name": "Объекты культуры",
        "queries": [
            "театр", "музей", "кинотеатр", "выставочный зал", "галерея",
            "дом культуры", "филармония", "концертный зал"
        ]
    },
    "entertainment": {
        "name": "Развлечения",
        "queries": [
            "боулинг", "бильярд", "игровой центр", "игровые автоматы",
            "аренда", "квест", "лазертаг", "караоке", "ночной клуб"
        ]
    },
    "sport": {
        "name": "Спорт",
        "queries": [
            "спортивный комплекс", "спортивный центр", "фитнес", "тренажерный зал",
            "бассейн", "каток", "спортивная школа", "горнолыжный комплекс",
            "лыжная база", "спортивная площадка"
        ]
    },
    "shopping": {
        "name": "Шоппинг",
        "queries": [
            "сувениры", "рынок", "магазин сувениров", "ярмарка",
            "базар", "торговый центр", "торговый комплекс"
        ]
    }
}

OUTPUT_FILE = "../data/yandex_maps_entertainment_filtered.json"
HEADLESS = False
MAX_BUSINESSES = 100

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class YandexEntertainmentParser:
    def __init__(self, city: str, categories: Dict, headless: bool = False):
        self.city = city
        self.categories = categories
        self.headless = headless
        self.driver = None
        self.all_businesses = []
        self.seen_ids = set()

        self.excluded_cities = ["Иркутск", "Ангарск", "Шелехов", "Усолье-Сибирское", "Слюдянка"]
        self.excluded_keywords = [
            "офис", "представительство", "агентство", "бронирование",
            "отель", "гостиница", "хостел", "гостевой дом", "турбаза"
        ]
        self.radius_degrees = CITY_COORDINATES["radius_km"] / 111.0

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
        logger.info("Драйвер инициализирован")

    def clean_name(self, name: str) -> str:
        if not name:
            return ""
        name = re.sub(r'\d+[,.]\d+', '', name)
        name = re.sub(r'[Сс]р\. чек.*$', '', name)
        name = re.sub(r'\d+[₽руб].*$', '', name)
        name = re.sub(r'Закрыто.*$', '', name)
        name = name.split('\n')[0].strip()
        name = re.sub(r'\s+', ' ', name)
        return name[:80]

    def extract_coords(self, url: str) -> Optional[Tuple[float, float]]:
        match = re.search(r'/@([\d.-]+),([\d.-]+),\d+z', url)
        if match:
            try:
                return (float(match.group(1)), float(match.group(2)))
            except:
                pass
        match = re.search(r'll=([\d.-]+)%2C([\d.-]+)', url)
        if match:
            try:
                return (float(match.group(2)), float(match.group(1)))
            except:
                pass
        return None

    def get_org_id(self, url: str) -> str:
        match = re.search(r'/org/([^/?]+)', url)
        return match.group(1) if match else None

    def is_in_city_radius(self, lat: float, lon: float) -> bool:
        if lat is None or lon is None:
            return False
        lat_diff = abs(lat - CITY_COORDINATES["latitude"])
        lon_diff = abs(lon - CITY_COORDINATES["longitude"])
        return lat_diff <= self.radius_degrees and lon_diff <= self.radius_degrees

    def is_valid_business(self, name: str, address: str, lat: float, lon: float) -> bool:
        for excluded_city in self.excluded_cities:
            if excluded_city in name or (address and excluded_city in address):
                logger.debug(f"  Исключён по названию города: {name} содержит {excluded_city}")
                return False
        for keyword in self.excluded_keywords:
            if keyword.lower() in name.lower():
                logger.debug(f"  Исключён по ключевому слову: {name} содержит {keyword}")
                return False
        if lat is not None and lon is not None:
            if not self.is_in_city_radius(lat, lon):
                logger.debug(f"  Исключён по координатам: {name} ({lat:.4f}, {lon:.4f})")
                return False
        return True

    def parse_overall_rating_and_count(self, soup) -> Tuple[Optional[float], Optional[int]]:
        overall_rating = None
        ratings_count = None

        rating_element = soup.find('div', {'aria-label': re.compile(r'Оценка \d+[,.]\d+ из 5')})
        if not rating_element:
            rating_element = soup.find('div', {'aria-label': re.compile(r'\d+[,.]\d+')})
        if rating_element:
            aria_label = rating_element.get('aria-label', '')
            match = re.search(r'Оценка (\d+)[,.](\d+) из 5', aria_label)
            if match:
                try:
                    rating_str = f"{match.group(1)}.{match.group(2)}"
                    overall_rating = float(rating_str)
                except:
                    pass

        count_elements = soup.find_all(attrs={'aria-label': re.compile(r'\d+\s*оценк')})
        for elem in count_elements:
            aria_label = elem.get('aria-label', '')
            match = re.search(r'(\d+)\s*оценк', aria_label)
            if match:
                try:
                    ratings_count = int(match.group(1))
                    break
                except:
                    pass

        if ratings_count is None:
            all_spans = soup.find_all(['span', 'div'], text=re.compile(r'\d+\s*оценок'))
            for span in all_spans:
                text = span.get_text(strip=True)
                match = re.search(r'(\d+)\s*оценок', text)
                if match:
                    try:
                        ratings_count = int(match.group(1))
                        break
                    except:
                        pass

        if overall_rating is None:
            rating_text_elem = soup.find('span', class_=re.compile(r'business-rating-badge-view__rating-text'))
            if not rating_text_elem:
                rating_text_elem = soup.find('div', class_=re.compile(r'business-header-rating-view'))
            if rating_text_elem:
                text = rating_text_elem.get_text(strip=True)
                match = re.search(r'(\d+)[,.](\d+)', text)
                if match:
                    try:
                        rating_str = f"{match.group(1)}.{match.group(2)}"
                        if len(match.group(2)) > 2:
                            rating_str = f"{match.group(1)}.{match.group(2)[0]}"
                        overall_rating = float(rating_str)
                    except:
                        pass
                if ratings_count is None:
                    count_match = re.search(r'(\d+)\s*оценок', text)
                    if count_match:
                        try:
                            ratings_count = int(count_match.group(1))
                        except:
                            pass

        return overall_rating, ratings_count

    def parse_business_page(self, url: str, category_type: str, subcategory: str) -> Optional[Dict]:
        org_id = self.get_org_id(url)
        if not org_id or org_id in self.seen_ids:
            return None

        try:
            logger.info(f"  Загрузка: {org_id[:30]}")
            self.driver.get(url)
            time.sleep(3)

            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            name = ""
            name_elem = soup.find('h1')
            if name_elem:
                name = name_elem.get_text(strip=True)
            name = self.clean_name(name)
            if not name or len(name) < 2:
                return None

            address = ""
            addr_elem = soup.select_one('div[class*="address"] span, span[class*="address"]')
            if addr_elem:
                address = addr_elem.get_text(strip=True)
                address = address.split('этаж')[0].strip()
                address = address.split('•')[0].strip()

            coords = self.extract_coords(self.driver.current_url)

            if not self.is_valid_business(name, address, coords[0] if coords else None, coords[1] if coords else None):
                logger.info(f"    ✗ Пропущено (не в {self.city}): {name}")
                return None

            overall_rating, ratings_count = self.parse_overall_rating_and_count(soup)

            business = {
                'id': org_id,
                'name': name,
                'address': address,
                'category_type': category_type,
                'subcategory': subcategory,
                'overall_rating': overall_rating,
                'ratings_count': ratings_count,
                'url': url,
                'latitude': coords[0] if coords else None,
                'longitude': coords[1] if coords else None,
                'parsed_at': datetime.now().isoformat()
            }

            self.seen_ids.add(org_id)
            return business

        except Exception as e:
            logger.debug(f"  Ошибка при парсинге {org_id}: {e}")
            return None

    def search_and_collect_urls(self, query: str) -> List[str]:
        full_query = f"{self.city} {query}"
        logger.info(f"Поиск: {full_query}")

        self.driver.get("https://yandex.ru/maps/")
        time.sleep(2)
        try:
            accept_btn = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Согласен')]")
            accept_btn.click()
            time.sleep(1)
        except:
            pass

        search_input = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Поиск']"))
        )
        search_input.clear()
        search_input.send_keys(full_query)
        search_input.send_keys(Keys.RETURN)
        time.sleep(4)

        unique_ids = set()
        urls_by_id = {}
        scroll_count = 0
        no_new_count = 0

        while len(unique_ids) < MAX_BUSINESSES and scroll_count < 15 and no_new_count < 5:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            try:
                show_more = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Показать еще')]")
                show_more.click()
                time.sleep(1)
            except:
                pass

            links = self.driver.execute_script("""
                var links = document.querySelectorAll('a[href*="/org/"]');
                var result = [];
                for (var i = 0; i < links.length; i++) {
                    var href = links[i].href;
                    if (href && href.indexOf('/org/') !== -1) {
                        var match = href.match(/\\/org\\/([^/?]+)/);
                        if (match) {
                            result.push({
                                id: match[1],
                                url: href.split('?')[0]
                            });
                        }
                    }
                }
                return result;
            """)

            new_count = 0
            for item in links:
                org_id = item['id']
                clean_url = item['url']
                if org_id not in unique_ids:
                    unique_ids.add(org_id)
                    urls_by_id[org_id] = clean_url
                    new_count += 1

            if new_count > 0:
                logger.info(f"  Найдено {len(unique_ids)} уникальных объектов (+{new_count})")
                no_new_count = 0
            else:
                no_new_count += 1
            scroll_count += 1

        logger.info(f"  Всего уникальных: {len(unique_ids)}")
        return list(urls_by_id.values())

    def run(self):
        try:
            self.setup_driver()

            total_categories = sum(len(cat["queries"]) for cat in self.categories.values())
            current_index = 0

            for category_key, category_info in self.categories.items():
                logger.info(f"\n{'=' * 60}")
                logger.info(f"ОБРАБОТКА {category_info['name'].upper()}")
                logger.info(f"{'=' * 60}")

                for subcategory in category_info["queries"]:
                    current_index += 1
                    logger.info(f"\n[{current_index}/{total_categories}] {subcategory.upper()}")

                    try:
                        urls = self.search_and_collect_urls(subcategory)
                        logger.info(f"Найдено {len(urls)} URL")

                        for i, url in enumerate(urls, 1):
                            if len(self.all_businesses) >= MAX_BUSINESSES:
                                break
                            logger.info(f"  [{i}/{len(urls)}] Парсинг...")
                            business = self.parse_business_page(url, category_info["name"], subcategory)
                            if business:
                                self.all_businesses.append(business)
                                coords_str = f" [{business['latitude']:.6f}, {business['longitude']:.6f}]" if business['latitude'] else ""
                                info = []
                                if business.get('overall_rating'):
                                    info.append(f"⭐{business['overall_rating']}")
                                if business.get('ratings_count'):
                                    info.append(f"({business['ratings_count']} оценок)")
                                logger.info(f"    ✓ {business['name']}{coords_str} | {' '.join(info) if info else 'нет рейтингов'}")
                            else:
                                logger.info(f"    ✗ Пропущено")
                            time.sleep(random.uniform(1, 1.5))
                    except Exception as e:
                        logger.error(f"Ошибка при обработке {subcategory}: {e}")
                        continue

            output = {
                "metadata": {
                    "city": self.city,
                    "city_coordinates": CITY_COORDINATES,
                    "categories": self.categories,
                    "total_businesses": len(self.all_businesses),
                    "parsed_at": datetime.now().isoformat()
                },
                "businesses": self.all_businesses
            }

            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            print("\n" + "=" * 70)
            print(f"ДАННЫЕ РАЗВЛЕЧЕНИЙ И МАГАЗИНОВ ДЛЯ {CITY.upper()}")
            print("=" * 70)
            print(f"Всего мест: {len(self.all_businesses)}")
            print(f"С координатами: {sum(1 for b in self.all_businesses if b['latitude'])}")
            print(f"С общим рейтингом: {sum(1 for b in self.all_businesses if b.get('overall_rating'))}")
            print(f"С числом оценок: {sum(1 for b in self.all_businesses if b.get('ratings_count'))}")
            print(f"\nВыходной файл: {OUTPUT_FILE}")

            print(f"\n📊 Сводка по категориям:")
            for category_type in ["Объекты культуры", "Развлечения", "Спорт", "Шоппинг"]:
                count = sum(1 for b in self.all_businesses if b['category_type'] == category_type)
                if count > 0:
                    print(f"  • {category_type}: {count} мест")

            if self.all_businesses:
                print(f"\n🏢 Примеры мест в {self.city}:")
                for b in self.all_businesses[:8]:
                    print(f"\n  • {b['name']}")
                    print(f"    📍 {b['category_type']} / {b['subcategory']}")
                    if b.get('overall_rating'):
                        print(f"    ⭐ Общий рейтинг: {b['overall_rating']} ({b.get('ratings_count', '?')} оценок)")
                    if b.get('latitude'):
                        print(f"    🗺️ {b['latitude']:.6f}, {b['longitude']:.6f}")

            print("\n" + "=" * 70)

        except Exception as e:
            logger.error(f"Парсер упал: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.driver:
                self.driver.quit()


if __name__ == "__main__":
    print("=" * 70)
    print(f"Yandex Maps Parser – Развлечения и магазины для {CITY}")
    print("=" * 70)
    parser = YandexEntertainmentParser(CITY, CATEGORIES, HEADLESS)
    parser.run()
    print("\n✅ Готово!")