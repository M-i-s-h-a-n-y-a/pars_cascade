import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from datetime import datetime, timedelta
import os
import glob

# --- Попытка импорта для GRIB файлов ---
try:
    import xarray as xr
    import cfgrib

    GRIB_AVAILABLE = True
except ImportError:
    GRIB_AVAILABLE = False

# --- Конфигурация страницы Streamlit ---
st.set_page_config(page_title="🏔️ Погода для Байкальска", page_icon="⛷️", layout="wide")

# --- Константы и настройки ---
CITY_NAME = "Байкальск"
GEO_LOCATOR_URL = "https://geocoding-api.open-meteo.com/v1/search"
HISTORICAL_WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
SNOWINESS_FILE = 'snowiness_era5.csv'
GRIB_FILE = 'snowiness.grib'


# --- Вспомогательные функции ---
@st.cache_data(ttl=3600)
def get_city_coordinates(city_name):
    params = {"name": city_name, "count": 1, "language": "ru"}
    try:
        response = requests.get(GEO_LOCATOR_URL, params=params, timeout=10)
        if response.status_code == 200 and response.json().get('results'):
            location = response.json()['results'][0]
            return location['latitude'], location['longitude']
        else:
            st.error(f"Город '{city_name}' не найден.")
            return None, None
    except Exception as e:
        st.error(f"Ошибка геокодирования: {e}")
        return None, None


