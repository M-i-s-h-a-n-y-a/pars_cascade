import streamlit as st
import json
import pandas as pd
import numpy as np
from geopy.distance import geodesic
import folium
from streamlit_folium import st_folium
import plotly.express as px
import plotly.graph_objects as go
from shapely.geometry import Polygon

# Настройка страницы
st.set_page_config(
    page_title="Транспортная доступность Байкальска",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Заголовок дашборда
st.title("🚌 Анализ транспортной доступности Байкальска")
st.markdown("### Покрытие остановок и парковки в радиусе 15 км")


# Функция загрузки данных
@st.cache_data
def load_all_data():
    """Загрузка всех данных"""
    data = {}

    try:
        with open('./data/baikal_transport_stops.json', 'r', encoding='utf-8') as f:
            data['stops'] = json.load(f)
    except Exception as e:
        st.sidebar.error(f"❌ Остановки: {e}")
        return None

    try:
        with open('./data/yandex_maps_entertainment_filtered.json', 'r', encoding='utf-8') as f:
            data['entertainment'] = json.load(f)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Развлечения: {e}")
        data['entertainment'] = {'businesses': []}

    try:
        with open('./data/yandex_maps_food.json', 'r', encoding='utf-8') as f:
            data['food'] = json.load(f)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Питание: {e}")
        data['food'] = {'businesses': []}

    try:
        with open('./data/yandex_maps_stores.json', 'r', encoding='utf-8') as f:
            data['stores'] = json.load(f)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Магазины: {e}")
        data['stores'] = {'stores': []}

    try:
        with open('./data/car_parks.geojson', 'r', encoding='utf-8') as f:
            data['parks'] = json.load(f)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Парковки: {e}")
        data['parks'] = {'features': []}

    return data


# Загрузка данных
data = load_all_data()

if data is None:
    st.error("Не удалось загрузить данные. Проверьте наличие файлов.")
    st.stop()


# Обработка остановок
@st.cache_data
def process_stops(stops_data):
    """Обработка и классификация остановок"""
    stops_list = stops_data['stops']

    processed = []
    for stop in stops_list:
        name = stop['name']
        name_lower = name.lower()

        # Определение типа
        if any(term in name_lower for term in ['станция', 'вокзал', 'платформа', 'жд', 'ж/д', 'железнодорожный']):
            stop_type = 'Ж/Д остановка'
        elif 'автостанция' in name_lower or 'автовокзал' in name_lower:
            stop_type = 'Автостанция'
        elif 'нет посадки' in name_lower:
            stop_type = 'Техническая (нет посадки)'
        else:
            stop_type = 'Автобусная остановка'

        # Очистка названия
        clean_name = name.replace(' (нет посадки)', '').replace('(нет посадки)', '').strip()

        processed.append({
            'name': name,
            'clean_name': clean_name,
            'type': stop_type,
            'lat': stop['latitude'],
            'lon': stop['longitude'],
            'url': stop.get('url', '')
        })

    df = pd.DataFrame(processed)

    # Группировка по чистому названию
    grouped = df.groupby('clean_name').agg({
        'lat': 'mean',
        'lon': 'mean',
        'type': lambda x: x.mode()[0] if not x.mode().empty else x.iloc[0],
        'name': 'first',
        'url': 'first'
    }).reset_index()

    return df, grouped


# Обработка парковок
@st.cache_data
def process_parks(parks_data):
    """Обработка парковок"""
    parks = []

    for i, feature in enumerate(parks_data['features']):
        try:
            geom = feature['geometry']
            props = feature['properties']

            if geom['type'] == 'Polygon':
                coords = geom['coordinates'][0]
                folium_coords = [[c[1], c[0]] for c in coords]

                lats = [c[1] for c in coords]
                lons = [c[0] for c in coords]

                parks.append({
                    'id': props.get('@id', f'parking_{i}'),
                    'type': props.get('parking', 'surface'),
                    'center_lat': np.mean(lats),
                    'center_lon': np.mean(lons),
                    'coords': folium_coords
                })
        except Exception as e:
            continue

    return parks


# Сбор всех объектов инфраструктуры
@st.cache_data
def collect_all_objects(entertainment_data, food_data, stores_data):
    """Сбор всех объектов в единый список"""
    all_objects = []

    # Развлечения и культура
    for business in entertainment_data.get('businesses', []):
        all_objects.append({
            'name': business['name'],
            'category': '🎭 Развлечения и культура',
            'subcategory': business.get('subcategory', ''),
            'lat': business['latitude'],
            'lon': business['longitude'],
            'rating': business.get('overall_rating'),
            'ratings_count': business.get('ratings_count', 0)
        })

    # Заведения питания
    for business in food_data.get('businesses', []):
        all_objects.append({
            'name': business['name'],
            'category': '🍽️ Заведения питания',
            'subcategory': business.get('category', ''),
            'lat': business['latitude'],
            'lon': business['longitude'],
            'rating': business.get('overall_rating'),
            'ratings_count': business.get('ratings_count', 0)
        })

    # Магазины
    for store in stores_data.get('stores', []):
        all_objects.append({
            'name': store['name'],
            'category': '🏪 Магазины',
            'subcategory': store.get('category', ''),
            'lat': store['latitude'],
            'lon': store['longitude'],
            'rating': store.get('overall_rating'),
            'ratings_count': store.get('ratings_count', 0)
        })

    return pd.DataFrame(all_objects)


# Обработка данных
stops_original, stops_grouped = process_stops(data['stops'])
parks = process_parks(data['parks'])
df_objects = collect_all_objects(data['entertainment'], data['food'], data['stores'])

# Боковая панель с информацией о загрузке
st.sidebar.markdown("### 📦 Загрузка данных")
st.sidebar.success(f"✅ Остановок: {len(stops_original)} (уникальных: {len(stops_grouped)})")
st.sidebar.success(f"✅ Парковок: {len(parks)}")
st.sidebar.success(f"✅ Объектов питания: {len(data['food'].get('businesses', []))}")
st.sidebar.success(f"✅ Магазинов: {len(data['stores'].get('stores', []))}")
st.sidebar.success(f"✅ Развлечений: {len(data['entertainment'].get('businesses', []))}")

# Фильтры
st.sidebar.markdown("---")
st.sidebar.markdown("### 🎯 Параметры анализа")

# Тип остановок
stop_types = ['Все типы'] + sorted(stops_grouped['type'].unique().tolist())
selected_type = st.sidebar.selectbox("Тип остановок:", stop_types)

# Радиус буферной зоны
buffer_radius = st.sidebar.slider(
    "Радиус буферной зоны (м):",
    min_value=100,
    max_value=1000,
    value=500,
    step=50
)

# Категории для анализа
categories = st.sidebar.multiselect(
    "Категории для анализа покрытия:",
    ["🍽️ Заведения питания", "🏪 Магазины", "🎭 Развлечения и культура"],
    default=["🍽️ Заведения питания", "🏪 Магазины", "🎭 Развлечения и культура"]
)

# Показывать объекты на карте
show_objects = st.sidebar.checkbox("Показывать объекты на карте", value=True)

# Показывать парковки
show_parks = st.sidebar.checkbox("Показывать парковки", value=True)

# Фильтрация остановок
if selected_type == 'Все типы':
    filtered_stops = stops_grouped.copy()
else:
    filtered_stops = stops_grouped[stops_grouped['type'] == selected_type].copy()


# Функция расчета расстояния
def get_distance(lat1, lon1, lat2, lon2):
    return geodesic((lat1, lon1), (lat2, lon2)).meters


# Анализ покрытия
def calculate_coverage(stops_df, objects_df, categories_list, radius):
    """Расчет покрытия для каждой остановки"""
    results = []

    for _, stop in stops_df.iterrows():
        coverage = {
            'name': stop['clean_name'],
            'type': stop['type'],
            'lat': stop['lat'],
            'lon': stop['lon']
        }

        for _, obj in objects_df.iterrows():
            if obj['category'] not in categories_list:
                continue

            dist = get_distance(stop['lat'], stop['lon'], obj['lat'], obj['lon'])
            if dist <= radius:
                cat = obj['category']
                coverage[cat] = coverage.get(cat, 0) + 1

        results.append(coverage)

    df = pd.DataFrame(results)
    df = df.fillna(0)

    # Добавляем столбцы для категорий, если их нет
    for cat in categories_list:
        if cat not in df.columns:
            df[cat] = 0

    return df


# Расчет покрытия
df_coverage = calculate_coverage(filtered_stops, df_objects, categories, buffer_radius)


# Создание карты
def create_full_map(stops_df, parks_list, objects_df, buffer_radius, show_objects, show_parks, categories_list):
    """Создание полной карты с аналитикой"""

    # Центр карты
    center_lat = stops_df['lat'].mean() if not stops_df.empty else 51.517
    center_lon = stops_df['lon'].mean() if not stops_df.empty else 104.12

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13,
        tiles='CartoDB positron'
    )

    # Цвета для типов остановок
    type_colors = {
        'Автобусная остановка': '#2196F3',
        'Ж/Д остановка': '#f44336',
        'Автостанция': '#9C27B0',
        'Техническая (нет посадки)': '#757575'
    }

    # Добавление остановок и буферных зон
    for _, stop in stops_df.iterrows():
        color = type_colors.get(stop['type'], '#000000')

        # Буферная зона
        folium.Circle(
            location=[stop['lat'], stop['lon']],
            radius=buffer_radius,
            color=color,
            fill=True,
            fill_opacity=0.1,
            weight=2,
            popup=f"""
            <b>{stop['clean_name']}</b><br>
            Тип: {stop['type']}<br>
            Радиус: {buffer_radius} м<br>
            Координаты: {stop['lat']:.4f}, {stop['lon']:.4f}
            """
        ).add_to(m)

        # Маркер остановки
        icon_name = 'train' if stop['type'] == 'Ж/Д остановка' else 'bus'

        folium.Marker(
            location=[stop['lat'], stop['lon']],
            popup=f"""
            <b>{stop['clean_name']}</b><br>
            <small>{stop['type']}</small><br>
            <small>{stop['lat']:.4f}, {stop['lon']:.4f}</small>
            """,
            icon=folium.Icon(color=color.replace('#', '').lower() if color.startswith('#') else 'blue',
                             icon=icon_name, prefix='fa'),
            tooltip=stop['clean_name']
        ).add_to(m)

    # Добавление объектов инфраструктуры
    if show_objects and categories_list:
        # Объекты питания
        if '🍽️ Заведения питания' in categories_list:
            food_group = folium.FeatureGroup(name='🍽️ Заведения питания')
            food_objects = objects_df[objects_df['category'] == '🍽️ Заведения питания']
            for _, obj in food_objects.iterrows():
                folium.CircleMarker(
                    location=[obj['lat'], obj['lon']],
                    radius=5,
                    color='#ff6b6b',
                    fill=True,
                    fill_opacity=0.7,
                    popup=f"<b>{obj['name']}</b><br>{obj['subcategory']}<br>Рейтинг: {obj['rating']}",
                    tooltip=obj['name']
                ).add_to(food_group)
            food_group.add_to(m)

        # Магазины
        if '🏪 Магазины' in categories_list:
            store_group = folium.FeatureGroup(name='🏪 Магазины')
            store_objects = objects_df[objects_df['category'] == '🏪 Магазины']
            for _, obj in store_objects.iterrows():
                folium.CircleMarker(
                    location=[obj['lat'], obj['lon']],
                    radius=5,
                    color='#4ecdc4',
                    fill=True,
                    fill_opacity=0.7,
                    popup=f"<b>{obj['name']}</b><br>{obj['subcategory']}<br>Рейтинг: {obj['rating']}",
                    tooltip=obj['name']
                ).add_to(store_group)
            store_group.add_to(m)

        # Развлечения
        if '🎭 Развлечения и культура' in categories_list:
            ent_group = folium.FeatureGroup(name='🎭 Развлечения и культура')
            ent_objects = objects_df[objects_df['category'] == '🎭 Развлечения и культура']
            for _, obj in ent_objects.iterrows():
                folium.CircleMarker(
                    location=[obj['lat'], obj['lon']],
                    radius=5,
                    color='#ffd93d',
                    fill=True,
                    fill_opacity=0.7,
                    popup=f"<b>{obj['name']}</b><br>{obj['subcategory']}<br>Рейтинг: {obj['rating']}",
                    tooltip=obj['name']
                ).add_to(ent_group)
            ent_group.add_to(m)

    # Добавление парковок
    if show_parks and parks_list:
        parking_group = folium.FeatureGroup(name='🅿️ Парковки')
        for park in parks_list:
            folium.Polygon(
                locations=park['coords'],
                color='#555555',
                weight=3,
                fill=True,
                fill_color='#888888',
                fill_opacity=0.4,
                popup=f"<b>Парковка</b><br>ID: {park['id']}<br>Тип: {park['type']}",
                tooltip=f'Парковка {park["id"]}'
            ).add_to(parking_group)

            folium.CircleMarker(
                location=[park['center_lat'], park['center_lon']],
                radius=6,
                color='black',
                fill=True,
                fill_color='gray',
                fill_opacity=0.8,
                popup=f"Центр парковки {park['id']}"
            ).add_to(parking_group)
        parking_group.add_to(m)

    # Контроль слоев
    folium.LayerControl(collapsed=False).add_to(m)

    return m


