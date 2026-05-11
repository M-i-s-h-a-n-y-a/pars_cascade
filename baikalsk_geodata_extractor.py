"""
Извлечение геоданных Байкальска из OpenStreetMap:
здания, дороги, водные объекты, железные дороги.
Сохраняет результат в GeoJSON файлы.
"""

import osmnx as ox
import geopandas as gpd
import overpy
from shapely.geometry import Polygon, LineString
import time
import threading

# ==================== НАСТРОЙКИ ====================
PLACE_NAME = "Байкальск, Иркутская область, Россия"

OVERPLAY_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

TIMEOUT = 90
MAX_RETRIES = 3

# ---------------------------
# 1. ГЕОКОДИРОВАНИЕ
# ---------------------------
print(f"1. Загружаем границы '{PLACE_NAME}'...")
try:
    area_gdf = ox.geocode_to_gdf(PLACE_NAME, which_result=1)
    bbox = area_gdf.total_bounds
    print(f"   BBOX: {bbox}")
except Exception as e:
    print(f"   Ошибка: {e}")
    exit()

# ---------------------------
# 2. ФУНКЦИЯ ЗАПРОСА С ТАЙМАУТОМ
# ---------------------------
def query_overpass_with_timeout(endpoint, query, timeout):
    api = overpy.Overpass(url=endpoint)
    api.timeout = timeout
    result = []
    error = []

    def target():
        try:
            result.append(api.query(query))
        except Exception as e:
            error.append(e)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        raise TimeoutError(f"Таймаут {timeout} сек на {endpoint}")
    if error:
        raise error[0]
    return result[0]

def fetch_data(query, description):
    for endpoint in OVERPLAY_ENDPOINTS:
        for attempt in range(MAX_RETRIES):
            try:
                print(f"   [{description}] {endpoint} (попытка {attempt+1}/{MAX_RETRIES})...")
                data = query_overpass_with_timeout(endpoint, query, TIMEOUT)
                print(f"   ✅ Успешно!")
                return data
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
                if attempt < MAX_RETRIES - 1:
                    wait = 5 * (attempt + 1)
                    print(f"   Повтор через {wait} сек...")
                    time.sleep(wait)
        print(f"   Сервер {endpoint} не ответил, переключаемся...")
    raise Exception(f"Не удалось загрузить {description} ни с одного сервера.")

# ---------------------------
# 3. ЗДАНИЯ
# ---------------------------
print("\n2. Загружаем здания...")
buildings_query = f"""
    way["building"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
    out body;
    >;
    out skel qt;
"""
try:
    res = fetch_data(buildings_query, "здания")
    geoms = []
    for way in res.ways:
        coords = [(float(n.lon), float(n.lat)) for n in way.nodes]
        if len(coords) >= 3:
            geoms.append(Polygon(coords))
    buildings_gdf = gpd.GeoDataFrame(geometry=geoms, crs="EPSG:4326")
    print(f"   Загружено зданий: {len(buildings_gdf)}")
except Exception as e:
    print(f"   Не удалось загрузить здания: {e}")
    buildings_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

# ---------------------------
# 4. ДОРОГИ
# ---------------------------
print("\n3. Загружаем дороги...")
roads_query = f"""
    way["highway"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
    out body;
    >;
    out skel qt;
"""
try:
    res = fetch_data(roads_query, "дороги")
    geoms = []
    for way in res.ways:
        coords = [(float(n.lon), float(n.lat)) for n in way.nodes]
        if len(coords) >= 2:
            geoms.append(LineString(coords))
    roads_gdf = gpd.GeoDataFrame(geometry=geoms, crs="EPSG:4326")
    print(f"   Загружено дорог: {len(roads_gdf)}")
except Exception as e:
    print(f"   Не удалось загрузить дороги: {e}")
    roads_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

# ---------------------------
# 5. ВОДНЫЕ ОБЪЕКТЫ
# ---------------------------
print("\n4. Загружаем водные объекты...")
water_query = f"""
    (
        way["natural"="water"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["natural"="riverbank"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["water"="lake"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["water"="pond"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["water"="reservoir"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["landuse"="basin"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["waterway"="river"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["waterway"="stream"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["waterway"="canal"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["waterway"="drain"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["waterway"="ditch"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
    );
    out body;
    >;
    out skel qt;
"""
try:
    res = fetch_data(water_query, "водные объекты")
    water_polygons = []
    water_lines = []
    for way in res.ways:
        coords = [(float(n.lon), float(n.lat)) for n in way.nodes]
        if len(coords) < 2:
            continue
        is_polygon = len(coords) >= 3 and (coords[0] == coords[-1] or way.is_closed())
        if is_polygon:
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            water_polygons.append(Polygon(coords))
        else:
            water_lines.append(LineString(coords))
    water_polygons_gdf = gpd.GeoDataFrame(geometry=water_polygons, crs="EPSG:4326")
    water_lines_gdf = gpd.GeoDataFrame(geometry=water_lines, crs="EPSG:4326")
    print(f"   Водных полигонов (озёра, водохранилища): {len(water_polygons_gdf)}")
    print(f"   Водных линий (реки, ручьи, каналы): {len(water_lines_gdf)}")
except Exception as e:
    print(f"   Не удалось загрузить водные объекты: {e}")
    water_polygons_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    water_lines_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

# ---------------------------
# 6. ЖЕЛЕЗНЫЕ ДОРОГИ
# ---------------------------
print("\n5. Загружаем железные дороги...")
railways_query = f"""
    (
        way["railway"="rail"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["railway"="narrow_gauge"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["railway"="light_rail"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["railway"="subway"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["railway"="tram"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["railway"="monorail"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["railway"="disused"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
        way["railway"="abandoned"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});
    );
    out body;
    >;
    out skel qt;
"""
try:
    res = fetch_data(railways_query, "железные дороги")
    railway_geoms = []
    for way in res.ways:
        coords = [(float(n.lon), float(n.lat)) for n in way.nodes]
        if len(coords) >= 2:
            railway_geoms.append(LineString(coords))
    railways_gdf = gpd.GeoDataFrame(geometry=railway_geoms, crs="EPSG:4326")
    print(f"   Загружено железных дорог: {len(railways_gdf)}")
except Exception as e:
    print(f"   Не удалось загрузить железные дороги: {e}")
    railways_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

# ---------------------------
# 7. СОХРАНЕНИЕ
# ---------------------------
print("\n6. Сохраняем файлы...")
buildings_gdf.to_file("../data/baikalsk_buildings.geojson", driver="GeoJSON")
roads_gdf.to_file("../data/baikalsk_roads.geojson", driver="GeoJSON")
water_polygons_gdf.to_file("../data/baikalsk_water_polygons.geojson", driver="GeoJSON")
water_lines_gdf.to_file("../data/baikalsk_water_lines.geojson", driver="GeoJSON")
railways_gdf.to_file("../data/baikalsk_railways.geojson", driver="GeoJSON")

print("\n   Файлы сохранены:")
print("     - baikalsk_buildings.geojson")
print("     - baikalsk_roads.geojson")
print("     - baikalsk_water_polygons.geojson")
print("     - baikalsk_water_lines.geojson")
print("     - baikalsk_railways.geojson")

print("\n7. ИТОГО:")
print(f"   Здания: {len(buildings_gdf)}")
print(f"   Дороги: {len(roads_gdf)}")
print(f"   Водные полигоны: {len(water_polygons_gdf)}")
print(f"   Водные линии: {len(water_lines_gdf)}")
print(f"   Железные дороги: {len(railways_gdf)}")