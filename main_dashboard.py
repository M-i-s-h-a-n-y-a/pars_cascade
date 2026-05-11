import streamlit as st
import os
import sys

# Настройка страницы ДОЛЖНА быть первой командой Streamlit
st.set_page_config(
    page_title="Анализ города",
    page_icon="🏔️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Получаем путь к директории, где находится main_dashboard.py (на директорию выше папки vizualization)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Путь к папке с дашбордами
DASHBOARDS_DIR = os.path.join(CURRENT_DIR, 'vizualization')

# Стилизация
st.markdown("""
<style>
    .main-header {
        font-size: 2.8rem;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
        margin-bottom: 0.5rem;
        text-align: center;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #6b7280;
        margin-bottom: 2rem;
        text-align: center;
    }
    .nav-card {
        padding: 1.5rem;
        border-radius: 15px;
        background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
        border: 1px solid #dee2e6;
        transition: transform 0.2s;
        margin-bottom: 1rem;
    }
    .nav-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 25px rgba(0,0,0,0.1);
    }
    .nav-card h3 {
        color: #1f2937;
        margin-bottom: 0.5rem;
    }
    .nav-card p {
        color: #6b7280;
        font-size: 0.9rem;
    }
    .stat-box {
        padding: 1.5rem;
        border-radius: 10px;
        background: white;
        box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        text-align: center;
        margin-bottom: 1rem;
    }
    .stat-box .emoji {
        font-size: 2rem;
        margin-bottom: 0.5rem;
    }
    .stat-box .value {
        font-size: 1.5rem;
        font-weight: bold;
        color: #1f2937;
    }
    .stat-box .label {
        font-size: 0.9rem;
        color: #6b7280;
    }
</style>
""", unsafe_allow_html=True)

# Инициализация состояния сессии
if 'current_page' not in st.session_state:
    st.session_state.current_page = 'home'


# Функция для безопасной загрузки дашборда
def load_dashboard(filename):
    """Загружает и выполняет код дашборда из папки vizualization"""
    # Теперь путь строится от DASHBOARDS_DIR (папка vizualization)
    file_path = os.path.join(DASHBOARDS_DIR, filename)

    if not os.path.exists(file_path):
        st.error(f"❌ Файл не найден: {file_path}")
        st.info(f"Проверьте, что файл {filename} находится в папке: {DASHBOARDS_DIR}")

        # Показываем содержимое папки vizualization для отладки
        st.write(f"Содержимое папки {DASHBOARDS_DIR}:")
        try:
            files = os.listdir(DASHBOARDS_DIR)
            for f in files:
                if f.endswith('.py'):
                    st.write(f"📄 {f}")
        except Exception as e:
            st.write(f"Ошибка чтения папки: {e}")
        return

    try:
        # Читаем код файла
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()

        # Удаляем все st.set_page_config из кода (они могут быть с разным форматированием)
        import re
        # Паттерн для поиска set_page_config с любыми параметрами
        pattern = r'st\.set_page_config\s*\([^)]*\)'
        code = re.sub(pattern, '# set_page_config removed for embedding', code)

        # Компилируем и выполняем код
        compiled_code = compile(code, file_path, 'exec')
        exec(compiled_code, globals())

    except SyntaxError as e:
        st.error(f"❌ Синтаксическая ошибка в {filename}")
        st.code(f"Строка {e.lineno}: {e.msg}")
        st.code(e.text if hasattr(e, 'text') else "Не удалось показать строку с ошибкой")

        # Показываем проблемную область файла
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            start = max(0, e.lineno - 3)
            end = min(len(lines), e.lineno + 2)
            st.write("Контекст ошибки:")
            for i in range(start, end):
                prefix = ">>>" if i + 1 == e.lineno else "   "
                st.code(f"{prefix} Строка {i + 1}: {lines[i].rstrip()}")
        except:
            pass

    except IndentationError as e:
        st.error(f"❌ Ошибка отступа в {filename}")
        st.code(f"Строка {e.lineno}: {e.msg}")

        # Показываем проблемную область
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            start = max(0, e.lineno - 3)
            end = min(len(lines), e.lineno + 2)
            st.write("Контекст ошибки отступа:")
            for i in range(start, end):
                prefix = ">>>" if i + 1 == e.lineno else "   "
                line_content = lines[i].replace(' ', '·').replace('\t', '→→→→')
                st.code(f"{prefix} Строка {i + 1}: {line_content.rstrip()}")
        except:
            pass

    except Exception as e:
        st.error(f"❌ Ошибка при загрузке {filename}: {str(e)}")
        st.error(f"Тип ошибки: {type(e).__name__}")


# Боковая панель навигации
with st.sidebar:
    st.markdown("## 🏔️ Навигация")
    st.markdown("---")

    # Кнопки навигации
    nav_pages = {
        'home': '🏠 Главная',
        'overview': '🗺️ Плотность застройки',
        'price': '💰 Ценовое зонирование',
        'transport': '🚌 Транспортная доступность',
        'weather': '🌤️ Погодная аналитика',
        'invest': '📊 Инвестиционная карта'
    }

    for page_key, page_label in nav_pages.items():
        if st.button(
                page_label,
                use_container_width=True,
                type="primary" if st.session_state.current_page == page_key else "secondary",
                key=f"nav_{page_key}"
        ):
            st.session_state.current_page = page_key
            st.rerun()

    st.markdown("---")

    # Информация о дашбордах
    with st.expander("📌 О системе", expanded=False):
        st.info("""
        **Аналитическая платформа Байкальска**

        5 инструментов для анализа:

        🗺️ Плотность застройки
        💰 Цены на недвижимость
        🚌 Транспортная доступность
        🌤️ Погодная аналитика
        📊 Инвестиционная карта
        """)

    # Отладка
    with st.expander("🔧 Отладка", expanded=False):
        st.write(f"Директория main_dashboard: {CURRENT_DIR}")
        st.write(f"Директория дашбордов: {DASHBOARDS_DIR}")
        if st.button("Проверить файлы"):
            try:
                # Проверяем файлы в папке vizualization
                if os.path.exists(DASHBOARDS_DIR):
                    files = os.listdir(DASHBOARDS_DIR)
                    st.write(f"Найденные .py файлы в vizualization:")
                    for f in sorted(files):
                        if f.endswith('.py'):
                            file_path = os.path.join(DASHBOARDS_DIR, f)
                            st.write(f"✅ {f}" if os.path.exists(file_path) else f"❌ {f}")
                else:
                    st.error(f"Папка vizualization не найдена в {CURRENT_DIR}")
            except Exception as e:
                st.error(f"Ошибка: {e}")

    st.markdown("---")
    st.caption("🟢 Все системы работают")
    st.caption("📅 Данные: май 2026")
    st.caption("📍 Байкальск, Иркутская обл.")

# Главная страница
if st.session_state.current_page == 'home':
    # Заголовок
    st.markdown('<div class="main-header">🏔️ Анализ города</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Комплексный анализ городской среды и инвестиционной привлекательности</div>',
                unsafe_allow_html=True)

    # Краткая статистика
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.markdown("""
        <div class="stat-box">
            <div class="emoji">🏢</div>
            <div class="value">5,247</div>
            <div class="label">Зданий в городе</div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown("""
        <div class="stat-box">
            <div class="emoji">📍</div>
            <div class="value">247</div>
            <div class="label">Общественных мест</div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        st.markdown("""
        <div class="stat-box">
            <div class="emoji">🚌</div>
            <div class="value">86</div>
            <div class="label">Остановок</div>
        </div>
        """, unsafe_allow_html=True)

    with col4:
        st.markdown("""
        <div class="stat-box">
            <div class="emoji">💰</div>
            <div class="value">342</div>
            <div class="label">Объявлений</div>
        </div>
        """, unsafe_allow_html=True)

    with col5:
        st.markdown("""
        <div class="stat-box">
            <div class="emoji">📐</div>
            <div class="value">45.2 км²</div>
            <div class="label">Площадь анализа</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # Карточки дашбордов
    st.markdown("### 📊 Выберите дашборд для анализа")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        <div class="nav-card">
            <h3>🗺️ Плотность застройки и общественные места</h3>
            <p>Тепловая карта плотности застройки, анализ рельефа и расположения 
            социально-значимых объектов. Индекс TAI для оценки привлекательности территории.</p>
            <small>🔑 Ключевые метрики: плотность застройки, уклон рельефа, POI, TAI индекс</small>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Открыть дашборд →", key="btn_overview", use_container_width=True):
            st.session_state.current_page = 'overview'
            st.rerun()

        st.markdown("---")

        st.markdown("""
        <div class="nav-card">
            <h3>🚌 Транспортная доступность</h3>
            <p>Анализ покрытия остановок общественного транспорта с расчетом 
            пешеходной доступности до объектов инфраструктуры.</p>
            <small>🔑 Ключевые метрики: радиус покрытия, доступность объектов</small>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Открыть дашборд →", key="btn_transport", use_container_width=True):
            st.session_state.current_page = 'transport'
            st.rerun()

        st.markdown("---")

        st.markdown("""
        <div class="nav-card">
            <h3>📊 Инвестиционная карта</h3>
            <p>Анализ коммерческой активности, концентрации бизнесов 
            и общественных пространств для оценки инвестиционного потенциала.</p>
            <small>🔑 Ключевые метрики: плотность бизнесов, рейтинги, кластеры</small>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Открыть дашборд →", key="btn_invest", use_container_width=True):
            st.session_state.current_page = 'invest'
            st.rerun()

    with col2:
        st.markdown("""
        <div class="nav-card">
            <h3>💰 Ценовое зонирование</h3>
            <p>Интерактивная карта цен на недвижимость с тепловыми картами 
            и детальной статистикой по категориям объектов.</p>
            <small>🔑 Ключевые метрики: цена за м², медианные значения, кластеризация</small>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Открыть дашборд →", key="btn_price", use_container_width=True):
            st.session_state.current_page = 'price'
            st.rerun()

        st.markdown("---")

        st.markdown("""
        <div class="nav-card">
            <h3>🌤️ Погодная аналитика</h3>
            <p>Анализ погодных условий, климатических данных и их влияния 
            на инвестиционную привлекательность территории.</p>
            <small>🔑 Ключевые метрики: температура, осадки, климатические зоны</small>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Открыть дашборд →", key="btn_weather", use_container_width=True):
            st.session_state.current_page = 'weather'
            st.rerun()

    # Дополнительная информация
    st.markdown("---")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("### 🎯 Цель проекта")
        st.info("""
        Создание единой аналитической платформы 
        для оценки городской среды Байкальска 
        и принятия инвестиционных решений.
        """)

    with col2:
        st.markdown("### 📊 Источники данных")
        st.success("""
        • **OpenStreetMap** - здания, дороги
        • **Яндекс.Карты** - POI, остановки
        • **Avito** - цены на недвижимость
        • **SRTM** - цифровая модель рельефа
        • **OpenWeatherMap** - погода
        """)

    with col3:
        st.markdown("### 🔄 Обновления")
        st.warning("""
        **Май 2026**
        • Актуализация всех данных
        • Добавлена погодная аналитика
        • Улучшена визуализация
        """)

# Загрузка дашбордов
elif st.session_state.current_page == 'overview':
    load_dashboard('over_view_territory.py')

elif st.session_state.current_page == 'price':
    load_dashboard('price_zoning_dashboard.py')

elif st.session_state.current_page == 'transport':
    load_dashboard('transport_stop_coverage.py')

elif st.session_state.current_page == 'weather':
    load_dashboard('weather_dashboard.py')

elif st.session_state.current_page == 'invest':
    load_dashboard('invest_map.py')

# Футер (только для главной страницы)
if st.session_state.current_page == 'home':
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.caption("🏔️ Байкальск 2026")
    with col2:
        st.caption("📊 Версия 2.0")
    with col3:
        st.caption("🔄 Данные: май 2026")
    with col4:
        st.caption("💡 Инвестиционный портал")