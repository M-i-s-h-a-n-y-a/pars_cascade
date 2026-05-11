import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import json
import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import unary_union


# --- 1. Загрузка и подготовка данных ---
@st.cache_data
def load_data(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        items = data.get('businesses', data.get('stores', []))
        records = []
        for item in items:
            if item.get('latitude') and item.get('longitude'):
                records.append({
                    'name': item['name'],
                    'category': item['category'],
                    'overall_rating': item.get('overall_rating'),
                    'ratings_count': item.get('ratings_count'),
                    'latitude': item['latitude'],
                    'longitude': item['longitude'],
                    'url': item.get('url', '')
                })
        return pd.DataFrame(records)
    except Exception as e:
        st.error(f"Ошибка загрузки файла {file_path}: {e}")
        return pd.DataFrame()


@st.cache_data
def load_geojson_districts(file_path):
    """Загружает GeoJSON с районами и конвертирует координаты из EPSG:3857 в WGS84"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            geojson_data = json.load(f)

        transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

        for feature in geojson_data['features']:
            if feature['geometry']['type'] == 'MultiPolygon':
                new_coords = []
                for polygon in feature['geometry']['coordinates']:
                    new_polygon = []
                    for ring in polygon:
                        new_ring = []
                        for coord in ring:
                            lon, lat = transformer.transform(coord[0], coord[1])
                            new_ring.append([lon, lat])
                        new_polygon.append(new_ring)
                    new_coords.append(new_polygon)
                feature['geometry']['coordinates'] = new_coords

        return geojson_data
    except Exception as e:
        st.error(f"Ошибка загрузки GeoJSON: {e}")
        return None


@st.cache_data
def filter_objects_by_districts(df, geojson_data):
    """Оставляет только объекты, которые находятся внутри районов Байкальска"""
    if geojson_data is None:
        return df

    district_geometries = []
    for feature in geojson_data['features']:
        geom = shape(feature['geometry'])
        district_geometries.append(geom)

    all_districts = unary_union(district_geometries)

    mask = df.apply(lambda row: all_districts.contains(Point(row['longitude'], row['latitude'])), axis=1)
    return df[mask].copy()


def get_object_district(point, geojson_data):
    """Определяет к какому району относится объект"""
    for feature in geojson_data['features']:
        geom = shape(feature['geometry'])
        if geom.contains(point):
            return feature['properties'].get('district_name', 'Неизвестный')
    return 'За пределами районов'


# Загрузка данных из папки ./data/
df_accommodation = load_data('./data/yandex_maps_accommodation.json')
df_food = load_data('./data/yandex_maps_food.json')
df_stores = load_data('./data/yandex_maps_stores.json')

df_accommodation['object_type'] = 'Проживание'
df_food['object_type'] = 'Питание'
df_stores['object_type'] = 'Магазины и аптеки'

df_all = pd.concat([df_accommodation, df_food, df_stores], ignore_index=True)

# Загрузка границ районов
districts_geojson = load_geojson_districts('./data/districts_geojson.geojson')

# Фильтруем объекты - оставляем только внутри районов Байкальска
if districts_geojson:
    df_all = filter_objects_by_districts(df_all, districts_geojson)

    # Добавляем информацию о районе для каждого объекта
    df_all['district'] = df_all.apply(
        lambda row: get_object_district(Point(row['longitude'], row['latitude']), districts_geojson),
        axis=1
    )

# --- 2. Цветовые схемы ---
# Кастомные цветовые схемы для градиента
CUSTOM_COLORSCALES = {
    'Желто-красный': [
        [0.0, '#ffffb2'],  # светло-желтый
        [0.25, '#fed976'],  # желтый
        [0.5, '#fd8d3c'],  # оранжевый
        [0.75, '#e31a1c'],  # красный
        [1.0, '#800026']  # темно-красный
    ],
    'Голубо-синий': [
        [0.0, '#e3f2fd'],  # очень светло-голубой
        [0.25, '#90caf9'],  # светло-голубой
        [0.5, '#42a5f5'],  # голубой
        [0.75, '#1565c0'],  # синий
        [1.0, '#0d47a1']  # темно-синий
    ]
}

# Цвета точек в зависимости от градиента
POINT_COLORS = {
    'Желто-красный': '#800026',  # темно-красный
    'Голубо-синий': '#0d47a1'  # темно-синий
}

# Цвета для разных районов
DISTRICT_COLORS = {
    'Восточный': '#FF6B6B',  # кораллово-красный
    'Строитель': '#4ECDC4',  # бирюзовый
    'Южный': '#45B7D1',  # небесно-голубой
    'Юго-Западный': '#96CEB4',  # мятно-зеленый
    'Гагарина': '#FFEAA7',  # светло-желтый
    'Красный Ключ': '#DDA0DD',  # сливовый
    'Промплощадка': '#98D8C8'  # светло-бирюзовый
}

# --- 3. Интерфейс Streamlit ---
st.set_page_config(page_title="Плотность объектов в Байкальске", layout="wide")
st.title("📍 Дашборд коммерческих объектов г. Байкальск")
st.markdown(
    "Анализ плотности размещения объектов проживания, питания и торговли в пределах административных границ города.")

# Боковая панель с фильтрами
st.sidebar.header("🎛️ Фильтры данных")

# Выбор типов объектов
object_types = st.sidebar.multiselect(
    "Типы объектов:",
    options=sorted(df_all['object_type'].unique()),
    default=sorted(df_all['object_type'].unique())
)

# Выбор районов
if districts_geojson:
    district_names = sorted(df_all['district'].unique())
    selected_districts = st.sidebar.multiselect(
        "Районы Байкальска:",
        options=district_names,
        default=district_names
    )
else:
    selected_districts = []

# Слайдер для фильтрации по рейтингу
rating_range = st.sidebar.slider(
    "Диапазон рейтинга:",
    min_value=0.0,
    max_value=5.0,
    value=(0.0, 5.0),
    step=0.1
)

# Слайдер для фильтрации по количеству отзывов
if not df_all.empty and df_all['ratings_count'].notna().any():
    max_reviews = int(df_all['ratings_count'].max())
    reviews_range = st.sidebar.slider(
        "Диапазон количества отзывов:",
        min_value=0,
        max_value=max_reviews,
        value=(0, max_reviews),
        step=1
    )
else:
    max_reviews = 1
    reviews_range = (0, 1)

# --- 4. Настройки градиента ---
st.sidebar.header("🌡️ Настройки тепловой карты")

colorscale_name = st.sidebar.selectbox(
    "Цветовая схема градиента:",
    options=list(CUSTOM_COLORSCALES.keys()),
    index=0,
    help="Желто-красный: темно-красные точки переходят в светло-желтые\nГолубо-синий: темно-синие точки переходят в светло-голубые"
)

radius_px = st.sidebar.slider(
    "Радиус размытия:",
    min_value=5,
    max_value=50,
    value=20,
    step=5,
    help="Больший радиус = более плавные и широкие зоны плотности"
)

opacity = st.sidebar.slider(
    "Прозрачность тепловой карты:",
    min_value=0.3,
    max_value=1.0,
    value=0.75,
    step=0.05
)

# --- 5. Настройки отображения районов ---
st.sidebar.header("🗺️ Настройки районов")

show_districts = st.sidebar.checkbox("Показать границы районов", value=True)

fill_districts = st.sidebar.checkbox(
    "Закрасить районы",
    value=False,
    help="Заливка районов цветом с прозрачностью"
)

if fill_districts:
    fill_opacity = st.sidebar.slider(
        "Прозрачность заливки районов:",
        min_value=0.05,
        max_value=0.3,
        value=0.1,
        step=0.05
    )

if show_districts:
    district_line_width = st.sidebar.slider(
        "Толщина границ:",
        min_value=1,
        max_value=5,
        value=2,
        step=1
    )

    show_district_labels = st.sidebar.checkbox("Подписи районов", value=True)

# --- 6. Фильтрация данных ---
df_filtered = df_all.copy()

if object_types:
    df_filtered = df_filtered[df_filtered['object_type'].isin(object_types)]

if selected_districts:
    df_filtered = df_filtered[df_filtered['district'].isin(selected_districts)]

df_filtered = df_filtered.dropna(subset=['overall_rating', 'ratings_count'])
df_filtered = df_filtered[
    (df_filtered['overall_rating'] >= rating_range[0]) &
    (df_filtered['overall_rating'] <= rating_range[1]) &
    (df_filtered['ratings_count'] >= reviews_range[0]) &
    (df_filtered['ratings_count'] <= reviews_range[1])
    ]

# --- 7. Визуализация дашборда ---
if df_filtered.empty:
    st.warning("Нет данных, соответствующих заданным фильтрам. Попробуйте изменить параметры фильтрации.")
    st.stop()

# Основные метрики
col1, col2, col3, col4 = st.columns(4)
col1.metric("🏢 Всего объектов", len(df_filtered))
col2.metric("⭐ Средний рейтинг", f"{df_filtered['overall_rating'].mean():.2f}")
col3.metric("📝 Медиана отзывов", f"{int(df_filtered['ratings_count'].median())}")
col4.metric("🏘️ Районов", f"{df_filtered['district'].nunique()}")

st.markdown("---")

# Основная тепловая карта
st.subheader("🌡️ Тепловая карта плотности коммерческих объектов")

fig_density = go.Figure()

# Добавляем тепловую карту с кастомным градиентом
fig_density.add_trace(
    go.Densitymapbox(
        lat=df_filtered['latitude'],
        lon=df_filtered['longitude'],
        radius=radius_px,
        colorscale=CUSTOM_COLORSCALES[colorscale_name],
        opacity=opacity,
        zauto=True,
        name='Плотность',
        hoverinfo='skip',
        showscale=True,
        colorbar=dict(
            title="Концентрация",
            thickness=15,
            len=0.7,
            x=1.02
        )
    )
)

# Добавляем точки объектов с цветом, соответствующим градиенту
max_ratings = df_filtered['ratings_count'].max()
if max_ratings > 0:
    marker_sizes = (df_filtered['ratings_count'] / max_ratings * 12 + 4).clip(6, 16)
else:
    marker_sizes = 8

point_color = POINT_COLORS[colorscale_name]

fig_density.add_trace(
    go.Scattermapbox(
        lat=df_filtered['latitude'],
        lon=df_filtered['longitude'],
        mode='markers',
        marker=dict(
            size=marker_sizes,
            color=point_color,
            opacity=0.85,
            symbol='circle'
        ),
        hovertext=df_filtered['name'] +
                  '<br>🏷️ ' + df_filtered['category'] +
                  '<br>⭐ ' + df_filtered['overall_rating'].round(1).astype(str) +
                  ' | 📝 ' + df_filtered['ratings_count'].astype(str) + ' отзывов' +
                  '<br>📍 ' + df_filtered['district'],
        hoverinfo='text',
        name='Объекты',
        showlegend=False
    )
)

# Добавляем границы районов с разными цветами
if show_districts and districts_geojson:
    for feature in districts_geojson['features']:
        properties = feature['properties']
        district_name = properties.get('district_name', '')

        if selected_districts and district_name not in selected_districts:
            continue

        # Получаем цвет для района
        district_color = DISTRICT_COLORS.get(district_name, '#808080')

        # Конвертируем HEX в RGB для заливки
        hex_color = district_color.lstrip('#')
        rgb_color = tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

        coords = feature['geometry']['coordinates']

        for polygon in coords:
            for ring in polygon:
                lons = [coord[0] for coord in ring]
                lats = [coord[1] for coord in ring]

                # Закрашенный район
                if fill_districts:
                    fig_density.add_trace(
                        go.Scattermapbox(
                            lon=lons,
                            lat=lats,
                            mode='lines',
                            line=dict(width=0),
                            fill='toself',
                            fillcolor=f'rgba{rgb_color + (fill_opacity,)}',
                            name=f'{district_name} (заливка)',
                            hoverinfo='name',
                            showlegend=False
                        )
                    )

                # Граница района
                fig_density.add_trace(
                    go.Scattermapbox(
                        lon=lons,
                        lat=lats,
                        mode='lines',
                        line=dict(
                            width=district_line_width,
                            color=district_color
                        ),
                        name=district_name,
                        hoverinfo='name',
                        showlegend=True
                    )
                )

                # Подпись района
                if show_district_labels:
                    center_lon = np.mean(lons)
                    center_lat = np.mean(lats)

                    fig_density.add_trace(
                        go.Scattermapbox(
                            lon=[center_lon],
                            lat=[center_lat],
                            mode='text',
                            text=[district_name],
                            textfont=dict(
                                size=11,
                                color='#1a1a1a',
                                family='Arial Black'
                            ),
                            textposition='middle center',
                            hoverinfo='skip',
                            showlegend=False
                        )
                    )

# Настройка карты
fig_density.update_layout(
    mapbox=dict(
        style="open-street-map",
        center=dict(lat=51.517, lon=104.12),
        zoom=11.5,
        bearing=0,
        pitch=0
    ),
    margin={"r": 0, "t": 30, "l": 0, "b": 0},
    height=650,
    paper_bgcolor='#f0f2f6',
    plot_bgcolor='#f0f2f6',
    legend=dict(
        yanchor="top",
        y=0.99,
        xanchor="left",
        x=0.01,
        bgcolor='rgba(255,255,255,0.95)',
        bordercolor='#ccc',
        borderwidth=1,
        title=dict(text='Районы', font=dict(size=12, color='#333'))
    )
)

st.plotly_chart(fig_density, use_container_width=True)

# --- 8. Статистика по районам ---
st.markdown("---")
st.subheader("📊 Анализ по районам Байкальска")

col_left, col_right = st.columns([1.5, 1])

with col_left:
    district_stats = []
    for district in sorted(df_filtered['district'].unique()):
        df_district = df_filtered[df_filtered['district'] == district]

        area = 1
        if districts_geojson:
            for feature in districts_geojson['features']:
                if feature['properties'].get('district_name') == district:
                    area = feature['properties'].get('area', 1)
                    break

        density = len(df_district) / (area / 1_000_000) if area > 0 else 0

        stats = {
            'Район': district,
            '🏢 Объектов': len(df_district),
            '⭐ Рейтинг': round(df_district['overall_rating'].mean(), 2),
            '📝 Отзывы (медиана)': int(df_district['ratings_count'].median()),
            '📊 Плотность (об./км²)': round(density, 1)
        }
        district_stats.append(stats)

    if district_stats:
        df_stats = pd.DataFrame(district_stats)
        df_stats = df_stats.sort_values('🏢 Объектов', ascending=False)

        st.markdown("**Сводная таблица по районам:**")
        st.dataframe(
            df_stats.style.background_gradient(cmap='YlOrRd', subset=['📊 Плотность (об./км²)']),
            hide_index=True,
            use_container_width=True,
            height=250
        )

with col_right:
    if district_stats:
        df_viz = pd.DataFrame(district_stats)

        fig_district = px.bar(
            df_viz,
            x='Район',
            y='🏢 Объектов',
            title='Количество объектов по районам',
            color='Район',
            color_discrete_map=DISTRICT_COLORS,
            text='🏢 Объектов'
        )
        fig_district.update_traces(textposition='outside')
        fig_district.update_layout(
            height=400,
            showlegend=False
        )
        st.plotly_chart(fig_district, use_container_width=True)

# --- 9. Детальная информация ---
st.markdown("---")
st.subheader("📋 Детальный список объектов")

df_display = df_filtered[[
    'name', 'category', 'object_type', 'district',
    'overall_rating', 'ratings_count'
]].copy()
df_display.columns = [
    'Название', 'Категория', 'Тип', 'Район',
    'Рейтинг', 'Отзывы'
]
df_display = df_display.sort_values(['Район', 'Рейтинг'], ascending=[True, False])

st.dataframe(
    df_display.style.format({'Рейтинг': '{:.1f}'}).background_gradient(
        cmap='RdYlGn', subset=['Рейтинг']
    ),
    height=400,
    use_container_width=True
)

# Дополнительная статистика
col1, col2, col3 = st.columns(3)
with col1:
    st.markdown("**🏆 Топ-5 по рейтингу:**")
    top_rated = df_filtered.nlargest(5, 'overall_rating')[
        ['name', 'overall_rating', 'district']
    ]
    top_rated.columns = ['Название', 'Рейтинг', 'Район']
    st.dataframe(top_rated, hide_index=True, use_container_width=True)

with col2:
    st.markdown("**📝 Топ-5 по отзывам:**")
    top_reviews = df_filtered.nlargest(5, 'ratings_count')[
        ['name', 'ratings_count', 'district']
    ]
    top_reviews.columns = ['Название', 'Отзывы', 'Район']
    st.dataframe(top_reviews, hide_index=True, use_container_width=True)

with col3:
    st.markdown("**📊 Категории объектов:**")
    category_counts = df_filtered['category'].value_counts().head(5).reset_index()
    category_counts.columns = ['Категория', 'Количество']
    st.dataframe(category_counts, hide_index=True, use_container_width=True)

# Подвал
st.markdown("---")
col1, col2, col3 = st.columns(3)
col1.caption("📅 Данные актуальны на 04.05.2026")
col2.caption(f"📍 Показано объектов: {len(df_filtered)} из {len(df_all)}")
col3.caption("🗺️ Границы: OpenStreetMap | Яндекс.Карты")

# Стилизация
st.markdown("""
<style>
    .stMetric {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 15px;
        border-radius: 10px;
        color: white;
    }
    .stMetric label {
        color: white !important;
    }
    .stMetric [data-testid="stMetricValue"] {
        color: white !important;
    }
    .stDataFrame {
        border-radius: 10px;
        overflow: hidden;
    }
</style>
""", unsafe_allow_html=True)