# Создание вкладок
tab1, tab2, tab3, tab4 = st.tabs([
    "🗺️ Карта покрытия",
    "📊 Статистика покрытия",
    "📋 Детальная информация",
    "🏪 Объекты инфраструктуры"
])

with tab1:
    st.markdown("### Интерактивная карта транспортной доступности")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Остановок на карте", len(filtered_stops))
    with col2:
        st.metric("Радиус буфера", f"{buffer_radius} м")
    with col3:
        total_objects = len(df_objects[df_objects['category'].isin(categories)]) if categories else 0
        st.metric("Объектов для анализа", total_objects)

    if filtered_stops.empty:
        st.warning(f"Нет остановок типа: {selected_type}")
    else:
        try:
            m = create_full_map(
                filtered_stops, parks, df_objects,
                buffer_radius, show_objects, show_parks, categories
            )
            st_folium(m, width=1400, height=700)
        except Exception as e:
            st.error(f"Ошибка создания карты: {e}")

with tab2:
    st.markdown("### 📊 Анализ покрытия остановок")

    if not df_coverage.empty and categories:
        # График покрытия
        st.subheader(f"Количество объектов в радиусе {buffer_radius} м")

        # Создаем график
        if len(categories) > 0:
            fig = go.Figure()

            colors = {'🍽️ Заведения питания': '#ff6b6b',
                      '🏪 Магазины': '#4ecdc4',
                      '🎭 Развлечения и культура': '#ffd93d'}

            for cat in categories:
                if cat in df_coverage.columns:
                    fig.add_trace(go.Bar(
                        name=cat,
                        x=df_coverage['name'],
                        y=df_coverage[cat],
                        marker_color=colors.get(cat, '#000'),
                        text=df_coverage[cat].astype(int),
                        textposition='outside',
                    ))

            fig.update_layout(
                title=f"Покрытие остановок объектами инфраструктуры (радиус {buffer_radius} м)",
                xaxis_title="Остановки",
                yaxis_title="Количество объектов",
                barmode='group',
                height=500,
                xaxis_tickangle=-45,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                )
            )

            st.plotly_chart(fig, use_container_width=True)

        # Сводная статистика
        st.subheader("Сводная статистика")

        col1, col2, col3 = st.columns(3)

        with col1:
            total_covered = df_coverage[categories].sum().sum() if categories else 0
            st.metric(
                label="Всего объектов в зоне покрытия",
                value=f"{int(total_covered):,}"
            )

        with col2:
            df_coverage['total'] = df_coverage[categories].sum(axis=1)
            avg_coverage = df_coverage['total'].mean() if not df_coverage.empty else 0
            st.metric(
                label="Среднее покрытие на остановку",
                value=f"{avg_coverage:.1f}"
            )

        with col3:
            if not df_coverage.empty and len(df_coverage) > 0:
                best_stop = df_coverage.loc[df_coverage['total'].idxmax()]
                st.metric(
                    label="Лучшая остановка по покрытию",
                    value=best_stop['name'],
                    delta=f"{int(best_stop['total'])} объектов"
                )

        # Топ-5 остановок по покрытию
        st.subheader("Топ-5 остановок по общему покрытию")
        top5 = df_coverage.nlargest(5, 'total')[['name', 'type', 'total'] + categories]
        top5_display = top5.copy()
        top5_display.columns = ['Название', 'Тип', 'Всего'] + [
            cat.replace('🍽️ ', '').replace('🏪 ', '').replace('🎭 ', '') for cat in categories]
        st.dataframe(top5_display, use_container_width=True, hide_index=True)

        # Распределение по типам остановок
        if len(df_coverage['type'].unique()) > 1:
            st.subheader("Среднее покрытие по типам остановок")
            type_stats = df_coverage.groupby('type')[categories].mean().round(1)
            st.dataframe(type_stats, use_container_width=True)

    else:
        st.info("Выберите категории для анализа покрытия")

