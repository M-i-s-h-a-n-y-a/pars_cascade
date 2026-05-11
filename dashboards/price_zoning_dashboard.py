import streamlit as st
import pandas as pd
import numpy as np
from scipy.spatial import cKDTree
import folium
from folium.plugins import HeatMap, Fullscreen, MarkerCluster
from streamlit_folium import st_folium
import branca.colormap as cm
import warnings

warnings.filterwarnings('ignore')

# ============================================================
# НАСТРОЙКА СТРАНИЦЫ
# ============================================================
st.set_page_config(page_title="Ценовое зонирование Байкальск", layout="wide")
st.title("🏘️ Дашборд «Ценовое зонирование»")
st.markdown("### Интерактивная карта цен на недвижимость в Байкальске и окрестностях")

# ============================================================
# РАСШИРЕННЫЕ ГРАНИЦЫ (Байкальск + Байкальская ТЭЦ + окрестности)
# ============================================================
BAIKALSK_BOUNDS = {
    'south': 51.470,
    'north': 51.555,
    'west': 104.045,
    'east': 104.230
}

BAIKALSK_POLYGON_COORDS = [
    [51.470, 104.045],
    [51.470, 104.230],
    [51.555, 104.230],
    [51.555, 104.045],
]


# ============================================================
# ЗАГРУЗКА ДАННЫХ
# ============================================================
@st.cache_data
def load_data():
    commercial = pd.read_csv('./data/avito_commercial.csv')
    properties = pd.read_csv('./data/avito_properties.csv')
    return commercial, properties


commercial, properties = load_data()


# ============================================================
# ФИЛЬТРАЦИЯ ТОЧЕК ПО РАСШИРЕННЫМ ГРАНИЦАМ
# ============================================================
def is_in_area(lat, lon):
    if pd.isna(lat) or pd.isna(lon):
        return False
    return (BAIKALSK_BOUNDS['south'] <= lat <= BAIKALSK_BOUNDS['north'] and
            BAIKALSK_BOUNDS['west'] <= lon <= BAIKALSK_BOUNDS['east'])


# ============================================================
# БОКОВАЯ ПАНЕЛЬ
# ============================================================
st.sidebar.header("🎛️ Настройки")

st.sidebar.subheader("📊 Категории недвижимости")
show_apartments = st.sidebar.checkbox("🏢 Аренда квартир (посуточно)", value=True)
show_houses = st.sidebar.checkbox("🏠 Аренда домов (посуточно)", value=True)
show_commercial_rent = st.sidebar.checkbox("🏪 Аренда коммерческой недвижимости", value=True)
show_commercial_sale = st.sidebar.checkbox("💰 Продажа коммерческой недвижимости", value=True)

st.sidebar.subheader("🗺️ Слои карты")
show_heatmap = st.sidebar.checkbox("Тепловая карта цен (HeatMap)", value=True)
show_markers = st.sidebar.checkbox("Маркеры объектов", value=True)
show_clusters = st.sidebar.checkbox("Кластеризация маркеров", value=True,
                                    help="Группирует близкие маркеры в кружки с числом")
show_area_boundary = st.sidebar.checkbox("Граница области анализа", value=True)

st.sidebar.subheader("📐 Параметры")
normalize_by_area = st.sidebar.checkbox("Показывать цену за м² (вместо общей)", value=True,
                                        help="Если выключено — показывает полную стоимость")

heat_radius = st.sidebar.slider("Радиус тепловой карты", 15, 50, 25)
heat_blur = st.sidebar.slider("Размытие тепловой карты", 5, 30, 15)

# ============================================================
# СБОР ДАННЫХ
# ============================================================
collection = []

# 1. Аренда квартир (посуточно)
if show_apartments:
    df = properties[properties['type'] == 'apartment'].copy()
    for _, row in df.iterrows():
        if not is_in_area(row['latitude'], row['longitude']):
            continue

        area = None
        title = str(row.get('title', ''))
        if 'м²' in title:
            try:
                area_str = title.split('м²')[0].split(',')[-1].strip().replace(' ', '')
                area = float(area_str)
            except:
                pass

        guests_count = row.get('guests', 0) if pd.notna(row.get('guests')) else 0

        if normalize_by_area and area and area > 0:
            price_val = row['price'] / area
        else:
            price_val = row['price']

        collection.append({
            'lat': row['latitude'],
            'lon': row['longitude'],
            'price': price_val,
            'full_price': row['price'],
            'category': 'Аренда квартир',
            'title': title,
            'url': row.get('url', ''),
            'area': area,
            'guests': guests_count,
            'address': row.get('address', 'Не указан')
        })

