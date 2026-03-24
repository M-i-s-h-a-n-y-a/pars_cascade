#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yandex Maps Parser - Stores Edition
Parses stores data: name, location, rating, and 24/7 status
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
CATEGORIES = ["супермаркеты", "торговые центры", "аптеки", "магазины",  "продуктовые магазины"]
OUTPUT_FILE = "yandex_maps_stores.json"
HEADLESS = False
MAX_BUSINESSES = 200  # Increased to collect more
SCROLL_ATTEMPTS = 30  # Increased scroll attempts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class YandexStoresParser:
    def __init__(self, city: str, categories: List[str], headless: bool = False):
        self.city = city
        self.categories = categories
        self.headless = headless
        self.driver = None
        self.all_businesses = []
        self.seen_ids = set()  # Still used to prevent duplicates across all categories

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
        """Parse overall rating and number of ratings"""
        overall_rating = None
        ratings_count = None

        # Method 1: Get rating from aria-label attribute
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

        # Method 2: Get count from elements with aria-label
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

        # Method 3: Find count in span with text
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

        # Method 4: Get rating from visible text
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

        # Method 5: Final fallback
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

    def parse_24_7_status(self, soup) -> Dict[str, any]:
        """Parse if the store is open 24/7"""
        result = {
            'is_24_7': False,
            'working_hours_raw': None
        }

        # Find working hours
        hours_elem = soup.find('div', class_=re.compile(r'business-hours-view__hours'))
        if hours_elem:
            hours_text = hours_elem.get_text(strip=True)
            result['working_hours_raw'] = hours_text

            # Check for 24/7 indicators
            if hours_text:
                hours_lower = hours_text.lower()
                if ('24' in hours_text and '7' in hours_text) or \
                        ('круглосуточно' in hours_lower) or \
                        ('ежедневно' in hours_lower and '00:00' in hours_text) or \
                        ('пн-вс' in hours_lower and '00:00' in hours_text):
                    result['is_24_7'] = True

        # Also check status
        status_elem = soup.find('div', class_=re.compile(r'business-hours-view__state'))
        if status_elem:
            status_text = status_elem.get_text(strip=True)
            if status_text and 'круглосуточно' in status_text.lower():
                result['is_24_7'] = True

        return result

    def parse_business_page(self, url: str, category: str) -> Optional[Dict]:
        org_id = self.get_org_id(url)

        if not org_id:
            return None

        # Skip if already collected from any category
        if org_id in self.seen_ids:
            return None

        try:
            logger.info(f"  Loading store: {org_id[:50]}")
            self.driver.get(url)
            time.sleep(random.uniform(2, 3))

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

            # Parse rating and count
            overall_rating, ratings_count = self.parse_overall_rating_and_count(soup)

            # Parse 24/7 status
            hours_info = self.parse_24_7_status(soup)

            # Get coordinates
            coords = self.extract_coords(self.driver.current_url)

            business = {
                'id': org_id,
                'name': name,
                'address': address,
                'category': category,
                'overall_rating': overall_rating,
                'ratings_count': ratings_count,
                'is_24_7': hours_info['is_24_7'],
                'working_hours': hours_info['working_hours_raw'],
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
        """Collect unique URLs for a category"""
        query = f"{self.city} {category}"
        logger.info(f"Searching: {query}")

        self.driver.get("https://yandex.ru/maps/")
        time.sleep(3)

        # Accept cookies if present
        try:
            accept_btn = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Согласен')]")
            accept_btn.click()
            time.sleep(1)
        except:
            pass

        # Search
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
            logger.error(f"Search failed: {e}")
            return []

        unique_urls = []
        seen_in_category = set()

        scroll_count = 0
        no_new_count = 0

        while scroll_count < SCROLL_ATTEMPTS and no_new_count < 8:
            # Scroll down
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

            # Try to click "Show more" button
            try:
                show_more = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Показать еще')]")
                show_more.click()
                time.sleep(1.5)
            except:
                pass

            # Extract all organization links
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

                # Only add if not seen in this category
                if org_id not in seen_in_category:
                    seen_in_category.add(org_id)
                    unique_urls.append(clean_url)
                    new_count += 1

            if new_count > 0:
                logger.info(f"  Found {len(unique_urls)} unique stores in this category (+{new_count})")
                no_new_count = 0
            else:
                no_new_count += 1
                logger.info(f"  No new stores found ({no_new_count}/8)")

            scroll_count += 1

            # Early exit if we've collected enough
            if len(unique_urls) >= MAX_BUSINESSES:
                break

        logger.info(f"  Total unique URLs in {category}: {len(unique_urls)}")
        return unique_urls

    def run(self):
        try:
            self.setup_driver()

            total_collected = 0

            for idx, category in enumerate(self.categories, 1):
                if total_collected >= MAX_BUSINESSES:
                    logger.info(f"Reached maximum businesses ({MAX_BUSINESSES}), stopping...")
                    break

                logger.info(f"\n{'=' * 60}")
                logger.info(f"[{idx}/{len(self.categories)}] {category.upper()}")
                logger.info(f"{'=' * 60}")

                try:
                    urls = self.search_and_collect_urls(category)
                    logger.info(f"Found {len(urls)} unique store URLs in {category}")

                    # Limit how many we process from this category
                    urls_to_process = urls[:MAX_BUSINESSES - total_collected]

                    for i, url in enumerate(urls_to_process, 1):
                        if total_collected >= MAX_BUSINESSES:
                            break

                        logger.info(f"  [{i}/{len(urls_to_process)}] Parsing store...")

                        business = self.parse_business_page(url, category)
                        if business:
                            self.all_businesses.append(business)
                            total_collected += 1

                            # Prepare info string for logging
                            info = []
                            if business.get('overall_rating'):
                                info.append(f"⭐{business['overall_rating']}")
                            if business.get('ratings_count'):
                                info.append(f"({business['ratings_count']} оценок)")
                            if business.get('is_24_7'):
                                info.append(f"🕐24/7")
                            elif business.get('working_hours'):
                                info.append(f"🕐{business['working_hours'][:20]}")

                            logger.info(f"    ✓ {business['name']} | {' '.join(info) if info else 'no data'}")
                        else:
                            logger.info(f"    ✗ Skipped (duplicate or error)")

                        time.sleep(random.uniform(1.5, 2.5))

                except Exception as e:
                    logger.error(f"Error processing {category}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

            # Save results
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

            # Summary statistics
            print("\n" + "=" * 70)
            print("STORES DATA COLLECTION SUMMARY")
            print("=" * 70)
            print(f"Total stores: {len(self.all_businesses)}")
            print(f"With coordinates: {sum(1 for b in self.all_businesses if b['latitude'])}")
            print(f"With ratings: {sum(1 for b in self.all_businesses if b.get('overall_rating'))}")
            print(f"With ratings count: {sum(1 for b in self.all_businesses if b.get('ratings_count'))}")
            print(f"24/7 stores: {sum(1 for b in self.all_businesses if b.get('is_24_7'))}")

            # Stats by category
            print("\n📊 Stores by category:")
            category_counts = {}
            for b in self.all_businesses:
                cat = b['category']
                category_counts[cat] = category_counts.get(cat, 0) + 1
            for cat, count in sorted(category_counts.items()):
                print(f"  {cat}: {count}")

            print(f"\nOutput file: {OUTPUT_FILE}")

            # Show sample stores
            if self.all_businesses:
                print("\n🏪 SAMPLE STORES:")
                for b in self.all_businesses[:5]:
                    print(f"\n  • {b['name']} ({b['category']})")
                    if b.get('address'):
                        print(f"    📍 {b['address']}")
                    if b.get('overall_rating'):
                        print(f"    ⭐ Rating: {b['overall_rating']} ({b.get('ratings_count', 0)} reviews)")
                    if b.get('is_24_7'):
                        print(f"    🕐 24/7 (круглосуточно)")
                    elif b.get('working_hours'):
                        print(f"    🕐 Hours: {b['working_hours']}")
                    if b.get('latitude'):
                        print(f"    🗺️ Coordinates: {b['latitude']:.6f}, {b['longitude']:.6f}")

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
    print("Yandex Maps Store Parser")
    print("=" * 70)
    print(f"City: {CITY}")
    print(f"Categories: {', '.join(CATEGORIES)}")
    print(f"Max stores: {MAX_BUSINESSES}")
    print("=" * 70)

    parser = YandexStoresParser(CITY, CATEGORIES, HEADLESS)
    parser.run()

    print("\n✅ Done! Store data saved.")