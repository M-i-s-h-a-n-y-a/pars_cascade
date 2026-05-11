import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import plotly.express as px
import plotly.graph_objects as go
from scipy.spatial import cKDTree
from scipy.ndimage import gaussian_filter
from shapely.geometry import Polygon, Point, box
import geopandas as gpd
import rasterio
from rasterio.mask import mask
import warnings

warnings.filterwarnings('ignore')

# Настройка страницы
st.set_page_config(
    page_title="Байкальск: Плотность застройки",
    page_icon="🏔️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Пути к данным
DATA_DIR = "./data"


def get_data_path(filename):
    return os.path.join(DATA_DIR, filename)


# Заголовок дашборда
st.title("🏔️ Байкальск: Плотность застройки и общественные места")
st.markdown("### Тепловая карта плотности застройки с инфраструктурой")


# ============================================================
# 1. Загрузка данных
# ============================================================
@st.cache_data
def load_buildings():
    path = get_data_path('baikalsk_buildings.geojson')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


@st.cache_data
def load_businesses():
    path = get_data_path('yandex_maps_entertainment_filtered.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


@st.cache_data
def load_boundary():
    """Загрузка границы Байкальска"""
    path = get_data_path('districts_geojson.geojson')
    if not os.path.exists(path):
        return None

    districts_gdf = gpd.read_file(path)

    if districts_gdf.crs is None:
        bounds = districts_gdf.total_bounds
        if bounds[0] > 100 and bounds[1] > 50:
            districts_gdf = districts_gdf.set_crs('EPSG:4326')
        else:
            districts_gdf = districts_gdf.set_crs('EPSG:3857')
            districts_gdf = districts_gdf.to_crs('EPSG:4326')
    elif districts_gdf.crs.to_string() != 'EPSG:4326':
        districts_gdf = districts_gdf.to_crs('EPSG:4326')

    if 'district_name' in districts_gdf.columns:
        boundary = districts_gdf[districts_gdf['district_name'].notna()].dissolve()[['geometry']]
    else:
        boundary = districts_gdf.dissolve()[['geometry']]

    boundary['geometry'] = boundary['geometry'].simplify(0.0005)

    return boundary


@st.cache_data
def load_slope_data_full():
    """Загрузка полного slope.tif без обрезки"""
    path = get_data_path('slope.tif')
    if not os.path.exists(path):
        return None

    try:
        with rasterio.open(path) as src:
            return {
                'array': src.read(1),
                'transform': src.transform,
                'crs': src.crs.to_string(),
                'bounds': src.bounds,
                'width': src.width,
                'height': src.height
            }
    except Exception as e:
        st.warning(f"Ошибка загрузки slope.tif: {e}")
        return None


@st.cache_data
def load_slope_data_clipped(_boundary_gdf):
    """Загрузка и обрезка slope.tif по границе"""
    path = get_data_path('slope.tif')
    if not os.path.exists(path):
        return None

    try:
        with rasterio.open(path) as src:
            boundary_for_clip = _boundary_gdf.to_crs(src.crs)

            if box(*src.bounds).intersects(box(*boundary_for_clip.total_bounds)):
                out_image, out_transform = mask(src, boundary_for_clip.geometry.values, crop=True, nodata=np.nan)
                out_image = out_image[0]
            else:
                out_image = src.read(1)
                out_transform = src.transform

            return {
                'array': out_image,
                'transform': out_transform,
                'crs': src.crs.to_string()
            }
    except Exception as e:
        st.warning(f"Ошибка загрузки slope.tif: {e}")
        return None


@st.cache_data
def load_roads(_boundary_gdf):
    """Загрузка дорог"""
    path = get_data_path('baikalsk_roads.geojson')
    if not os.path.exists(path):
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')

    roads = gpd.read_file(path)
    if roads.crs is None:
        roads = roads.set_crs('EPSG:4326')
    elif roads.crs.to_string() != 'EPSG:4326':
        roads = roads.to_crs('EPSG:4326')

    try:
        roads = gpd.clip(roads, _boundary_gdf)
    except:
        pass
    return roads


@st.cache_data
def load_all_poi(_boundary_gdf):
    """Загрузка всех POI из разных файлов"""
    poi_list = []
    file_configs = [
        ('yandex_maps_food.json', 'food'),
        ('yandex_maps_accommodation.json', 'accommodation'),
        ('yandex_maps_stores.json', 'stores'),
        ('yandex_maps_entertainment_filtered.json', 'entertainment')
    ]

    for filename, cat_type in file_configs:
        filepath = get_data_path(filename)
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                businesses = data.get('businesses', data.get('stores', []))
                for biz in businesses:
                    lat, lon = biz.get('latitude'), biz.get('longitude')
                    if lat and lon:
                        rating = biz.get('overall_rating')
                        ratings_count = biz.get('ratings_count', 0)

                        poi_list.append({
                            'geometry': Point(lon, lat),
                            'name': str(biz.get('name', 'Unknown')),
                            'type': cat_type,
                            'subcategory': str(biz.get('subcategory', biz.get('category', ''))),
                            'rating': float(rating) if rating is not None else None,
                            'ratings_count': int(ratings_count) if ratings_count is not None else 0
                        })

    poi_gdf = gpd.GeoDataFrame(poi_list, crs='EPSG:4326') if poi_list else gpd.GeoDataFrame(
        columns=['geometry', 'name', 'type'], crs='EPSG:4326')

    try:
        poi_gdf = gpd.clip(poi_gdf, _boundary_gdf)
    except:
        pass

    return poi_gdf


@st.cache_data
def create_buffer_around_boundary(_boundary_gdf, buffer_km=15):
    """Создание буфера вокруг границы"""
    boundary_meters = _boundary_gdf.to_crs('EPSG:3857')
    buffer_meters = boundary_meters.geometry.buffer(buffer_km * 1000)
    buffer_gdf = gpd.GeoDataFrame(geometry=buffer_meters, crs='EPSG:3857')
    buffer_4326 = buffer_gdf.to_crs('EPSG:4326')
    return buffer_4326


@st.cache_data
def load_buildings_as_gdf(_buildings_data, _boundary_gdf):
    """Конвертация зданий в GeoDataFrame с фильтрацией по границе"""
    if _buildings_data is None:
        return gpd.GeoDataFrame(columns=['geometry'], crs='EPSG:4326')

    geometries = []
    for feature in _buildings_data['features']:
        if feature['geometry']['type'] == 'Polygon':
            coords = feature['geometry']['coordinates'][0]
            geometries.append(Polygon(coords))
        elif feature['geometry']['type'] == 'MultiPolygon':
            for polygon_coords in feature['geometry']['coordinates']:
                geometries.append(Polygon(polygon_coords[0]))

    buildings_gdf = gpd.GeoDataFrame(geometry=geometries, crs='EPSG:4326')

    try:
        buildings_gdf = gpd.clip(buildings_gdf, _boundary_gdf)
    except:
        pass

    return buildings_gdf


# Загрузка всех данных
with st.spinner("🔄 Загрузка данных..."):
    boundary = load_boundary()
    buildings_data = load_buildings()
    businesses_data = load_businesses()
    slope_data_full = load_slope_data_full()

    if boundary is not None:
        slope_data = load_slope_data_clipped(boundary)
        roads = load_roads(boundary)
        all_poi = load_all_poi(boundary)
        buffer_15km = create_buffer_around_boundary(boundary, buffer_km=15)
        buildings_gdf = load_buildings_as_gdf(buildings_data, boundary)
    else:
        slope_data = None
        roads = gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
        all_poi = gpd.GeoDataFrame(columns=['geometry', 'name', 'type'], crs='EPSG:4326')
        buffer_15km = None
        buildings_gdf = gpd.GeoDataFrame(columns=['geometry'], crs='EPSG:4326')


# Извлечение центроидов зданий
def extract_building_centroids(geojson_data):
    if geojson_data is None:
        return pd.DataFrame(columns=['longitude', 'latitude'])

    centroids = []
    for feature in geojson_data['features']:
        coords = feature['geometry']['coordinates'][0]
        polygon = Polygon(coords)
        centroid = polygon.centroid
        centroids.append({
            'longitude': centroid.x,
            'latitude': centroid.y
        })
    return pd.DataFrame(centroids)


buildings_df = extract_building_centroids(buildings_data)

# Извлечение бизнесов для развлечений
if businesses_data:
    businesses_list = []
    for biz in businesses_data['businesses']:
        businesses_list.append({
            'name': biz['name'],
            'category': biz['category_type'],
            'subcategory': biz['subcategory'],
            'latitude': biz['latitude'],
            'longitude': biz['longitude'],
            'rating': biz['overall_rating'],
            'ratings_count': biz['ratings_count']
        })
    entertainment_df = pd.DataFrame(businesses_list)
else:
    entertainment_df = pd.DataFrame(
        columns=['name', 'category', 'subcategory', 'latitude', 'longitude', 'rating', 'ratings_count'])

# ============================================================
# ПРАВИЛЬНЫЙ РАСЧЕТ ПЛОЩАДИ
# ============================================================
if boundary is not None:
    boundary_meters = boundary.to_crs('EPSG:3857')
    area_sq_meters = boundary_meters.geometry.area.sum()
    area_sq_km = area_sq_meters / 1_000_000
else:
    lon_range = buildings_df['longitude'].max() - buildings_df['longitude'].min()
    lat_range = buildings_df['latitude'].max() - buildings_df['latitude'].min()
    mid_lat = (buildings_df['latitude'].max() + buildings_df['latitude'].min()) / 2
    area_sq_km = lon_range * 111.32 * np.cos(np.radians(mid_lat)) * lat_range * 111.32

# Расчет статистики уклонов
slope_stats = None
if slope_data is not None:
    arr = slope_data['array']
    valid = arr[~np.isnan(arr)]
    if len(valid) > 0:
        slope_stats = {
            'mean': float(np.mean(valid)),
            'median': float(np.median(valid)),
            'std': float(np.std(valid)),
            'min': float(np.min(valid)),
            'max': float(np.max(valid)),
            'flat_pct': float((valid < 5).mean() * 100),
            'moderate_pct': float(((valid >= 5) & (valid < 15)).mean() * 100),
            'steep_pct': float((valid >= 15).mean() * 100),
            'valid': valid
        }

# ============================================================
# 2. Боковая панель с настройками
# ============================================================
st.sidebar.header("🔧 Настройки визуализации")

# Фильтр категорий POI
st.sidebar.subheader("Категории заведений")
poi_categories = {
    'food': ('🍽️ Питание', True),
    'accommodation': ('🏨 Проживание', True),
    'stores': ('🛒 Магазины', True),
    'entertainment': ('🎭 Развлечения', True)
}

selected_poi_types = []
for cat, (label, default) in poi_categories.items():
    if st.sidebar.checkbox(label, value=default, key=f"poi_{cat}"):
        selected_poi_types.append(cat)

# Фильтр категорий развлечений
st.sidebar.subheader("Подкатегории развлечений")
if not entertainment_df.empty:
    entertainment_categories = entertainment_df['category'].unique().tolist()
    selected_ent_categories = st.sidebar.multiselect(
        "Типы развлечений:",
        options=entertainment_categories,
        default=entertainment_categories,
        format_func=lambda x: {
            'Объекты культуры': '🎭 Культура',
            'Развлечения': '🎮 Развлечения',
            'Спорт': '⚽ Спорт',
            'Шоппинг': '🛍️ Шоппинг'
        }.get(x, x)
    )
else:
    selected_ent_categories = []

# Настройки тепловой карты
st.sidebar.subheader("Параметры тепловой карты")
radius_pixels = st.sidebar.slider(
    "Радиус влияния здания",
    min_value=10,
    max_value=100,
    value=40,
    step=5,
    help="Определяет 'размытие' тепловой карты"
)

grid_resolution = st.sidebar.slider(
    "Детализация сетки",
    min_value=50,
    max_value=300,
    value=150,
    step=10
)

# Только 2 цветовые схемы
colorscale_option = st.sidebar.selectbox(
    "Цветовая схема плотности",
    options=['Hot', 'Blues'],
    index=0,
    format_func=lambda x: {
        'Hot': '🔥 Красно-желтая (Hot)',
        'Blues': '💙 Сине-голубая (Blues)'
    }.get(x, x)
)

# Настройки для новой карты уклонов
st.sidebar.subheader("Настройки карты уклонов")
slope_opacity = st.sidebar.slider(
    "Прозрачность слоя уклонов",
    min_value=0.1,
    max_value=1.0,
    value=0.6,
    step=0.1
)

slope_colorscheme = st.sidebar.selectbox(
    "Цветовая схема уклонов",
    options=['RdYlGn_r', 'Viridis', 'Plasma', 'Terrain'],
    index=0,
    format_func=lambda x: {
        'RdYlGn_r': '🟢🟡🔴 Красно-желто-зеленая',
        'Viridis': '💜💚💛 Фиолетово-желтая',
        'Plasma': '💙💜💛 Сине-желтая',
        'Terrain': '🏔️ Ландшафтная'
    }.get(x, x)
)

# Опции отображения слоев
st.sidebar.subheader("Слои карты")
show_boundary = st.sidebar.checkbox("📍 Граница Байкальска", value=True)
show_roads_layer = st.sidebar.checkbox("🛣️ Дороги", value=True)
show_slope_overlay = st.sidebar.checkbox("🏔️ Рельеф (уклоны)", value=True)
show_contour = st.sidebar.checkbox("📐 Контуры плотности", value=False)
show_density_legend = st.sidebar.checkbox("📊 Легенда плотности", value=True)

# Настройки для карты с буфером
show_buffer = st.sidebar.checkbox("🔵 Показать 15-км зону", value=True)
show_buildings_polygons = st.sidebar.checkbox("🏠 Показывать контуры зданий", value=True)

min_rating = st.sidebar.slider(
    "Минимальный рейтинг заведений",
    min_value=0.0,
    max_value=5.0,
    value=0.0,
    step=0.5
)

# ============================================================
# 3. ПРАВИЛЬНЫЕ МЕТРИКИ
# ============================================================
# Фильтрация POI
filtered_poi = all_poi[all_poi['type'].isin(selected_poi_types)].copy() if selected_poi_types else all_poi.copy()
filtered_entertainment = entertainment_df[
    (entertainment_df['category'].isin(selected_ent_categories)) &
    (entertainment_df['rating'].fillna(0) >= min_rating)
    ] if not entertainment_df.empty else entertainment_df

# Плотность POI (количество на км²)
poi_density = len(filtered_poi) / area_sq_km if area_sq_km > 0 else 0

# Плотность застройки (зданий на км²)
building_density = len(buildings_df) / area_sq_km if area_sq_km > 0 else 0

# TAI (Territory Attractiveness Index)
if slope_stats:
    relief_score = min(slope_stats['flat_pct'] / 100 * 10, 10)
    poi_score = min(poi_density / 10 * 10, 10)
    building_score = min(building_density / 100 * 10, 10)
    tai = round(relief_score * 0.3 + poi_score * 0.4 + building_score * 0.3, 1)
else:
    poi_score = min(poi_density / 10 * 10, 10)
    building_score = min(building_density / 100 * 10, 10)
    tai = round(poi_score * 0.5 + building_score * 0.5, 1)

# Вывод метрик
st.markdown("---")
col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
col1.metric("🎯 TAI", f"{tai}/10", help="Индекс привлекательности территории")
col2.metric("📍 POI (фильтр)", len(filtered_poi))
col3.metric("📊 Плотность POI", f"{poi_density:.1f}/км²")
col4.metric("🏢 Зданий", len(buildings_df))
col5.metric("🏗️ Плотность застр.", f"{building_density:.1f}/км²")
col6.metric("📐 Площадь", f"{area_sq_km:.2f} км²")
col7.metric("⛰️ Средний уклон", f"{slope_stats['mean']:.1f}°" if slope_stats else "Н/Д")

# Отладочная информация
with st.expander("🔍 Диагностика расчетов", expanded=False):
    st.write(f"**Площадь территории:** {area_sq_km:.2f} км² ({area_sq_meters:,.0f} м²)")
    st.write(f"**Всего POI в данных:** {len(all_poi)}")
    st.write(f"**POI после фильтрации:** {len(filtered_poi)}")
    st.write(f"**Плотность POI:** {poi_density:.1f} POI/км²")
    st.write(f"**Плотность застройки:** {building_density:.1f} зданий/км²")

    if slope_stats:
        st.write(
            f"**Рельеф:** ровный {slope_stats['flat_pct']:.0f}%, умеренный {slope_stats['moderate_pct']:.0f}%, крутой {slope_stats['steep_pct']:.0f}%")
        st.write(f"**Оценка рельефа:** {relief_score:.1f}/10")

    st.write(f"**Оценка POI:** {poi_score:.1f}/10")
    st.write(f"**Оценка застройки:** {building_score:.1f}/10")
    st.write(f"**TAI:** {tai}/10")

    if boundary is not None:
        st.write(f"**CRS границы:** {boundary.crs}")
        st.write(f"**Координаты границы:** {boundary.total_bounds}")

st.markdown("---")

# ============================================================
# 4. Вкладки
# ============================================================
tab1, tab2, tab3 = st.tabs([
    "🗺️ Тепловая карта застройки",
    "⛰️ Анализ рельефа",
    "🏔️ Карта уклонов (15 км)"
])

# ---------- ВКЛАДКА 1: Тепловая карта ----------
with tab1:
    st.subheader("Тепловая карта плотности застройки с инфраструктурой")

    # Создание сетки
    if len(buildings_df) > 0:
        lon_min, lon_max = buildings_df['longitude'].min(), buildings_df['longitude'].max()
        lat_min, lat_max = buildings_df['latitude'].min(), buildings_df['latitude'].max()
    else:
        lon_min, lon_max, lat_min, lat_max = 104.0, 104.2, 51.4, 51.6

    if boundary is not None:
        bounds = boundary.total_bounds
        lon_min, lat_min, lon_max, lat_max = bounds[0], bounds[1], bounds[2], bounds[3]

    lon_padding = (lon_max - lon_min) * 0.05
    lat_padding = (lat_max - lat_min) * 0.05

    lon_range = np.linspace(lon_min - lon_padding, lon_max + lon_padding, grid_resolution)
    lat_range = np.linspace(lat_min - lat_padding, lat_max + lat_padding, grid_resolution)
    lon_grid, lat_grid = np.meshgrid(lon_range, lat_range)

    # Расчет плотности застройки с весом по площади полигона
    density = np.zeros_like(lon_grid)

    # Вычисляем площади всех зданий и находим максимум для нормализации
    if 'geometry' in buildings_df.columns:
        building_areas = buildings_df.geometry.area.values
        max_area = building_areas.max() if len(building_areas) > 0 else 1.0
    else:
        # Запасной вариант — если геометрия недоступна
        building_areas = np.ones(len(buildings_df))
        max_area = 1.0

    for idx, building in buildings_df.iterrows():
        # Вес здания — нормализованная площадь (от 0 до 1)
        if 'geometry' in buildings_df.columns:
            weight = building.geometry.area / max_area
        else:
            weight = 1.0  # равный вес, если геометрии нет

        # Сигма адаптивная — зависит от размера здания
        # Крупные здания "размазывают" плотность на большую площадь
        base_sigma = 0.03  # ~30 метров для самого маленького здания
        area_factor = np.sqrt(weight)  # корень из нормализованной площади
        sigma = base_sigma + 0.04 * area_factor  # от 0.03 до 0.07 градуса

        dist_lon = (lon_grid - building['longitude']) * 111.32 * np.cos(np.radians(building['latitude']))
        dist_lat = (lat_grid - building['latitude']) * 111.32
        dist_sq = dist_lon ** 2 + dist_lat ** 2

        # Добавляем взвешенный вклад
        density += weight * np.exp(-dist_sq / (2 * sigma ** 2))

    # Нормализация и сглаживание
    density_max = density.max()
    if density_max > 0:
        density_normalized = density / density_max
    else:
        density_normalized = density

    sigma_smooth = radius_pixels / 10
    density_smooth = gaussian_filter(density_normalized, sigma=sigma_smooth)

    # Кастомная красно-желтая схема на светлом фоне
    if colorscale_option == 'Hot':
        # Светлый фон для низких значений, красно-желтый для высоких
        custom_colorscale = [
            [0.0, '#f7f7f7'],  # Очень светлый серый (нет застройки)
            [0.1, '#ffcccc'],  # Светло-розовый
            [0.2, '#ff9999'],  # Розовый
            [0.3, '#ff6666'],  # Светло-красный
            [0.4, '#ff4444'],  # Красный
            [0.5, '#ff2222'],  # Насыщенный красный
            [0.6, '#ff6600'],  # Оранжевый
            [0.7, '#ff9900'],  # Темно-оранжевый
            [0.8, '#ffcc00'],  # Золотой
            [0.9, '#ffee00'],  # Желтый
            [1.0, '#ffff00']  # Ярко-желтый (максимальная плотность)
        ]
    else:
        # Сине-голубая схема
        custom_colorscale = 'Blues'

    # Создание фигуры
    fig = go.Figure()

    # Тепловая карта плотности застройки
    fig.add_trace(go.Heatmap(
        z=density_smooth,
        x=lon_range,
        y=lat_range,
        colorscale=custom_colorscale,
        colorbar=dict(
            title=dict(text="Плотность<br>застройки", font=dict(size=12, color='#333333')),
            tickformat=".2f",
            tickfont=dict(color='#333333'),
            len=0.6,
            y=0.5,
            yanchor='middle',
            x=1.02,
            xanchor='left',
            thickness=15
        ) if show_density_legend else None,
        opacity=0.9,
        hovertemplate='Плотность застройки: %{z:.2f}<br>Долгота: %{x:.4f}<br>Широта: %{y:.4f}<extra></extra>',
        showscale=show_density_legend,
        name='Плотность застройки'
    ))

    # Контуры плотности
    if show_contour:
        fig.add_trace(go.Contour(
            z=density_smooth,
            x=lon_range,
            y=lat_range,
            contours=dict(
                start=0.1,
                end=0.9,
                size=0.2,
                coloring='lines'
            ),
            line=dict(width=0.5, color='white'),
            showscale=False,
            hoverinfo='skip',
            name='Контуры плотности'
        ))

    # Рельеф (уклоны) как overlay
    if show_slope_overlay and slope_data is not None:
        slope_arr = slope_data['array']
        slope_valid = ~np.isnan(slope_arr)

        if slope_valid.any():
            sample_step = max(1, min(slope_arr.shape) // 200)
            slope_sampled = slope_arr[::sample_step, ::sample_step]

            fig.add_trace(go.Contour(
                z=slope_sampled,
                colorscale=[[0, 'green'], [0.5, 'yellow'], [1, 'red']],
                contours=dict(
                    start=5,
                    end=30,
                    size=5
                ),
                line=dict(width=1),
                showscale=False,
                hoverinfo='skip',
                name='Уклоны (контуры)',
                opacity=0.3
            ))

    # Граница Байкальска
    if show_boundary and boundary is not None:
        for geom in boundary.geometry:
            if geom.geom_type == 'Polygon':
                x, y = geom.exterior.xy
                fig.add_trace(go.Scatter(
                    x=list(x),
                    y=list(y),
                    mode='lines',
                    line=dict(color='red', width=2, dash='dash'),
                    name='Граница Байкальска',
                    hoverinfo='skip'
                ))
            elif geom.geom_type == 'MultiPolygon':
                for poly in geom.geoms:
                    x, y = poly.exterior.xy
                    fig.add_trace(go.Scatter(
                        x=list(x),
                        y=list(y),
                        mode='lines',
                        line=dict(color='red', width=2, dash='dash'),
                        name='Граница Байкальска',
                        hoverinfo='skip',
                        showlegend=False
                    ))

    # Дороги
    if show_roads_layer and len(roads) > 0:
        for geom in roads.geometry:
            if geom.geom_type == 'LineString':
                x, y = geom.xy
                fig.add_trace(go.Scatter(
                    x=list(x),
                    y=list(y),
                    mode='lines',
                    line=dict(color='#3498db', width=1),
                    name='Дороги',
                    hoverinfo='skip',
                    showlegend=False
                ))

    # Заведения развлечений
    category_config_ent = {
        'Объекты культуры': {'emoji': '🎭', 'color': '#8B00FF', 'symbol': 'star-diamond', 'name': 'Культура'},
        # Ярко-фиолетовый
        'Развлечения': {'emoji': '🎮', 'color': '#00CED1', 'symbol': 'diamond', 'name': 'Развлечения'},
        # Бирюзовый (Dark Turquoise)
        'Спорт': {'emoji': '⚽', 'color': '#00FF7F', 'symbol': 'triangle-up', 'name': 'Спорт'},  # Spring Green
        'Шоппинг': {'emoji': '🛍️', 'color': '#FF1493', 'symbol': 'square', 'name': 'Шоппинг'}  # Deep Pink
    }

    for category in selected_ent_categories:
        cat_data = filtered_entertainment[filtered_entertainment['category'] == category]
        if len(cat_data) == 0:
            continue

        config = category_config_ent.get(category, {
            'emoji': '📍', 'color': '#FFFFFF', 'symbol': 'circle', 'name': category
        })

        fig.add_trace(go.Scatter(
            x=cat_data['longitude'],
            y=cat_data['latitude'],
            mode='markers+text',
            marker=dict(
                size=14,
                color=config['color'],
                symbol=config['symbol'],
                line=dict(width=2, color='white'),
                opacity=0.9
            ),
            text=[config['emoji']] * len(cat_data),
            textposition='top center',
            textfont=dict(size=16),
            name=config['emoji'] + ' ' + config['name'],
            customdata=np.stack([
                cat_data['name'],
                cat_data['rating'].fillna('Нет данных'),
                cat_data['subcategory']
            ], axis=-1),
            hovertemplate=(
                    '<b>%{customdata[0]}</b><br>'
                    'Категория: ' + config['name'] + '<br>'
                                                     'Подкатегория: %{customdata[2]}<br>'
                                                     'Рейтинг: %{customdata[1]}<br>'
                                                     '<extra></extra>'
            )
        ))

    # Прочие POI (питание, проживание, магазины)
    poi_config = {
        'food': {'emoji': '🍽️', 'color': '#00BFFF', 'symbol': 'circle', 'name': 'Питание'},  # Deep Sky Blue
        'accommodation': {'emoji': '🏨', 'color': '#FFD700', 'symbol': 'square', 'name': 'Проживание'},  # Золотой (Gold)
        'stores': {'emoji': '🛒', 'color': '#FF69B4', 'symbol': 'diamond', 'name': 'Магазины'}  # Hot Pink
    }

    for poi_type in selected_poi_types:
        if poi_type in ['entertainment']:
            continue

        poi_subset = filtered_poi[filtered_poi['type'] == poi_type]
        if len(poi_subset) == 0:
            continue

        config = poi_config.get(poi_type, {'emoji': '📍', 'color': '#999', 'symbol': 'circle', 'name': poi_type})

        fig.add_trace(go.Scatter(
            x=[p.geometry.x for _, p in poi_subset.iterrows()],
            y=[p.geometry.y for _, p in poi_subset.iterrows()],
            mode='markers',
            marker=dict(
                size=8,
                color=config['color'],
                symbol=config['symbol'],
                line=dict(width=1, color='white'),
                opacity=0.7
            ),
            name=config['emoji'] + ' ' + config['name'],
            customdata=poi_subset[['name', 'rating', 'subcategory']].values,
            hovertemplate=(
                    '<b>%{customdata[0]}</b><br>'
                    'Тип: ' + config['name'] + '<br>'
                                               'Подкатегория: %{customdata[2]}<br>'
                                               'Рейтинг: %{customdata[1]}<br>'
                                               '<extra></extra>'
            )
        ))

    # Настройка layout с КОНТРАСТНОЙ легендой
    fig.update_layout(
        title=dict(
            text='Плотность застройки и инфраструктура Байкальска',
            x=0.5,
            font=dict(size=18, color='#333333')
        ),
        xaxis=dict(
            title='Долгота',
            scaleanchor='y',
            scaleratio=1,
            constrain='domain',
            gridcolor='#e0e0e0',
            zerolinecolor='#999999'
        ),
        yaxis=dict(
            title='Широта',
            gridcolor='#e0e0e0',
            zerolinecolor='#999999'
        ),
        # Легенда с ТЕМНЫМ фоном и БЕЛЫМ текстом
        legend=dict(
            x=0.01,
            y=0.02,
            xanchor='left',
            yanchor='bottom',
            bgcolor='rgba(30, 30, 30, 0.85)',  # Темный фон
            bordercolor='#666666',
            borderwidth=1,
            font=dict(
                size=11,
                color='white'  # Белый текст
            ),
            orientation='v',
            itemsizing='constant'
        ),
        # Светлый фон графика
        plot_bgcolor='#fafafa',
        paper_bgcolor='white',
        height=750,
        hovermode='closest'
    )

    fig.update_xaxes(range=[lon_min - lon_padding, lon_max + lon_padding], constrain='domain')
    fig.update_yaxes(range=[lat_min - lat_padding, lat_max + lat_padding], constrain='domain')

    st.plotly_chart(fig, use_container_width=True)

    # Легенда плотности
    col1, col2, col3 = st.columns(3)
    with col1:
        st.info("🟡 **Яркие области** — высокая плотность застройки (многоэтажки/плотно застроенный частный сектор)")
    with col2:
        st.info("🟠 **Средний тон** — умеренная плотность застройки")
    with col3:
        st.info("⬜ **Светлые области** — низкая плотность или нет застройки")

# ---------- ВКЛАДКА 2: Анализ рельефа ----------
with tab2:
    st.subheader("⛰️ Анализ рельефа территории Байкальска")

    if slope_stats:
        col_left, col_right = st.columns([2, 1])

        with col_left:
            fig_slope = go.Figure()
            fig_slope.add_trace(go.Histogram(
                x=slope_stats['valid'],
                nbinsx=40,
                name='Распределение уклонов',
                marker_color='#636efa',
                opacity=0.7
            ))

            for thresh, label, color in [(5, 'Равнина ≤5°', 'green'), (15, 'Умеренный склон 15°', 'orange')]:
                fig_slope.add_vline(
                    x=thresh,
                    line_dash="dash",
                    line_color=color,
                    annotation_text=label,
                    annotation_position="top"
                )

            fig_slope.update_layout(
                title="Распределение уклонов территории",
                xaxis_title="Уклон (градусы)",
                yaxis_title="Количество пикселей",
                bargap=0.1,
                height=450
            )
            st.plotly_chart(fig_slope, use_container_width=True)

        with col_right:
            zones = {
                'Равнина (<5°)': slope_stats['flat_pct'],
                'Умеренный (5-15°)': slope_stats['moderate_pct'],
                'Крутой (>15°)': slope_stats['steep_pct']
            }
            fig_pie = px.pie(
                values=list(zones.values()),
                names=list(zones.keys()),
                title="Доля территории по зонам",
                color_discrete_sequence=['#27ae60', '#f39c12', '#e74c3c'],
                hole=0.4
            )
            fig_pie.update_layout(height=450)
            st.plotly_chart(fig_pie, use_container_width=True)

        cols = st.columns(6)
        cols[0].metric("📐 Средний", f"{slope_stats['mean']:.1f}°")
        cols[1].metric("📏 Медиана", f"{slope_stats['median']:.1f}°")
        cols[2].metric("📊 Мин/Макс", f"{slope_stats['min']:.1f}° / {slope_stats['max']:.1f}°")
        cols[3].metric("🟢 Равнина", f"{slope_stats['flat_pct']:.0f}%")
        cols[4].metric("🟡 Умеренный", f"{slope_stats['moderate_pct']:.0f}%")
        cols[5].metric("🔴 Крутой", f"{slope_stats['steep_pct']:.0f}%")

        st.markdown("### 🗺️ Карта уклонов территории")
        st.caption("Зеленый = плоско, желтый = умеренно, красный = круто")

        arr = slope_data['array']
        sample_step = max(1, min(arr.shape) // 300)
        sampled = arr[::sample_step, ::sample_step]

        fig_heat = px.imshow(
            sampled,
            color_continuous_scale='RdYlGn_r',
            title="Уклоны территории Байкальска",
            labels={'color': 'Уклон (°)'},
            aspect='auto'
        )
        fig_heat.update_layout(height=500)
        st.plotly_chart(fig_heat, use_container_width=True)

        st.markdown("### 🏗️ Практические выводы")
        flat_km2 = area_sq_km * slope_stats['flat_pct'] / 100
        moderate_km2 = area_sq_km * slope_stats['moderate_pct'] / 100

        col1, col2 = st.columns(2)
        col1.info(f"**✅ Пригодно для застройки (уклон <5°):** {flat_km2:.1f} км² ({slope_stats['flat_pct']:.0f}%)")
        col2.warning(
            f"**⚠️ Требует спецпроектов (5-15°):** {moderate_km2:.1f} км² ({slope_stats['moderate_pct']:.0f}%)")
    else:
        st.warning("⛔ Файл slope.tif не найден в папке ./data/")

# ---------- ВКЛАДКА 3: Карта уклонов с буфером 15 км ----------
with tab3:
    st.subheader("🏔️ Уклоны в радиусе 15 км вокруг Байкальска")
    st.markdown("*На карте показаны границы города, здания и растровый слой уклонов с буферной зоной*")

    if slope_data_full is not None and boundary is not None:
        try:
            from pyproj import Transformer

            # Получаем данные растра
            arr_full = slope_data_full['array'].astype(np.float64)
            bounds = slope_data_full['bounds']
            raster_crs = slope_data_full['crs']

            # Заменяем nodata на NaN
            arr_full[arr_full <= -9999] = np.nan

            # Получаем центр Байкальска
            boundary_centroid = boundary.to_crs('EPSG:4326').geometry.centroid
            center_lon = boundary_centroid.x.mean()
            center_lat = boundary_centroid.y.mean()

            # Трансформеры
            transformer_to_raster = Transformer.from_crs('EPSG:4326', raster_crs, always_xy=True)
            transformer_to_wgs = Transformer.from_crs(raster_crs, 'EPSG:4326', always_xy=True)

            # Трансформируем углы буферной зоны в CRS растра
            buffer_bounds = buffer_15km.total_bounds
            buffer_lon_min, buffer_lat_min, buffer_lon_max, buffer_lat_max = buffer_bounds

            x_min_raster, y_min_raster = transformer_to_raster.transform(buffer_lon_min, buffer_lat_min)
            x_max_raster, y_max_raster = transformer_to_raster.transform(buffer_lon_max, buffer_lat_max)

            # Сетка координат растра в его CRS
            height, width = arr_full.shape
            pixel_size_x = (bounds.right - bounds.left) / width

            x_raster = np.linspace(bounds.left + pixel_size_x / 2, bounds.right - pixel_size_x / 2, width)
            y_raster = np.linspace(bounds.top, bounds.bottom, height)

            # Обрезаем растр до буферной зоны
            x_mask = (x_raster >= x_min_raster) & (x_raster <= x_max_raster)
            y_mask = (y_raster >= min(y_min_raster, y_max_raster)) & (y_raster <= max(y_min_raster, y_max_raster))

            x_indices = np.where(x_mask)[0]
            y_indices = np.where(y_mask)[0]

            if len(x_indices) > 0 and len(y_indices) > 0:
                x_start, x_end = x_indices[0], x_indices[-1] + 1
                y_start, y_end = y_indices[0], y_indices[-1] + 1

                arr_cropped = arr_full[y_start:y_end, x_start:x_end]
                x_cropped_raster = x_raster[x_start:x_end]
                y_cropped_raster = y_raster[y_start:y_end]

                # Конвертируем координаты в WGS84
                center_x_raster, center_y_raster = transformer_to_raster.transform(center_lon, center_lat)

                x_cropped_wgs = np.array([
                    transformer_to_wgs.transform(x, center_y_raster)[0]
                    for x in x_cropped_raster
                ])
                y_cropped_wgs = np.array([
                    transformer_to_wgs.transform(center_x_raster, y)[1]
                    for y in y_cropped_raster
                ])
            else:
                st.error("❌ Растр не покрывает территорию вокруг Байкальска")
                arr_cropped = np.zeros((100, 100))
                x_cropped_wgs = np.linspace(buffer_lon_min, buffer_lon_max, 100)
                y_cropped_wgs = np.linspace(buffer_lat_min, buffer_lat_max, 100)

            # Уменьшаем разрешение для производительности
            max_size = 400
            crop_height, crop_width = arr_cropped.shape

            if crop_width > max_size or crop_height > max_size:
                sample_factor = max(crop_width, crop_height) // max_size + 1
                arr_display = arr_cropped[::sample_factor, ::sample_factor].copy()
                x_display = x_cropped_wgs[::sample_factor]
                y_display = y_cropped_wgs[::sample_factor]
            else:
                arr_display = arr_cropped.copy()
                x_display = x_cropped_wgs
                y_display = y_cropped_wgs

            # Статистика для цветовой шкалы
            valid_mask = ~np.isnan(arr_display)
            if np.any(valid_mask):
                valid_data = arr_display[valid_mask]
                vmin = 0
                vmax = min(np.percentile(valid_data, 95), 30)
            else:
                vmin = 0
                vmax = 30

            # Конвертируем для Plotly
            arr_display_obj = arr_display.astype(object)
            arr_display_obj[np.isnan(arr_display)] = None

            # Создаем фигуру
            fig_slope_map = go.Figure()

            # Цветовая схема
            if slope_colorscheme == 'RdYlGn_r':
                colorscale = [
                    [0.0, '#006837'],
                    [0.17, '#1a9850'],
                    [0.33, '#66bd63'],
                    [0.5, '#ffffbf'],
                    [0.67, '#fdae61'],
                    [0.83, '#f46d43'],
                    [1.0, '#a50026']
                ]
            elif slope_colorscheme == 'Terrain':
                colorscale = [
                    [0.0, '#2d8c3c'],
                    [0.25, '#7ab648'],
                    [0.5, '#c5a43e'],
                    [0.75, '#c4743e'],
                    [1.0, '#8c4a30']
                ]
            else:
                colorscale = slope_colorscheme

            # Растровый слой уклонов
            fig_slope_map.add_trace(go.Heatmap(
                z=arr_display_obj,
                x=x_display,
                y=y_display,
                colorscale=colorscale,
                zmin=vmin,
                zmax=vmax,
                colorbar=dict(
                    title=dict(text="Уклон (°)", font=dict(size=14)),
                    len=0.8,
                    y=0.5,
                    yanchor='middle',
                    tickvals=[0, 5, 10, 15, 20, 25, 30],
                    ticktext=['0°', '5°', '10°', '15°', '20°', '25°', '30°+'],
                    thickness=20,
                    x=1.02
                ),
                opacity=slope_opacity,
                name='Уклоны',
                hoverongaps=False,
                hovertemplate='Уклон: %{z:.1f}°<br>Долгота: %{x:.4f}<br>Широта: %{y:.4f}<extra></extra>'
            ))

            # Изолинии уклонов
            if np.any(valid_mask):
                arr_contour = np.where(np.isnan(arr_display), -9999, arr_display)
                fig_slope_map.add_trace(go.Contour(
                    z=arr_contour,
                    x=x_display,
                    y=y_display,
                    contours=dict(
                        start=5,
                        end=25,
                        size=5,
                        showlabels=True,
                        labelfont=dict(size=10, color='#333333'),
                        coloring='lines'
                    ),
                    line=dict(width=1, color='rgba(0,0,0,0.4)'),
                    showscale=False,
                    hoverinfo='skip',
                    name='Изолинии уклонов'
                ))

            # Граница Байкальска
            if show_boundary:
                for geom in boundary.geometry:
                    if geom.geom_type == 'Polygon':
                        x, y = geom.exterior.xy
                        fig_slope_map.add_trace(go.Scatter(
                            x=list(x), y=list(y),
                            mode='lines',
                            line=dict(color='red', width=3),
                            name='Граница Байкальска',
                            hoverinfo='skip'
                        ))
                    elif geom.geom_type == 'MultiPolygon':
                        for i, poly in enumerate(geom.geoms):
                            x, y = poly.exterior.xy
                            fig_slope_map.add_trace(go.Scatter(
                                x=list(x), y=list(y),
                                mode='lines',
                                line=dict(color='red', width=3),
                                name='Граница Байкальска',
                                showlegend=(i == 0),
                                hoverinfo='skip'
                            ))

            # Буферная зона 15 км
            if show_buffer and buffer_15km is not None:
                for geom in buffer_15km.geometry:
                    if geom.geom_type == 'Polygon':
                        x, y = geom.exterior.xy
                        fig_slope_map.add_trace(go.Scatter(
                            x=list(x), y=list(y),
                            mode='lines',
                            line=dict(color='blue', width=2.5, dash='dash'),
                            name='Буфер 15 км',
                            hoverinfo='skip'
                        ))

            # Здания
            if show_buildings_polygons and len(buildings_gdf) > 0:
                buildings_x, buildings_y = [], []
                for geom in buildings_gdf.geometry[:300]:
                    if geom.geom_type == 'Polygon':
                        x, y = geom.exterior.xy
                        buildings_x.extend(list(x) + [None])
                        buildings_y.extend(list(y) + [None])
                if buildings_x:
                    fig_slope_map.add_trace(go.Scatter(
                        x=buildings_x, y=buildings_y,
                        mode='lines',
                        line=dict(color='#2c3e50', width=0.5),
                        fill='toself',
                        fillcolor='rgba(44, 62, 80, 0.4)',
                        name='Здания',
                        hoverinfo='skip'
                    ))

            # Центроиды зданий
            if len(buildings_df) > 0:
                buildings_sample = buildings_df.sample(n=min(len(buildings_df), 500), random_state=42)
                fig_slope_map.add_trace(go.Scatter(
                    x=buildings_sample['longitude'],
                    y=buildings_sample['latitude'],
                    mode='markers',
                    marker=dict(size=2.5, color='#2c3e50', symbol='circle', opacity=0.5),
                    name='Здания (точки)',
                    hovertemplate='Здание<br>Долгота: %{x:.4f}<br>Широта: %{y:.4f}<extra></extra>'
                ))

            # Настройка макета
            margin = 0.02
            fig_slope_map.update_layout(
                title=dict(
                    text='Уклоны территории в радиусе 15 км от Байкальска',
                    x=0.5,
                    font=dict(size=18, color='#333333')
                ),
                xaxis=dict(
                    title='Долгота',
                    range=[buffer_lon_min - margin, buffer_lon_max + margin],
                    scaleanchor='y',
                    scaleratio=1,
                    gridcolor='#e0e0e0',
                    zerolinecolor='#999999'
                ),
                yaxis=dict(
                    title='Широта',
                    range=[buffer_lat_min - margin, buffer_lat_max + margin],
                    gridcolor='#e0e0e0',
                    zerolinecolor='#999999'
                ),
                legend=dict(
                    x=0.01, y=0.99,
                    bgcolor='rgba(255, 255, 255, 0.9)',
                    bordercolor='#999999',
                    borderwidth=1,
                    font=dict(size=10)
                ),
                plot_bgcolor='#f5f5f5',
                paper_bgcolor='white',
                height=700,
                hovermode='closest'
            )

            st.plotly_chart(fig_slope_map, use_container_width=True)

            # Легенда уклонов
            st.markdown("### 🎨 Легенда уклонов и рекомендации")
            legend_cols = st.columns(5)

            legend_items = [
                ('#1a9850', 'white', '0-5°', 'Равнина', '✅ Идеально'),
                ('#66bd63', 'white', '5-10°', 'Слабый', '✅ Пригодно'),
                ('#fdae61', '#333', '10-15°', 'Умеренный', '⚠️ Подготовка'),
                ('#f46d43', 'white', '15-20°', 'Крутой', '🔶 Сложно'),
                ('#a50026', 'white', '20°+', 'Обрыв', '❌ Непригодно')
            ]

            for col, (color, text_color, degrees, terrain, recommendation) in zip(legend_cols, legend_items):
                with col:
                    st.markdown(f"""
                    <div style='background-color: {color}; padding: 10px; border-radius: 5px; color: {text_color}; text-align: center;'>
                        <strong>{degrees}</strong><br>
                        <small>{terrain}</small><br>
                        <small>{recommendation}</small>
                    </div>
                    """, unsafe_allow_html=True)

            # Информационные блоки
            st.markdown("---")
            info_cols = st.columns(3)

            with info_cols[0]:
                st.info(f"""
                **📊 Характеристики растра:**
                - Размер: {width}×{height} пикселей
                - CRS: {raster_crs} → WGS84
                - Диапазон уклонов: {np.nanmin(arr_full):.1f}° - {np.nanmax(arr_full):.1f}°
                - Покрытие данными: {(np.sum(~np.isnan(arr_full)) / arr_full.size * 100):.1f}%
                """)

            with info_cols[1]:
                if slope_stats:
                    st.success(f"""
                    **⛰️ Уклоны в черте города:**
                    - Средний: {slope_stats['mean']:.1f}°
                    - Медианный: {slope_stats['median']:.1f}°
                    - Равнина (<5°): {slope_stats['flat_pct']:.0f}%
                    - Умеренный (5-15°): {slope_stats['moderate_pct']:.0f}%
                    - Крутой (>15°): {slope_stats['steep_pct']:.0f}%
                    """)
                else:
                    st.success("**⛰️ Статистика уклонов недоступна**")

            with info_cols[2]:
                buffer_area = buffer_15km.to_crs('EPSG:3857').geometry.area.sum() / 1_000_000
                st.warning(f"""
                **🏗️ Характеристики застройки:**
                - Зданий в городе: {len(buildings_gdf)}
                - Площадь города: {area_sq_km:.1f} км²
                - Буферная зона: 15 км
                - Площадь буфера: {buffer_area:.1f} км²
                - Плотность застройки: {len(buildings_gdf) / area_sq_km:.0f} зд/км²
                """)

        except Exception as e:
            st.error(f"❌ Ошибка при создании карты уклонов: {str(e)}")

    else:
        if slope_data_full is None:
            st.error("""
            ⛔ **Файл slope.tif не найден**

            Поместите файл `slope.tif` в папку `./data/` для отображения карты уклонов.
            """)

        if boundary is None:
            st.error("""
            ⛔ **Границы города не загружены**

            Проверьте наличие файла `districts_geojson.geojson` в папке `./data/`.
            """)

# Сводка в сайдбаре
st.sidebar.markdown("---")
st.sidebar.caption(f"""
**📋 Сводка данных:**
- Площадь: {area_sq_km:.2f} км²
- POI всего: {len(all_poi)}
- POI фильтр: {len(filtered_poi)}
- Зданий: {len(buildings_df)}
- Плотность POI: {poi_density:.1f}/км²
- Плотность застр: {building_density:.1f}/км²
- slope.tif: {'✅' if slope_data else '❌'}
- Дороги: {'✅' if len(roads) > 0 else '❌'}
""")