# 2. Аренда домов (посуточно)
if show_houses:
    df = properties[properties['type'] == 'house'].copy()
    for _, row in df.iterrows():
        if not is_in_area(row['latitude'], row['longitude']):
            continue

        area = None
        title = str(row.get('title', ''))
        if 'м²' in title:
            try:
                area_str = title.split('м²')[0].split(',')[-1].strip().replace(' ', '')
                area = float(area_str)
            except:
                pass

        guests_count = row.get('guests', 0) if pd.notna(row.get('guests')) else 0

        if normalize_by_area and area and area > 0:
            price_val = row['price'] / area
        else:
            price_val = row['price']

        collection.append({
            'lat': row['latitude'],
            'lon': row['longitude'],
            'price': price_val,
            'full_price': row['price'],
            'category': 'Аренда домов',
            'title': title,
            'url': row.get('url', ''),
            'area': area,
            'guests': guests_count,
            'address': row.get('address', 'Не указан')
        })

# 3. Аренда коммерческой недвижимости
if show_commercial_rent:
    df = commercial[commercial['purpose'] == 'rent'].copy()
    for _, row in df.iterrows():
        if not is_in_area(row['latitude'], row['longitude']):
            continue

        area = row.get('area', None)
        commercial_type = row.get('commercial_type', 'other')

        if normalize_by_area and pd.notna(area) and area > 0:
            price_val = row['price'] / area
        else:
            price_val = row['price']

        collection.append({
            'lat': row['latitude'],
            'lon': row['longitude'],
            'price': price_val,
            'full_price': row['price'],
            'category': 'Аренда коммерческой',
            'title': row['title'],
            'url': row.get('url', ''),
            'area': area if pd.notna(area) else None,
            'guests': 0,
            'address': row.get('address', 'Не указан'),
            'commercial_type': commercial_type
        })

# 4. Продажа коммерческой недвижимости
if show_commercial_sale:
    df = commercial[commercial['purpose'] == 'sale'].copy()
    for _, row in df.iterrows():
        if not is_in_area(row['latitude'], row['longitude']):
            continue

        area = row.get('area', None)
        commercial_type = row.get('commercial_type', 'other')

        if normalize_by_area and pd.notna(area) and area > 0:
            price_val = row['price'] / area
        else:
            price_val = row['price']

        collection.append({
            'lat': row['latitude'],
            'lon': row['longitude'],
            'price': price_val,
            'full_price': row['price'],
            'category': 'Продажа коммерческой',
            'title': row['title'],
            'url': row.get('url', ''),
            'area': area if pd.notna(area) else None,
            'guests': 0,
            'address': row.get('address', 'Не указан'),
            'commercial_type': commercial_type
        })

if not collection:
    st.warning("⚠️ Нет данных для отображения. Выберите хотя бы одну категорию.")
    st.stop()

df_points = pd.DataFrame(collection)

# Удаление выбросов
if len(df_points) > 10:
    q99 = df_points['price'].quantile(0.99)
    q01 = df_points['price'].quantile(0.01)
    df_points_before = len(df_points)
    df_points = df_points[(df_points['price'] >= q01) & (df_points['price'] <= q99)]
    removed = df_points_before - len(df_points)
    if removed > 0:
        st.sidebar.caption(f"Удалено {removed} выбросов")

# ============================================================
# СТАТИСТИКА В САЙДБАРЕ
# ============================================================
st.sidebar.markdown("---")
st.sidebar.subheader("📈 Статистика")

total = len(df_points)
st.sidebar.metric("Всего объектов", total)

for cat in df_points['category'].unique():
    count = len(df_points[df_points['category'] == cat])
    emoji = "🏢" if "квартир" in cat.lower() else "🏠" if "дом" in cat.lower() else "🏪" if "аренда" in cat.lower() else "💰"
    st.sidebar.metric(f"{emoji} {cat}", count)

avg_price = df_points['price'].mean()
median_price = df_points['price'].median()

if normalize_by_area:
    st.sidebar.metric("Средняя цена за м²", f"{avg_price:,.0f} ₽")
    st.sidebar.metric("Медианная цена за м²", f"{median_price:,.0f} ₽")
