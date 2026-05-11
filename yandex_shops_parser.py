"""
Парсер магазинов Байкальска (супермаркеты, аптеки, ТЦ).
Собирает рейтинг, адрес и координаты.
Выходной файл: yandex_maps_stores.json
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
CATEGORIES = ["супермаркеты", "торговые центры", "аптеки", "магазины", "продуктовые магазины"]
OUTPUT_FILE = "../data/yandex_maps_stores.json"
HEADLESS = False
MAX_BUSINESSES = 200
SCROLL_ATTEMPTS = 30

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class YandexStoresParser:
    def __init__(self, city: str, categories: List[str], headless: bool = False):
        self.city = city
        self.categories = categories
        self.headless = headless
        self.driver = None
        self.all_businesses = []
        self.seen_ids = set()

    def setup_driver(self):
        options = webdriver.ChromeOptions()
        if self.headless:
            options.add_argument('--headless')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        options.add_argument('--lang=ru')
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
        return name[:100]

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
            all_spans = soup.find_all(['span', 'div'], string=re.compile(r'\d+\s*оценок'))
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

        if ratings_count is None:
            page_text = soup.get_text()
            count_match = re.search(r'(\d+)\s*(?:оценок|оценки|оценка)', page_text)
            if count_match:
                try:
                    potential_count = int(count_match.group(1))
                    if potential_count < 100000:
                        ratings_count = potential_count
                except:
                    pass

        return overall_rating, ratings_count

    def parse_business_page(self, url: str, category: str) -> Optional[Dict]:
        org_id = self.get_org_id(url)
        if not org_id:
            return None
        if org_id in self.seen_ids:
            return None

        try:
            logger.info(f"  Загрузка магазина: {org_id[:50]}")
            self.driver.get(url)
            time.sleep(random.uniform(2, 3))

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

            overall_rating, ratings_count = self.parse_overall_rating_and_count(soup)
            coords = self.extract_coords(self.driver.current_url)

            business = {
                'id': org_id,
                'name': name,
                'address': address,
                'category': category,
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

    def search_and_collect_urls(self, category: str) -> List[str]:
        query = f"{self.city} {category}"
        logger.info(f"Поиск: {query}")

        self.driver.get("https://yandex.ru/maps/")
        time.sleep(3)

        try:
            accept_btn = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Согласен')]")
            accept_btn.click()
            time.sleep(1)
        except:
            pass

        try:
            search_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[placeholder*='Поиск'], input[placeholder*='Найти']"))
            )
            search_input.clear()
            search_input.send_keys(query)
            search_input.send_keys(Keys.RETURN)
            time.sleep(5)
        except Exception as e:
            logger.error(f"Поиск не удался: {e}")
            return []

        unique_urls = []
        seen_in_category = set()
        scroll_count = 0
        no_new_count = 0

        while scroll_count < SCROLL_ATTEMPTS and no_new_count < 8:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            try:
                show_more = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Показать еще')]")
                show_more.click()
                time.sleep(1.5)
            except:
                pass

            links = self.driver.execute_script("""
                var links = document.querySelectorAll('a[href*="/org/"]');
                var result = [];
                for (var i = 0; i < links.length; i++) {
                    var href = links[i].href;
                    if (href && href.indexOf('/org/') !== -1 && href.indexOf('?') === -1) {
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
                if org_id not in seen_in_category:
                    seen_in_category.add(org_id)
                    unique_urls.append(clean_url)
                    new_count += 1

            if new_count > 0:
                logger.info(f"  Найдено {len(unique_urls)} уникальных магазинов (+{new_count})")
                no_new_count = 0
            else:
                no_new_count += 1
                logger.info(f"  Новых магазинов нет ({no_new_count}/8)")

            scroll_count += 1
            if len(unique_urls) >= MAX_BUSINESSES:
                break

        logger.info(f"  Всего в категории `{category}`: {len(unique_urls)}")
        return unique_urls

    def run(self):
        try:
            self.setup_driver()

            total_collected = 0
            for idx, category in enumerate(self.categories, 1):
                if total_collected >= MAX_BUSINESSES:
                    logger.info(f"Достигнут лимит ({MAX_BUSINESSES}), остановка.")
                    break

                logger.info(f"\n{'=' * 60}")
                logger.info(f"[{idx}/{len(self.categories)}] {category.upper()}")
                logger.info(f"{'=' * 60}")

                try:
                    urls = self.search_and_collect_urls(category)
                    logger.info(f"Найдено {len(urls)} URL в категории {category}")
                    urls_to_process = urls[:MAX_BUSINESSES - total_collected]

                    for i, url in enumerate(urls_to_process, 1):
                        if total_collected >= MAX_BUSINESSES:
                            break
                        logger.info(f"  [{i}/{len(urls_to_process)}] Парсинг магазина...")
                        business = self.parse_business_page(url, category)
                        if business:
                            self.all_businesses.append(business)
                            total_collected += 1
                            info = []
                            if business.get('overall_rating'):
                                info.append(f"⭐{business['overall_rating']}")
                            if business.get('ratings_count'):
                                info.append(f"({business['ratings_count']} оценок)")
                            logger.info(f"    ✓ {business['name']} | {' '.join(info) if info else 'нет данных'}")
                        else:
                            logger.info(f"    ✗ Пропущено (дубликат или ошибка)")
                        time.sleep(random.uniform(1.5, 2.5))

                except Exception as e:
                    logger.error(f"Ошибка при обработке {category}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

            output = {
                "metadata": {
                    "city": self.city,
                    "categories": self.categories,
                    "total_businesses": len(self.all_businesses),
                    "parsed_at": datetime.now().isoformat()
                },
                "stores": self.all_businesses
            }

            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            print("\n" + "=" * 70)
            print("ИТОГИ СБОРА МАГАЗИНОВ")
            print("=" * 70)
            print(f"Всего магазинов: {len(self.all_businesses)}")
            print(f"С координатами: {sum(1 for b in self.all_businesses if b['latitude'])}")
            print(f"С рейтингом: {sum(1 for b in self.all_businesses if b.get('overall_rating'))}")
            print(f"С числом оценок: {sum(1 for b in self.all_businesses if b.get('ratings_count'))}")

            category_counts = {}
            for b in self.all_businesses:
                cat = b['category']
                category_counts[cat] = category_counts.get(cat, 0) + 1
            print("\n📊 Магазины по категориям:")
            for cat, count in sorted(category_counts.items()):
                print(f"  {cat}: {count}")

            print(f"\nВыходной файл: {OUTPUT_FILE}")

            if self.all_businesses:
                print("\n🏪 ПРИМЕРЫ МАГАЗИНОВ:")
                for b in self.all_businesses[:5]:
                    print(f"\n  • {b['name']} ({b['category']})")
                    if b.get('address'):
                        print(f"    📍 {b['address']}")
                    if b.get('overall_rating'):
                        print(f"    ⭐ Рейтинг: {b['overall_rating']} ({b.get('ratings_count', 0)} отзывов)")
                    if b.get('latitude'):
                        print(f"    🗺️ Координаты: {b['latitude']:.6f}, {b['longitude']:.6f}")

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
    print("Yandex Maps Store Parser")
    print("=" * 70)
    parser = YandexStoresParser(CITY, CATEGORIES, HEADLESS)
    parser.run()
    print("\n✅ Готово!")