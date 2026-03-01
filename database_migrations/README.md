# Alean AI Feedback Bot Database Migrations

Проект содержит миграции базы данных для системы управления отзывами отелей Alean AI Feedback Bot. Используется PostgreSQL в качестве основной СУБД и Alembic для управления миграциями.

## 📁 Структура проекта

```
database_migrations/
├── migrations/
│   ├── __init__.py
│   └── postgres/
│       ├── __init__.py
│       ├── constants.py          # Enum константы
│       ├── env.py               # Конфигурация Alembic
│       ├── script.py.mako       # Шаблон для миграций
│       ├── tables.py            # SQLAlchemy модели
│       └── versions/            # Файлы миграций
├── .gitlab/                     # GitLab CI/CD конфигурация
├── alembic.ini                  # Основная конфигурация Alembic
├── migrate.py                   # Скрипт для запуска миграций
├── setup.py                     # Конфигурация Python пакета
├── setup.cfg                    # Настройки линтеров
├── .pylintrc                    # Конфигурация Pylint
├── .gitignore                   # Игнорируемые файлы
├── gitlab-ci.yml               # GitLab CI пайплайн
└── code_quality_check.sh       # Скрипт проверки качества кода
```

## 🗄️ Модели базы данных

### Основные таблицы

- **hotels** - Отели
- **zones** - Зоны отелей
- **roles** - Роли пользователей
- **users** - Пользователи системы
- **user_hotel** - Связь пользователей с отелями

### Таблицы обратной связи

- **feedbacks** - Основная таблица отзывов
- **comments** - Комментарии к отзывам
- **attachments** - Вложения (фото, видео, документы)
- **feedback_comments** - Связь отзывов с комментариями
- **feedback_attachments** - Связь отзывов с вложениями
- **feedback_status_history** - История изменения статусов отзывов

### Таблицы анализа и конфигурации

- **model_config** - Конфигурация моделей ИИ
- **analysis_results** - Результаты анализа отзывов
- **scenarios** - Сценарии для разных зон отелей
- **reports** - Отчеты

## 🚀 Быстрый старт

### 1. Установка зависимостей

```bash
# Установка основных зависимостей
pip install -e .

# Установка зависимостей для проверки качества кода
pip install -e ".[code-quality]"
```

### 2. Настройка переменных окружения

Создайте файл `.env` в корне проекта:

```env
ALEAN_POSTGRES_HOST=localhost
ALEAN_POSTGRES_PORT=5433
ALEAN_POSTGRES_DB=alean_db
ALEAN_POSTGRES_USER=alean_user
ALEAN_POSTGRES_PASSWORD=alean_password
```

### 3. Создание и применение миграций

```bash
# Создание новой миграции
alembic revision --autogenerate -m "описание изменений"

# Применение миграций
alembic upgrade head

# Или используйте готовый скрипт
python migrate.py
```

## 📋 Команды Alembic

### Основные команды

```bash
# Показать текущую версию
alembic current

# Показать все версии
alembic history

# Показать последнюю версию
alembic heads

# Создать новую миграцию
alembic revision --autogenerate -m "описание"

# Применить миграции до последней версии
alembic upgrade head

# Применить миграции до конкретной версии
alembic upgrade <revision_id>

# Откатить миграции
alembic downgrade <revision_id>

# Откатить на одну версию назад
alembic downgrade -1
```

## 🔧 Конфигурация

## 📊 Индексы базы данных

Для обеспечения максимальной производительности в таблицах созданы следующие индексы:

### Основные индексы
- Поиск по внешним ID пользователей
- Фильтрация по статусам отзывов
- Сортировка по датам создания
- Составные индексы для сложных запросов

## 🧪 Проверка качества кода

```bash
# Запуск всех проверок
./code_quality_check.sh
```

## 🚀 CI/CD

Проект настроен для автоматического развертывания через GitLab CI/CD:

- Автоматическая проверка качества кода
- Создание и применение миграций

## 📝 Разработка

### Добавление новых моделей

1. Добавьте модель в `migrations/postgres/tables.py`
2. Если нужны новые enum'ы, добавьте их в `constants.py`
3. Создайте миграцию: `alembic revision --autogenerate -m "добавлена таблица X"`
4. Проверьте сгенерированную миграцию