else:
    st.sidebar.metric("Средняя цена", f"{avg_price:,.0f} ₽")
    st.sidebar.metric("Медианная цена", f"{median_price:,.0f} ₽")

# ============================================================
# ЦВЕТОВАЯ ШКАЛА
# ============================================================
price_min = df_points['price'].min()
price_max = df_points['price'].max()

colormap = cm.LinearColormap(
    colors=['#1a5276', '#2e86c1', '#28b463', '#f1c40f', '#e67e22', '#cb4335'],
    vmin=price_min,
    vmax=price_max,
    caption='Цена за м² (₽)' if normalize_by_area else 'Цена (₽)'
)

# ============================================================
# ИКОНКИ И ЦВЕТА ДЛЯ КАТЕГОРИЙ
# ============================================================
category_icons = {
    'Аренда квартир': 'building',
    'Аренда домов': 'home',
    'Аренда коммерческой': 'briefcase',
    'Продажа коммерческой': 'usd'
}

category_colors = {
    'Аренда квартир': 'blue',
    'Аренда домов': 'purple',
    'Аренда коммерческой': 'orange',
    'Продажа коммерческой': 'red'
}

# ============================================================
# СОЗДАНИЕ КАРТЫ (ТОЛЬКО НЕЙТРАЛЬНЫЕ ТАЙЛЫ, БЕЗ ФЛАГОВ!)
# ============================================================
center_lat = (BAIKALSK_BOUNDS['south'] + BAIKALSK_BOUNDS['north']) / 2
center_lon = (BAIKALSK_BOUNDS['west'] + BAIKALSK_BOUNDS['east']) / 2

# Карта ТОЛЬКО с CartoDB (гарантированно без политической окраски)
m = folium.Map(
    location=[center_lat, center_lon],
    zoom_start=13,
    tiles='CartoDB positron',
    control_scale=True,
    prefer_canvas=True
)

# Единственный дополнительный слой - спутник (точно без флагов)
folium.TileLayer(
    tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    name='🛰️ Спутник (ESRI)',
    attr='Esri, Maxar, Earthstar Geographics'
).add_to(m)

# Полноэкранный режим
Fullscreen().add_to(m)

# ============================================================
# СЛОЙ 1: ТЕПЛОВАЯ КАРТА
# ============================================================
if show_heatmap:
    heat_data_all = []
    for _, row in df_points.iterrows():
        weight = np.log1p(row['price']) / np.log1p(price_max)
        heat_data_all.append([row['lat'], row['lon'], weight * 10])

    if heat_data_all:
        heatmap_all = folium.FeatureGroup(name='🔥 Общая тепловая карта')
        HeatMap(
            heat_data_all,
            radius=heat_radius,
            blur=heat_blur,
            max_zoom=18,
            gradient={0.2: 'blue', 0.4: 'cyan', 0.6: 'lime', 0.8: 'yellow', 1.0: 'red'},
        ).add_to(heatmap_all)
        heatmap_all.add_to(m)

    for category in df_points['category'].unique():
        cat_data = df_points[df_points['category'] == category]
        heat_data = []
        for _, row in cat_data.iterrows():
            weight = np.log1p(row['price']) / np.log1p(price_max)
            heat_data.append([row['lat'], row['lon'], weight * 10])

        if heat_data:
            cat_heatmap = folium.FeatureGroup(name=f'🔥 Тепловая карта: {category}')
            HeatMap(
                heat_data,
                radius=heat_radius,
                blur=heat_blur,
                max_zoom=18,
                gradient={0.2: 'blue', 0.4: 'cyan', 0.6: 'lime', 0.8: 'yellow', 1.0: 'red'},
            ).add_to(cat_heatmap)
            cat_heatmap.add_to(m)

