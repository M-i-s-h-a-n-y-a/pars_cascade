import os
import subprocess
import sys
from pathlib import Path


def install_requirements():
    """Установка зависимостей из requirements.txt"""
    requirements_file = Path("requirements.txt")

    if not requirements_file.exists():
        print("❌ Файл requirements.txt не найден!")
        return False

    print("📦 Установка зависимостей из requirements.txt...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(requirements_file)])
        print("✅ Зависимости успешно установлены!\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка при установке зависимостей: {e}")
        return False


def run_parsers():
    """Запуск всех Python файлов из папки parsers"""
    parsers_dir = Path("parsers")

    if not parsers_dir.exists():
        print("❌ Папка 'parsers' не найдена!")
        return

    # Получаем список всех .py файлов и сортируем их по имени
    py_files = sorted(parsers_dir.glob("*.py"))

    if not py_files:
        print("❌ В папке 'parsers' нет Python файлов!")
        return

    print(f"🔍 Найдено {len(py_files)} файлов для запуска:\n")
    for py_file in py_files:
        print(f"  • {py_file.name}")
    print()

    # Запускаем каждый файл по очереди
    for i, py_file in enumerate(py_files, 1):
        print(f"{'=' * 50}")
        print(f"🚀 Запуск [{i}/{len(py_files)}]: {py_file.name}")
        print(f"{'=' * 50}")

        try:
            # Запускаем Python файл и ждем его завершения
            result = subprocess.run(
                [sys.executable, str(py_file)],
                capture_output=False,  # Вывод будет отображаться в реальном времени
                text=True
            )

            if result.returncode == 0:
                print(f"✅ Файл {py_file.name} выполнен успешно!\n")
            else:
                print(f"⚠️ Файл {py_file.name} завершился с кодом {result.returncode}\n")

        except Exception as e:
            print(f"❌ Ошибка при запуске {py_file.name}: {e}\n")


def main():
    """Главная функция"""
    print("🔧 Запуск main.py\n")

    # Сначала устанавливаем зависимости
    if not install_requirements():
        print("❌ Не удалось установить зависимости. Остановка выполнения.")
        return

    # Затем запускаем все парсеры
    run_parsers()

    print("🏁 Все задачи выполнены!")


if __name__ == "__main__":
    main()