@st.cache_data(ttl=3600)
def fetch_historical_weather(lat, lon, start_date, end_date):
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.strftime('%Y-%m-%d'),
        "end_date": end_date.strftime('%Y-%m-%d'),
        "timezone": "Asia/Irkutsk",
        "daily": [
            "temperature_2m_mean", "temperature_2m_min", "temperature_2m_max",
            "precipitation_sum", "snowfall_sum", "sunshine_duration",
            "wind_speed_10m_max", "wind_gusts_10m_max"
        ]
    }
    try:
        response = requests.get(HISTORICAL_WEATHER_URL, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            df = pd.DataFrame(data['daily'])
            df['time'] = pd.to_datetime(df['time'])
            df['month'] = df['time'].dt.month
            df['year'] = df['time'].dt.year
            return df
        else:
            st.error(f"Ошибка API погоды: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Ошибка запроса погоды: {e}")
        return None


@st.cache_data(ttl=3600)
def find_and_load_snowiness_data():
    """Ищет файл снежности в текущей папке и в подпапках."""
    possible_paths = [
        SNOWINESS_FILE,
        os.path.join('data', SNOWINESS_FILE),
    ]

    additional_paths = glob.glob(f'**/{SNOWINESS_FILE}', recursive=True)
    possible_paths.extend(additional_paths)

    for path in set(possible_paths):
        if os.path.exists(path):
            st.success(f"✅ Файл найден: {path}")
            try:
                df = pd.read_csv(path)
                df.rename(columns={'LON_LAT_025': 'coords', 'YEAR': 'year'}, inplace=True)
                return df, path
            except Exception as e:
                st.error(f"Ошибка чтения файла: {e}")
                return None, None

    return None, None


def get_month_names():
    return {1: "Янв", 2: "Фев", 3: "Мар", 4: "Апр", 5: "Май", 6: "Июн",
            7: "Июл", 8: "Авг", 9: "Сен", 10: "Окт", 11: "Ноя", 12: "Дек"}


def create_monthly_summary(df):
    """Создает сводку по месяцам на основе исторических данных."""
    month_names = get_month_names()
    monthly = df.groupby('month').agg(
        temp_mean=('temperature_2m_mean', 'mean'),
        temp_min=('temperature_2m_min', 'min'),
        temp_max=('temperature_2m_max', 'max'),
        total_precip=('precipitation_sum', 'sum'),
        total_snow=('snowfall_sum', 'sum'),
        avg_wind=('wind_speed_10m_max', 'mean'),
        max_wind_gust=('wind_gusts_10m_max', 'max'),
        total_sun=('sunshine_duration', 'sum'),
        days_count=('time', 'count')
    ).reset_index()
    monthly['month_name'] = monthly['month'].map(month_names)
    return monthly.sort_values('month')


def calculate_snow_accumulation(df, snow_melt_rate):
    """
    Рассчитывает накопленный снежный покров с учетом таяния.

    Параметры:
    - df: DataFrame с погодными данными
    - snow_melt_rate: скорость таяния снега (см/°C/день)
    """
    df = df.copy()
    df = df.sort_values('time')

    snow_depth = 0
    snow_depths = []

    for idx, row in df.iterrows():
        # Добавляем новый снег
        snow_depth += row['snowfall_sum']

        # Таяние при положительной температуре
        if row['temperature_2m_mean'] > 0:
            melt = snow_melt_rate * row['temperature_2m_mean']
            snow_depth -= melt

        # Снег не может быть отрицательным
        snow_depth = max(0, snow_depth)
        snow_depths.append(snow_depth)

    df['snow_depth'] = snow_depths
    return df


def find_ski_seasons(df, snow_threshold, snow_melt_rate):
    """Находит горнолыжные сезоны на основе накопленного снега (осень-весна)."""
    df = calculate_snow_accumulation(df, snow_melt_rate)
    df = df.sort_values('time').copy()

    # Находим все периоды, когда снега достаточно
    season_mask = df['snow_depth'] >= snow_threshold

    if not season_mask.any():
        return [], df

    # Группируем последовательные дни в сезоны
    df['season_group'] = (season_mask.astype(int).diff() != 0).cumsum()
    season_periods = df[season_mask].groupby('season_group')

    seasons = []
    for group_id, period in season_periods:
        start = period['time'].min()
        end = period['time'].max()
        days = len(period)
        max_snow = period['snow_depth'].max()

        # Определяем горнолыжный год (год начала сезона)
        # Если сезон начинается в июле-декабре → это начало горнолыжного года
        # Если в январе-июне → относим к предыдущему горнолыжному году
        if start.month >= 7:
            ski_year = start.year
        else:
            ski_year = start.year - 1

        # Ищем реальное начало накопления снега (до открытия сезона)
        pre_season = df[df['time'] < start].copy()

        accumulation_start = None
        if not pre_season.empty:
            # Идем от конца к началу, ищем последний день с малым количеством снега
            pre_season_sorted = pre_season.sort_values('time', ascending=False)
            for idx, row in pre_season_sorted.iterrows():
                if row['snow_depth'] <= 1:
                    next_day = pre_season[pre_season['time'] > row['time']]
                    if not next_day.empty:
                        accumulation_start = next_day['time'].min()
                    break

            if accumulation_start is None:
                accumulation_start = pre_season['time'].min()

        # Ищем реальное окончание таяния снега (после закрытия сезона)
        post_season = df[df['time'] > end].copy()

        melt_end = None
        if not post_season.empty:
            post_season_sorted = post_season.sort_values('time')
            for idx, row in post_season_sorted.iterrows():
                if row['snow_depth'] <= 1:
                    melt_end = row['time']
                    break

            if melt_end is None:
                melt_end = post_season['time'].max()

        # Оставляем только значимые сезоны (минимум 10 дней)
        if days >= 10:
            seasons.append({
                'ski_year': ski_year,
                'season_start': start,
                'season_end': end,
                'days': days,
                'max_snow': max_snow,
                'accumulation_start': accumulation_start,
                'melt_end': melt_end
            })

    # Сортируем по горнолыжному году
    seasons.sort(key=lambda x: x['ski_year'])

    return seasons, df


# --- Основной интерфейс ---
def main():
    st.title(f"⛷️ Погодный анализ для горнолыжного курорта: {CITY_NAME}")

    # --- Боковая панель ---
    with st.sidebar:
        st.header("⚙️ Настройки")
        today = datetime.today()
        start_date = st.date_input("Начало периода", today - timedelta(days=5 * 365))
        end_date = st.date_input("Конец периода", today)

        st.divider()
        st.header("🎿 Параметры сезона")
        snow_threshold = st.slider(
            "Мин. снега для открытия (см)",
            min_value=10,
            max_value=50,
            value=30,
            help="Сезон открывается, когда накопленный снег достигает этого значения"
        )
        melt_rate = st.slider(
            "Скорость таяния (см/°C/день)",
            min_value=0.1,
            max_value=1.0,
            value=0.3,
            step=0.1,
            help="Сколько снега тает за день при температуре +1°C"
        )

        st.divider()
        st.header("📂 Данные")
        show_era5 = st.checkbox("ERA5 Категории снежности (CSV)", value=True)
        show_grib = st.checkbox("GRIB Файл", value=False)
        st.divider()
        st.caption(f"Данные обновлены: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # --- Загрузка данных ---
    lat, lon = get_city_coordinates(CITY_NAME)
    if lat is None:
        st.stop()

    with st.spinner('Загрузка погодных данных с Open-Meteo...'):
        df_weather = fetch_historical_weather(lat, lon, start_date, end_date)

    df_era5 = None
    if show_era5:
        with st.spinner('Поиск файла снежности...'):
            df_era5, _ = find_and_load_snowiness_data()
            if df_era5 is None:
                st.warning("Файл snowiness_era5.csv не найден. Проверьте: текущая папка, папка data, подпапки.")

    if df_weather is None or df_weather.empty:
        st.error("Не удалось загрузить данные о погоде. Попробуйте другой период.")
        st.stop()

    monthly_summary = create_monthly_summary(df_weather)

    # Используем значения из слайдеров напрямую
    ski_seasons, df_with_snow = find_ski_seasons(df_weather, snow_threshold, melt_rate)

    # --- Вкладки ---
    tab1, tab2, tab3 = st.tabs(["📊 Климатическая сводка", "⛷️ Горнолыжный сезон", "🔍 Детальные данные"])

    with tab1:
        st.subheader("Погода по месяцам (среднее за выбранный период)")

        # Карточки с ключевыми показателями
        if not monthly_summary.empty:
            coldest_month_idx = monthly_summary['temp_mean'].idxmin()
            warmest_month_idx = monthly_summary['temp_mean'].idxmax()
            snowiest_month_idx = monthly_summary['total_snow'].idxmax()
            windiest_month_idx = monthly_summary['avg_wind'].idxmax()

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("🥶 Самый холодный",
                          f"{monthly_summary.loc[coldest_month_idx, 'month_name']}",
                          f"{monthly_summary.loc[coldest_month_idx, 'temp_mean']:.1f} °C")
            with c2:
                st.metric("🏖️ Самый теплый",
                          f"{monthly_summary.loc[warmest_month_idx, 'month_name']}",
                          f"{monthly_summary.loc[warmest_month_idx, 'temp_mean']:.1f} °C")
            with c3:
                st.metric("❄️ Самый снежный",
                          f"{monthly_summary.loc[snowiest_month_idx, 'month_name']}",
                          f"{monthly_summary.loc[snowiest_month_idx, 'total_snow']:.1f} см")
            with c4:
                st.metric("🌬️ Самый ветреный",
                          f"{monthly_summary.loc[windiest_month_idx, 'month_name']}",
                          f"{monthly_summary.loc[windiest_month_idx, 'avg_wind']:.1f} м/с")

            # Графики
            col1, col2 = st.columns(2)
            with col1:
                fig_temp = px.bar(monthly_summary, x='month_name', y='temp_mean',
                                  title="Средняя температура по месяцам",
                                  labels={'temp_mean': 'Температура (°C)', 'month_name': 'Месяц'},
                                  color='temp_mean', color_continuous_scale='RdBu_r')
                st.plotly_chart(fig_temp, use_container_width=True)

                fig_snow = px.bar(monthly_summary, x='month_name', y='total_snow',
                                  title="Суммарный снегопад по месяцам",
                                  labels={'total_snow': 'Снегопад (см)', 'month_name': 'Месяц'},
                                  color='total_snow', color_continuous_scale='Blues')
                st.plotly_chart(fig_snow, use_container_width=True)

            with col2:
                fig_wind = make_subplots(specs=[[{"secondary_y": True}]])
                fig_wind.add_trace(go.Bar(x=monthly_summary['month_name'], y=monthly_summary['avg_wind'],
                                          name="Ср. ветер (м/с)", marker_color='lightgreen'), secondary_y=False)
                fig_wind.add_trace(go.Scatter(x=monthly_summary['month_name'], y=monthly_summary['max_wind_gust'],
                                              name="Макс. порыв (м/с)", marker_color='red'), secondary_y=True)
                fig_wind.update_layout(title_text="Ветровая активность по месяцам")
                st.plotly_chart(fig_wind, use_container_width=True)

                fig_sun = px.bar(monthly_summary, x='month_name', y='total_sun',
                                 title="Солнечное сияние по месяцам",
                                 labels={'total_sun': 'Общая продолжительность (ч)', 'month_name': 'Месяц'},
                                 color='total_sun', color_continuous_scale='YlOrBr')
                st.plotly_chart(fig_sun, use_container_width=True)

    with tab2:
        st.subheader("⛷️ Горнолыжный сезон с учетом накопления снега")
        st.markdown(f"""
        **Модель расчета:**
        - Порог открытия сезона: **{snow_threshold} см** накопленного снега
        - Скорость таяния: **{melt_rate} см/°C/день** при T > 0°C
        - Сезоны определяются как непрерывные периоды с достаточным снегом
        - Горнолыжный год: июль-июнь
        """)

        if ski_seasons:
            # Таблица сезонов
            st.subheader("📅 Даты сезонов")
            seasons_df = pd.DataFrame(ski_seasons)
            seasons_df['season_start'] = seasons_df['season_start'].dt.strftime('%d.%m.%Y')
            seasons_df['season_end'] = seasons_df['season_end'].dt.strftime('%d.%m.%Y')
            seasons_df['accumulation_start'] = seasons_df['accumulation_start'].apply(
                lambda x: x.strftime('%d.%m.%Y') if pd.notna(x) else '-'
            )
            seasons_df['melt_end'] = seasons_df['melt_end'].apply(
                lambda x: x.strftime('%d.%m.%Y') if pd.notna(x) else '-'
            )

            # Форматируем для отображения
            display_df = seasons_df.rename(columns={
                'ski_year': 'Горнолыжный год',
                'accumulation_start': 'Начало снегопадов',
                'season_start': 'Открытие сезона',
                'season_end': 'Закрытие сезона',
                'melt_end': 'Полное таяние',
                'days': 'Дней катания',
                'max_snow': 'Макс. снег (см)'
            })

            # Переупорядочиваем колонки
            display_df = display_df[[
                'Горнолыжный год', 'Начало снегопадов', 'Открытие сезона',
                'Закрытие сезона', 'Полное таяние', 'Дней катания', 'Макс. снег (см)'
            ]]

            st.dataframe(display_df, hide_index=True, use_container_width=True)

            # Средние показатели
            avg_days = display_df['Дней катания'].mean()
            avg_max_snow = display_df['Макс. снег (см)'].mean()

            col1, col2 = st.columns(2)
            with col1:
                st.metric("📊 Средняя продолжительность", f"{avg_days:.0f} дней")
            with col2:
                st.metric("🏔️ Средний максимум снега", f"{avg_max_snow:.0f} см")

            # График накопленного снега с отметками сезонов
            st.subheader("📈 Динамика накопленного снега и температуры")

            fig_snow_cover = make_subplots(specs=[[{"secondary_y": True}]])

            # График снежного покрова
            fig_snow_cover.add_trace(
                go.Scatter(x=df_with_snow['time'], y=df_with_snow['snow_depth'],
                           name="Накопленный снег",
                           line=dict(color='cyan', width=3),
                           fill='tozeroy',
                           fillcolor='rgba(0, 255, 255, 0.1)'),
                secondary_y=False)

            # График температуры
            fig_snow_cover.add_trace(
                go.Scatter(x=df_with_snow['time'], y=df_with_snow['temperature_2m_mean'],
                           name="Температура (°C)",
                           line=dict(color='red', dash='dot', width=1)),
                secondary_y=True)

            # Добавляем закрашенные области для сезонов
            for season in ski_seasons:
                fig_snow_cover.add_vrect(
                    x0=season['season_start'], x1=season['season_end'],
                    fillcolor="green", opacity=0.1,
                    layer="below", line_width=0,
                    annotation_text=f"Сезон {season['ski_year']}/{season['ski_year'] + 1}",
                    annotation_position="top left"
                )

            # Горизонтальные линии порогов
            fig_snow_cover.add_hline(y=snow_threshold, line_dash="dash", line_color="green",
                                     secondary_y=False,
                                     annotation_text=f"Порог открытия ({snow_threshold} см)")
            fig_snow_cover.add_hline(y=0, line_dash="solid", line_color="gray",
                                     secondary_y=True)

            fig_snow_cover.update_layout(
                title_text="Накопленный снежный покров с учетом таяния",
                hovermode="x unified",
                height=600
            )
            fig_snow_cover.update_yaxes(title_text="Снежный покров (см)", secondary_y=False)
            fig_snow_cover.update_yaxes(title_text="Температура (°C)", secondary_y=True)

            st.plotly_chart(fig_snow_cover, use_container_width=True)
        else:
            st.info("Недостаточно данных для определения горнолыжных сезонов.")

    with tab3:
        st.subheader("Прочие данные")
        col1, col2 = st.columns(2)

        with col1:
            if df_era5 is not None:
                st.success("✅ Данные снежности загружены")
                st.dataframe(df_era5.head(10), use_container_width=True)
            else:
                st.warning("🔍 Файл snowiness_era5.csv не найден.")

        with col2:
            st.subheader("📊 Статистика по снегу")
            if 'snow_depth' in df_with_snow.columns:
                snow_stats = df_with_snow[df_with_snow['snow_depth'] > 0]
                if len(snow_stats) > 0:
                    st.metric("Дней со снегом", f"{len(snow_stats)}")
                    st.metric("Максимальный снег", f"{snow_stats['snow_depth'].max():.0f} см")
                    st.metric("Средний снег в сезон", f"{snow_stats['snow_depth'].mean():.0f} см")

        st.divider()
        st.subheader("Сырые данные (с расчетом снега, последние 30 записей)")
        if 'snow_depth' in df_with_snow.columns:
            display_cols = ['time', 'temperature_2m_mean', 'snowfall_sum', 'snow_depth', 'precipitation_sum']
            st.dataframe(df_with_snow[display_cols].tail(30), use_container_width=True)


if __name__ == "__main__":
    main()