# ============================================================
# СЛОЙ 2: МАРКЕРЫ ОБЪЕКТОВ
# ============================================================
if show_markers:
    for category in df_points['category'].unique():
        cat_data = df_points[df_points['category'] == category]

        if show_clusters:
            cluster = MarkerCluster(name=f'📍 {category} (кластеры)', disableClusteringAtZoom=16)

            for _, row in cat_data.iterrows():
                if normalize_by_area:
                    price_display = f"{row['price']:,.0f} ₽/м²"
                    full_price_display = f"{row['full_price']:,.0f} ₽"
                else:
                    price_display = f"{row['price']:,.0f} ₽"
                    full_price_display = None

                popup_html = f"""
                <div style="font-family: Arial; min-width: 280px;">
                    <h4 style="margin-top: 0;">{row['title']}</h4>
                    <hr>
                    <b>📍 Адрес:</b> {row['address']}<br>
                    <b>🏷️ Категория:</b> {category}<br>
                    <b>💰 Цена:</b> {price_display}<br>
                """

                if normalize_by_area and full_price_display:
                    popup_html += f"<b>💰 Полная цена:</b> {full_price_display}<br>"

                if row.get('area') and pd.notna(row['area']):
                    popup_html += f"<b>📐 Площадь:</b> {row['area']:,.0f} м²<br>"

                if row.get('guests') and row['guests'] > 0:
                    popup_html += f"<b>👥 Гостей:</b> до {int(row['guests'])} чел.<br>"

                if row.get('commercial_type'):
                    popup_html += f"<b>🏪 Тип:</b> {row['commercial_type']}<br>"

                if row.get('url'):
                    popup_html += f'<br><a href="{row["url"]}" target="_blank">🔗 Смотреть на Avito</a>'

                popup_html += "</div>"

                icon_name = category_icons.get(category, 'info-sign')
                icon_color = category_colors.get(category, 'blue')

                folium.Marker(
                    location=[row['lat'], row['lon']],
                    popup=folium.Popup(popup_html, max_width=350),
                    tooltip=f"{category}: {price_display}",
                    icon=folium.Icon(color=icon_color, icon=icon_name, prefix='fa')
                ).add_to(cluster)

            cluster.add_to(m)

        else:
            group_layer = folium.FeatureGroup(name=f'📍 {category}')

            for _, row in cat_data.iterrows():
                if normalize_by_area:
                    price_display = f"{row['price']:,.0f} ₽/м²"
                    full_price_display = f"{row['full_price']:,.0f} ₽"
                else:
                    price_display = f"{row['price']:,.0f} ₽"
                    full_price_display = None

                popup_html = f"""
                <div style="font-family: Arial; min-width: 280px;">
                    <h4 style="margin-top: 0;">{row['title']}</h4>
                    <hr>
                    <b>📍 Адрес:</b> {row['address']}<br>
                    <b>🏷️ Категория:</b> {category}<br>
                    <b>💰 Цена:</b> {price_display}<br>
                """

                if normalize_by_area and full_price_display:
                    popup_html += f"<b>💰 Полная цена:</b> {full_price_display}<br>"

                if row.get('area') and pd.notna(row['area']):
                    popup_html += f"<b>📐 Площадь:</b> {row['area']:,.0f} м²<br>"

                if row.get('guests') and row['guests'] > 0:
                    popup_html += f"<b>👥 Гостей:</b> до {int(row['guests'])} чел.<br>"

                if row.get('commercial_type'):
                    popup_html += f"<b>🏪 Тип:</b> {row['commercial_type']}<br>"

                if row.get('url'):
                    popup_html += f'<br><a href="{row["url"]}" target="_blank">🔗 Смотреть на Avito</a>'

                popup_html += "</div>"

                icon_name = category_icons.get(category, 'info-sign')
                icon_color = category_colors.get(category, 'blue')

                folium.Marker(
                    location=[row['lat'], row['lon']],
                    popup=folium.Popup(popup_html, max_width=350),
                    tooltip=f"{category}: {price_display}",
                    icon=folium.Icon(color=icon_color, icon=icon_name, prefix='fa')
                ).add_to(group_layer)

            group_layer.add_to(m)

# ============================================================
# СЛОЙ 3: ГРАНИЦА ОБЛАСТИ АНАЛИЗА
# ============================================================
if show_area_boundary:
    folium.Polygon(
        locations=BAIKALSK_POLYGON_COORDS,
        color='black',
        weight=2,
        fill=True,
        fill_color='gray',
        fill_opacity=0.05,
        dash_array='5, 5',
        popup='Граница области анализа\n(Байкальск + ТЭЦ + окрестности)',
        name='📍 Граница области'
    ).add_to(m)

# Маркер Байкальской ТЭЦ
folium.Marker(
    location=[51.475, 104.620],
    popup='<b>🏭 Байкальская ТЭЦ</b>',
    tooltip='Байкальская ТЭЦ',
    icon=folium.Icon(color='cadetblue', icon='industry', prefix='fa')
).add_to(m)

# ============================================================
# ЛЕГЕНДА И УПРАВЛЕНИЕ СЛОЯМИ
# ============================================================
m.add_child(colormap)
folium.LayerControl(collapsed=False).add_to(m)