with tab3:
    st.markdown("### 📋 Детальная информация по остановкам")

    if not filtered_stops.empty:
        # Создаем таблицу с данными
        display_df = filtered_stops[['clean_name', 'type', 'lat', 'lon', 'url']].copy()
        display_df.columns = ['Название', 'Тип', 'Широта', 'Долгота', 'Ссылка']

        # Добавляем статистику покрытия
        if not df_coverage.empty and categories:
            for cat in categories:
                if cat in df_coverage.columns:
                    short_name = cat.replace('🍽️ ', '').replace('🏪 ', '').replace('🎭 ', '')
                    display_df[short_name] = df_coverage[cat].astype(int)

        display_df = display_df.sort_values(['Тип', 'Название'])

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Широта": st.column_config.NumberColumn(format="%.4f"),
                "Долгота": st.column_config.NumberColumn(format="%.4f"),
                "Ссылка": st.column_config.LinkColumn(display_text="Карта")
            }
        )

        # Экспорт данных
        csv = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Скачать данные в CSV",
            data=csv,
            file_name=f"baikal_stops_coverage_{buffer_radius}m.csv",
            mime="text/csv"
        )
    else:
        st.warning("Нет данных для отображения")

with tab4:
    st.markdown("### 🏪 Объекты инфраструктуры")

    # Фильтры для объектов
    obj_category = st.selectbox(
        "Категория объектов:",
        ["Все", "🍽️ Заведения питания", "🏪 Магазины", "🎭 Развлечения и культура"]
    )

    if obj_category != "Все":
        filtered_objects = df_objects[df_objects['category'] == obj_category]
    else:
        filtered_objects = df_objects

    # Поиск по названию
    search = st.text_input("Поиск по названию:")
    if search:
        filtered_objects = filtered_objects[filtered_objects['name'].str.contains(search, case=False)]

    st.write(f"Найдено объектов: {len(filtered_objects)}")

    # Отображение таблицы объектов
    display_objects = filtered_objects[
        ['name', 'category', 'subcategory', 'rating', 'ratings_count', 'lat', 'lon']].copy()
    display_objects.columns = ['Название', 'Категория', 'Подкатегория', 'Рейтинг', 'Отзывов', 'Широта', 'Долгота']
    display_objects = display_objects.sort_values(['Категория', 'Рейтинг'], ascending=[True, False])

    st.dataframe(
        display_objects,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Рейтинг": st.column_config.NumberColumn(format="%.1f"),
            "Широта": st.column_config.NumberColumn(format="%.4f"),
            "Долгота": st.column_config.NumberColumn(format="%.4f")
        }
    )

    # Статистика по рейтингам
    if not filtered_objects.empty and filtered_objects['rating'].notna().any():
        st.subheader("Распределение рейтингов")
        fig = px.histogram(
            filtered_objects.dropna(subset=['rating']),
            x='rating',
            nbins=20,
            title="Распределение рейтингов объектов",
            color='category',
            color_discrete_map={
                '🍽️ Заведения питания': '#ff6b6b',
                '🏪 Магазины': '#4ecdc4',
                '🎭 Развлечения и культура': '#ffd93d'
            }
        )
        st.plotly_chart(fig, use_container_width=True)

