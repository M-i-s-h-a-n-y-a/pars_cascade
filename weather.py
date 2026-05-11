import requests
import pandas as pd
from datetime import datetime
from geopy.geocoders import Nominatim
import time
import os


# --- 1. Получение координат Байкальска ---
def get_coordinates(city_name="Байкальск"):
    """
    Определяет широту и долготу города с помощью Nominatim.
    User-Agent обязателен для работы с геокодером.
    """
    geolocator = Nominatim(user_agent="baikal_weather_app")
    location = geolocator.geocode(city_name)
    if location:
        print(f"Найдены координаты для {city_name}: {location.latitude}, {location.longitude}")
        return location.latitude, location.longitude
    else:
        raise ValueError(f"Город {city_name} не найден")


# --- 2. Запрос данных за один год ---
def fetch_yearly_weather(lat, lon, year):
    """
    Запрашивает исторические данные о погоде за указанный год.
    Возвращает DataFrame с дневными данными.
    """
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "Asia/Irkutsk",
        "daily": [
            "temperature_2m_mean",  # Средняя температура
            "temperature_2m_min",  # Минимальная температура
            "temperature_2m_max",  # Максимальная температура
            "precipitation_sum",  # Сумма осадков
            "wind_speed_10m_mean",  # Средняя скорость ветра
            "wind_gusts_10m_max",  # Максимальные порывы ветра
            "pressure_msl_mean",  # Среднее атмосферное давление
            "snowfall_sum",  # Сумма снегопада
            "sunshine_duration"  # Продолжительность солнечного сияния
        ]
    }

    print(f"Запрашиваем данные за {year} год...")
    response = requests.get(url, params=params)

    if response.status_code == 200:
        data = response.json()
        df = pd.DataFrame(data['daily'])
        df['time'] = pd.to_datetime(df['time'])
        df['year'] = year
        print(f"Данные за {year} успешно получены! ({len(df)} дней)")
        return df
    else:
        print(f"Ошибка API за {year}: {response.status_code}")
        return None


# --- 3. Сбор данных за несколько лет ---
def fetch_multiyear_weather(lat, lon, years):
    """
    Собирает погодные данные за несколько лет в один DataFrame.
    """
    all_data = []

    for year in years:
        year_data = fetch_yearly_weather(lat, lon, year)
        if year_data is not None:
            all_data.append(year_data)

        # Пауза между запросами, чтобы не нагружать API
        if year != years[-1]:
            time.sleep(2)

    if all_data:
        combined_df = pd.concat(all_data, ignore_index=True)
        print(f"\nВсего собрано данных: {len(combined_df)} дней за {len(years)} лет")
        return combined_df
    else:
        raise ValueError("Не удалось получить данные ни за один год")


# --- 4. Группировка по неделям ---
def aggregate_weekly(df):
    """
    Группирует данные по неделям и годам.
    Добавляет метрики для анализа.
    """
    # Словарь для агрегации
    weekly_agg = {
        'temperature_2m_mean': 'mean',
        'temperature_2m_min': 'min',
        'temperature_2m_max': 'max',
        'precipitation_sum': 'sum',
        'wind_speed_10m_mean': 'mean',
        'wind_gusts_10m_max': 'max',
        'pressure_msl_mean': 'mean',
        'snowfall_sum': 'sum',
        'sunshine_duration': 'sum'
    }

    # Группировка по году и неделе
    # Будем группировать с начала года (W-MON)
    weekly_data = df.resample('W-MON', on='time').agg(weekly_agg)

    # Округление для читаемости
    weekly_data = weekly_data.round({
        'temperature_2m_mean': 1,
        'temperature_2m_min': 1,
        'temperature_2m_max': 1,
        'precipitation_sum': 1,
        'wind_speed_10m_mean': 1,
        'wind_gusts_10m_max': 1,
        'pressure_msl_mean': 0,
        'snowfall_sum': 1,
        'sunshine_duration': 0
    })

    # Добавляем номер недели в году и среднюю за год
    weekly_data['week_of_year'] = weekly_data.index.isocalendar().week
    weekly_data['year'] = weekly_data.index.year

    # Переименовываем колонки
    weekly_data.rename(columns={
        'temperature_2m_mean': 'temp_mean',
        'temperature_2m_min': 'temp_min',
        'temperature_2m_max': 'temp_max',
        'precipitation_sum': 'precip_sum',
        'wind_speed_10m_mean': 'wind_mean',
        'wind_gusts_10m_max': 'wind_gust_max',
        'pressure_msl_mean': 'pressure_mean',
        'snowfall_sum': 'snow_sum',
        'sunshine_duration': 'sunshine_hours'
    }, inplace=True)

    return weekly_data


# --- 5. Сохранение результатов ---
def save_to_csv(df, filename, directory="data"):
    """
    Сохраняет DataFrame в CSV в указанную директорию.
    Создаёт директорию, если её нет.
    """
    # Создаём директорию, если не существует
    os.makedirs(directory, exist_ok=True)

    filepath = os.path.join(directory, filename)
    df.to_csv(filepath, encoding='utf-8-sig')
    print(f"Данные сохранены в: {filepath}")
    print(f"Размер файла: {os.path.getsize(filepath):,} байт")


# --- 6. Основная логика ---
if __name__ == "__main__":
    try:
        CITY = "Байкальск"

        # Последние 5 лет (включая текущий)
        current_year = datetime.now().year
        YEARS = list(range(current_year - 4, current_year + 1))

        print(f"Сбор данных за {len(YEARS)} лет: {YEARS[0]}-{YEARS[-1]}")
        print("-" * 50)

        # 1. Получаем координаты
        lat, lon = get_coordinates(CITY)
        time.sleep(1)

        # 2. Собираем данные за все годы
        daily_data = fetch_multiyear_weather(lat, lon, YEARS)

        # 3. Сохраняем сырые дневные данные
        save_to_csv(daily_data, f"weather_{CITY}_daily_{YEARS[0]}-{YEARS[-1]}.csv")

        # 4. Группируем по неделям
        weekly_data = aggregate_weekly(daily_data)

        # 5. Сохраняем недельные данные
        save_to_csv(weekly_data, f"weather_{CITY}_weekly_{YEARS[0]}-{YEARS[-1]}.csv")

        # 6. Выводим статистику для быстрого анализа
        print("\n" + "=" * 50)
        print("СТАТИСТИКА ЗА 5 ЛЕТ ПО НЕДЕЛЯМ")
        print("=" * 50)

        print(f"\nТемпература (°C):")
        print(f"  Средняя: {weekly_data['temp_mean'].mean():.1f}")
        print(f"  Минимальная: {weekly_data['temp_min'].min():.1f}")
        print(f"  Максимальная: {weekly_data['temp_max'].max():.1f}")

        print(f"\nОсадки (мм/нед):")
        print(f"  Средние: {weekly_data['precip_sum'].mean():.1f}")
        print(f"  Максимальные за неделю: {weekly_data['precip_sum'].max():.1f}")

        print(f"\nВетер (м/с):")
        print(f"  Средний: {weekly_data['wind_mean'].mean():.1f}")
        print(f"  Максимальный порыв: {weekly_data['wind_gust_max'].max():.1f}")

        # 7. Пример данных для проверки
        print(f"\nПервые 3 и последние 3 недели данных:")
        print(weekly_data[['year', 'week_of_year', 'temp_mean', 'precip_sum']].head(3))
        print("...")
        print(weekly_data[['year', 'week_of_year', 'temp_mean', 'precip_sum']].tail(3))

    except Exception as e:
        print(f"Ошибка: {e}")