# ============================================================
# ОТОБРАЖЕНИЕ КАРТЫ
# ============================================================
st_folium(m, width=1300, height=700)

# ============================================================
# СТАТИСТИКА ПОД КАРТОЙ
# ============================================================
st.markdown("---")
st.subheader("📊 Детальная статистика по категориям")

stats_cols = st.columns(len(df_points['category'].unique()))

for i, category in enumerate(df_points['category'].unique()):
    cat_data = df_points[df_points['category'] == category]

    with stats_cols[i]:
        emoji = "🏢" if "квартир" in category.lower() else "🏠" if "дом" in category.lower() else "🏪" if "аренда" in category.lower() else "💰"
        st.markdown(f"### {emoji} {category}")

        if len(cat_data) > 0:
            st.metric("Количество", len(cat_data))

            if normalize_by_area:
                st.metric("Мин. цена за м²", f"{cat_data['price'].min():,.0f} ₽")
                st.metric("Сред. цена за м²", f"{cat_data['price'].mean():,.0f} ₽")
                st.metric("Макс. цена за м²", f"{cat_data['price'].max():,.0f} ₽")
                st.metric("Медиана за м²", f"{cat_data['price'].median():,.0f} ₽")
            else:
                st.metric("Мин. цена", f"{cat_data['price'].min():,.0f} ₽")
                st.metric("Сред. цена", f"{cat_data['price'].mean():,.0f} ₽")
                st.metric("Макс. цена", f"{cat_data['price'].max():,.0f} ₽")
                st.metric("Медиана", f"{cat_data['price'].median():,.0f} ₽")

            if 'area' in cat_data.columns:
                avg_area = cat_data['area'].dropna().mean()
                if avg_area > 0:
                    st.metric("Сред. площадь", f"{avg_area:.0f} м²")
        else:
            st.info("Нет данных")

# ============================================================
# ТОП ОБЪЕКТОВ
# ============================================================
st.subheader("🔝 Топ-5 самых дорогих и доступных объектов")

col1, col2 = st.columns(2)

with col1:
    st.markdown("### 🔴 Самые дорогие")
    top_expensive = df_points.nlargest(5, 'price')
    for _, row in top_expensive.iterrows():
        if normalize_by_area:
            st.write(f"• **{row['title'][:80]}** — {row['price']:,.0f} ₽/м² ({row['category']})")
        else:
            st.write(f"• **{row['title'][:80]}** — {row['price']:,.0f} ₽ ({row['category']})")

with col2:
    st.markdown("### 🔵 Самые доступные")
    top_cheap = df_points.nsmallest(5, 'price')
    for _, row in top_cheap.iterrows():
        if normalize_by_area:
            st.write(f"• **{row['title'][:80]}** — {row['price']:,.0f} ₽/м² ({row['category']})")
        else:
            st.write(f"• **{row['title'][:80]}** — {row['price']:,.0f} ₽ ({row['category']})")

# ============================================================
# ТАБЛИЦА ВСЕХ ДАННЫХ (ИСПРАВЛЕНО)
# ============================================================
st.markdown("---")
st.subheader("📋 Все объекты")

# Формируем колонки для отображения
display_data = []
for _, row in df_points.iterrows():
    item = {
        'Категория': row['category'],
        'Название': row['title'][:100],
        'Адрес': row.get('address', 'Не указан'),
        'Площадь': f"{row['area']:.0f} м²" if pd.notna(row.get('area')) else "—"
    }

    if normalize_by_area:
        item['Цена за м²'] = f"{row['price']:,.0f} ₽"
        item['Общая цена'] = f"{row['full_price']:,.0f} ₽"
    else:
        item['Цена'] = f"{row['price']:,.0f} ₽"

    if row.get('guests') and row['guests'] > 0:
        item['Гостей'] = int(row['guests'])

    display_data.append(item)

display_df = pd.DataFrame(display_data)

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True
)

# ============================================================
# ЭКСПОРТ
# ============================================================
st.markdown("---")
csv = df_points.to_csv(index=False).encode('utf-8')
st.download_button(
    label="📥 Скачать данные (CSV)",
    data=csv,
    file_name='baikalsk_real_estate_prices.csv',
    mime='text/csv',
    use_container_width=True
)

st.caption("💡 *Данные собраны с Avito. Цены указаны за посуточную аренду (жильё) или за месяц (коммерческая аренда).*")