# Нижняя информационная панель
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown("#### 🚌 Остановки")
    st.markdown(f"**Всего:** {len(stops_grouped)}")
    for stop_type in stops_grouped['type'].unique():
        count = len(stops_grouped[stops_grouped['type'] == stop_type])
        st.markdown(f"- {stop_type}: {count}")

with col2:
    st.markdown("#### 🍽️ Питание")
    food_count = len(data['food'].get('businesses', []))
    st.markdown(f"**Всего:** {food_count}")
    if food_count > 0:
        categories_food = pd.DataFrame(data['food']['businesses'])['category'].value_counts()
        for cat, count in categories_food.items():
            st.markdown(f"- {cat}: {count}")

with col3:
    st.markdown("#### 🏪 Магазины")
    stores_count = len(data['stores'].get('stores', []))
    st.markdown(f"**Всего:** {stores_count}")
    if stores_count > 0:
        categories_stores = pd.DataFrame(data['stores']['stores'])['category'].value_counts()
        for cat, count in categories_stores.items():
            st.markdown(f"- {cat}: {count}")

with col4:
    st.markdown("#### 🎭 Культура")
    ent_count = len(data['entertainment'].get('businesses', []))
    st.markdown(f"**Всего:** {ent_count}")
    if ent_count > 0:
        categories_ent = pd.DataFrame(data['entertainment']['businesses'])['category_type'].value_counts()
        for cat, count in categories_ent.items():
            st.markdown(f"- {cat}: {count}")

# Футер
st.markdown("---")
st.caption("Данные собраны из Яндекс.Карт и OpenStreetMap | Актуальность: май 2026")
st.caption("Разработано для анализа транспортной доступности города Байкальск")