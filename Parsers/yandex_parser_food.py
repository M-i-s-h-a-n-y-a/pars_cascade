#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yandex Maps Parser - Fixed Power BI Edition
Correctly parses overall rating and review count
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

# ==================== CONFIGURATION ====================
CITY = "Байкальск"
CATEGORIES = ["рестораны", "кафе", "столовые", "кофейни", "позные", "суши", "пицца" "фастфуд"]
OUTPUT_FILE = "yandex_maps_for_powerbi.json"
HEADLESS = False
MAX_BUSINESSES = 100

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class YandexFixedParser:
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

        self.driver = webdriver.Chrome(options=options)
        logger.info("Driver initialized")

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

    def parse_overall_rating_and_count(self, soup) -> Tuple[Optional[float], Optional[int]]:
        """Parse overall rating and number of ratings - COMBINED APPROACH"""
        overall_rating = None
        ratings_count = None

        # Method 1: Get rating from aria-label attribute (most reliable)
        rating_element = soup.find('div', {'aria-label': re.compile(r'Оценка \d+[,.]\d+ из 5')})
        if not rating_element:
            # Try with different selector
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

        # Method 2: Get count from elements with aria-label containing "оценк"
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

        # Method 3: Alternative - find count in span with text containing "оценок"
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

        # Method 4: Get rating from visible text if not found via aria-label
        if overall_rating is None:
            rating_text_elem = soup.find('span', class_=re.compile(r'business-rating-badge-view__rating-text'))
            if not rating_text_elem:
                rating_text_elem = soup.find('div', class_=re.compile(r'business-header-rating-view'))

            if rating_text_elem:
                text = rating_text_elem.get_text(strip=True)
                # Extract just the rating (first number with decimal)
                match = re.search(r'(\d+)[,.](\d+)', text)
                if match:
                    try:
                        rating_str = f"{match.group(1)}.{match.group(2)}"
                        # If there are more than 2 digits after decimal, take only first
                        if len(match.group(2)) > 2:
                            rating_str = f"{match.group(1)}.{match.group(2)[0]}"
                        overall_rating = float(rating_str)
                    except:
                        pass

                # If we still don't have count, try to extract from same text
                if ratings_count is None:
                    count_match = re.search(r'(\d+)\s*оценок', text)
                    if count_match:
                        try:
                            ratings_count = int(count_match.group(1))
                        except:
                            pass

        # Method 5: Final fallback - search entire page text
        if ratings_count is None:
            page_text = soup.get_text()
            # Look for patterns like "23 оценки" or "119 оценок"
            count_match = re.search(r'(\d+)\s*(?:оценок|оценки|оценка)', page_text)
            if count_match:
                try:
                    potential_count = int(count_match.group(1))
                    # Sanity check: count should be reasonable (not too large)
                    if potential_count < 100000:
                        ratings_count = potential_count
                except:
                    pass

        return overall_rating, ratings_count
    def parse_percent_ratings(self, soup) -> Dict[str, int]:
        """Parse percentage ratings from carousel items"""
        ratings = {}

        # Find all carousel items
        carousel_items = soup.find_all('div', class_=re.compile(r'carousel__item'))

        for item in carousel_items:
            text_elem = item.find('div', class_=re.compile(r'business-aspect-view__text'))
            rating_elem = item.find('span', class_=re.compile(r'business-aspect-view__rating'))

            if text_elem and rating_elem:
                category = text_elem.get_text(strip=True).strip('"')
                rating_text = rating_elem.get_text(strip=True)

                match = re.search(r'(\d+)%', rating_text)
                if match:
                    percent = int(match.group(1))

                    if 'Еда' in category or 'Кухня' in category:
                        ratings['food_percent'] = percent
                    elif 'Обслуживание' in category or 'Персонал' in category or 'Сервис' in category:
                        ratings['service_percent'] = percent
                    elif 'Атмосфера' in category:
                        ratings['atmosphere_percent'] = percent
                    elif 'Интерьер' in category:
                        ratings['interior_percent'] = percent
                    elif 'Чистота' in category:
                        ratings['cleanliness_percent'] = percent
                    elif 'Цены' in category or 'Соотношение' in category:
                        ratings['price_percent'] = percent

        # Alternative: direct business-aspect-view elements
        if not ratings:
            aspect_items = soup.find_all('div', class_=re.compile(r'business-aspect-view'))

            for item in aspect_items:
                text_elem = item.find('div', class_=re.compile(r'business-aspect-view__text'))
                rating_elem = item.find('span', class_=re.compile(r'business-aspect-view__rating'))

                if text_elem and rating_elem:
                    category = text_elem.get_text(strip=True).strip('"')
                    rating_text = rating_elem.get_text(strip=True)

                    match = re.search(r'(\d+)%', rating_text)
                    if match:
                        percent = int(match.group(1))

                        if 'Еда' in category:
                            ratings['food_percent'] = percent
                        elif 'Обслуживание' in category or 'Персонал' in category:
                            ratings['service_percent'] = percent
                        elif 'Атмосфера' in category:
                            ratings['atmosphere_percent'] = percent
                        elif 'Чистота' in category:
                            ratings['cleanliness_percent'] = percent

        return ratings

    def parse_business_page(self, url: str, category: str) -> Optional[Dict]:
        org_id = self.get_org_id(url)

        if not org_id or org_id in self.seen_ids:
            return None

        try:
            logger.info(f"  Loading: {org_id[:30]}")
            self.driver.get(url)
            time.sleep(3)

            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # Get name
            name = ""
            name_elem = soup.find('h1')
            if name_elem:
                name = name_elem.get_text(strip=True)
            name = self.clean_name(name)

            if not name or len(name) < 2:
                return None

            # Get address
            address = ""
            addr_elem = soup.select_one('div[class*="address"] span, span[class*="address"]')
            if addr_elem:
                address = addr_elem.get_text(strip=True)
                address = address.split('этаж')[0].strip()
                address = address.split('•')[0].strip()

            # Parse overall rating and count - FIXED
            overall_rating, ratings_count = self.parse_overall_rating_and_count(soup)

            # Parse percentage ratings
            percent_ratings = self.parse_percent_ratings(soup)

            # Get coordinates
            coords = self.extract_coords(self.driver.current_url)

            business = {
                'id': org_id,
                'name': name,
                'address': address,
                'category': category,
                'overall_rating': overall_rating,
                'ratings_count': ratings_count,
                'food_percent': percent_ratings.get('food_percent'),
                'service_percent': percent_ratings.get('service_percent'),
                'atmosphere_percent': percent_ratings.get('atmosphere_percent'),
                'interior_percent': percent_ratings.get('interior_percent'),
                'cleanliness_percent': percent_ratings.get('cleanliness_percent'),
                'price_percent': percent_ratings.get('price_percent'),
                'url': url,
                'latitude': coords[0] if coords else None,
                'longitude': coords[1] if coords else None,
                'parsed_at': datetime.now().isoformat()
            }

            self.seen_ids.add(org_id)
            return business

        except Exception as e:
            logger.debug(f"  Error parsing {org_id}: {e}")
            return None

    def search_and_collect_urls(self, category: str) -> List[str]:
        query = f"{self.city} {category}"
        logger.info(f"Searching: {query}")

        self.driver.get("https://yandex.ru/maps/")
        time.sleep(2)

        # Accept cookies
        try:
            accept_btn = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Согласен')]")
            accept_btn.click()
            time.sleep(1)
        except:
            pass

        # Search
        search_input = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Поиск']"))
        )
        search_input.clear()
        search_input.send_keys(query)
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
                logger.info(f"  Found {len(unique_ids)} unique businesses (+{new_count})")
                no_new_count = 0
            else:
                no_new_count += 1

            scroll_count += 1

        logger.info(f"  Total unique: {len(unique_ids)}")
        return list(urls_by_id.values())

    def run(self):
        try:
            self.setup_driver()

            for idx, category in enumerate(self.categories, 1):
                logger.info(f"\n{'=' * 60}")
                logger.info(f"[{idx}/{len(self.categories)}] {category.upper()}")
                logger.info(f"{'=' * 60}")

                try:
                    urls = self.search_and_collect_urls(category)
                    logger.info(f"Found {len(urls)} unique business URLs")

                    for i, url in enumerate(urls, 1):
                        if len(self.all_businesses) >= MAX_BUSINESSES:
                            break

                        logger.info(f"  [{i}/{len(urls)}] Parsing...")

                        business = self.parse_business_page(url, category)
                        if business:
                            self.all_businesses.append(business)
                            coords_str = f" [{business['latitude']:.6f}, {business['longitude']:.6f}]" if business[
                                'latitude'] else ""

                            info = []
                            if business.get('overall_rating'):
                                info.append(f"⭐{business['overall_rating']}")
                            if business.get('ratings_count'):
                                info.append(f"({business['ratings_count']} оценок)")
                            if business.get('food_percent'):
                                info.append(f"🍽️{business['food_percent']}%")
                            if business.get('service_percent'):
                                info.append(f"👨‍🍳{business['service_percent']}%")

                            logger.info(
                                f"    ✓ {business['name']}{coords_str} | {' '.join(info) if info else 'no ratings'}")
                        else:
                            logger.info(f"    ✗ Skipped")

                        time.sleep(random.uniform(1, 1.5))

                except Exception as e:
                    logger.error(f"Error processing {category}: {e}")
                    continue

            # Save results
            output = {
                "metadata": {
                    "city": self.city,
                    "categories": self.categories,
                    "total_businesses": len(self.all_businesses),
                    "parsed_at": datetime.now().isoformat()
                },
                "businesses": self.all_businesses
            }

            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            # Summary
            with_coords = sum(1 for b in self.all_businesses if b['latitude'])
            with_overall = sum(1 for b in self.all_businesses if b.get('overall_rating'))
            with_count = sum(1 for b in self.all_businesses if b.get('ratings_count'))
            with_food = sum(1 for b in self.all_businesses if b.get('food_percent'))

            print("\n" + "=" * 70)
            print("RESULTS FOR POWER BI")
            print("=" * 70)
            print(f"Businesses: {len(self.all_businesses)}")
            print(f"With coordinates: {with_coords}")
            print(f"With overall rating: {with_overall}")
            print(f"With ratings count: {with_count}")
            print(f"With food percent: {with_food}")
            print(f"\nOutput: {OUTPUT_FILE}")

            if self.all_businesses:
                print("\n🏪 Sample businesses:")
                for b in self.all_businesses[:5]:
                    print(f"\n  • {b['name']} ({b['category']})")
                    if b.get('overall_rating'):
                        print(f"    ⭐ Общий рейтинг: {b['overall_rating']} ({b.get('ratings_count', '?')} оценок)")
                    if b.get('food_percent'): print(f"    🍽️ Еда: {b['food_percent']}%")
                    if b.get('service_percent'): print(f"    👨‍🍳 Обслуживание: {b['service_percent']}%")
                    if b.get('atmosphere_percent'): print(f"    🎭 Атмосфера: {b['atmosphere_percent']}%")
                    if b.get('cleanliness_percent'): print(f"    🧹 Чистота: {b['cleanliness_percent']}%")
                    if b.get('latitude'): print(f"    🗺️ {b['latitude']:.6f}, {b['longitude']:.6f}")

            print("\n" + "=" * 70)

        except Exception as e:
            logger.error(f"Parser failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.driver:
                self.driver.quit()


if __name__ == "__main__":
    print("=" * 70)
    print("Yandex Maps Parser - Fixed Power BI Edition")
    print("=" * 70)
    print(f"City: {CITY}")
    print(f"Categories: {', '.join(CATEGORIES)}")
    print(f"Max businesses: {MAX_BUSINESSES}")
    print("=" * 70)

    parser = YandexFixedParser(CITY, CATEGORIES, HEADLESS)
    parser.run()

    print("\n✅ Done! Ready for Power BI.")
