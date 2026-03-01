# Main menu user buttons
MAIN_MENU_USER_LEAVE_FEEDBACK_BUTTON = "⭐ Оставить отзыв"
MAIN_MENU_USER_ADD_TO_PREVIOUS_FEEDBACK_BUTTON = "📝 Дополнить предыдущий отзыв"
MAIN_MENU_USER_ABOUT_BOT_BUTTON = "ℹ️ О боте"
MAIN_MENU_USER_HELP_BUTTON = "❓ Помощь"

USER_FEEDBACK_COMPLETION_BUTTON = "Завершить отзыв"
USER_FEEDBACK_ADDITION_COMPLETION_BUTTON = "Завершить дополнение отзыва"

MANAGER_MENU_REPORT_BUTTON = "📊 Выгрузить отчетность"
MANAGER_MENU_NEGATIVE_FEEDBACKS_BUTTON = "⚠️ Нерешенные негативные отзывы"
MANAGER_MENU_QR_BUTTON = "🔗 Сгенерировать QR код"
MANAGER_MENU_PROMPTS_BUTTON = "⚙️ Настройка инструкций"
MANAGER_MENU_SELECT_ZONE_FOR_QR_BUTTON = "Выберите зону для QR:"
MANAGER_MENU_SELECT_HOTEL_FOR_REPORT_BUTTON = "Выберите отель для формирования отчетности:"
MANAGER_MENU_SELECT_ALL_HOTELS_FOR_REPORT_BUTTON = "Выберите период для формирования отчетности по всем отелям:"

SELECT_HOTEL_FOR_REGISTRATION_MESSAGE = "Выберите отель для регистрации:"

SHARE_PHONE_NUMBER_MESSAGE = "Пожалуйста, поделитесь номером телефона, используя кнопку ниже"

NO_HOTEL_FOR_REGISTRATION_ERROR_MESSAGE = "Ошибка: не выбран отель для регистрации. Пожалуйста, начните процесс заново."

ADMIN_MENU_USER_MANAGEMENT_BUTTON = "👥 Управление пользователями"
ADMIN_MENU_BRANCH_MANAGEMENT_BUTTON = "🏢 Управление филиалами"
ADMIN_USER_MANAGEMENT_MENU_MESSAGE = """👥 <b>Управление пользователями</b>

Выберите действие:
"""
ADMIN_BRANCH_MANAGEMENT_MENU_MESSAGE = """🏢 <b>Управление филиалами</b>

Выберите действие:
"""

NO_HOTELS_FOUND_MESSAGE = """🏢 <b>Выбор филиала</b>

❌ Нет доступных отелей.
"""

ADMIN_SELECT_BRANCH_MESSAGE = """🏢 <b>Выбор филиала</b>

Выберите отель (филиал) для управления (страница {page}):
"""

CHOOSE_HOTEL_MESSAGE = "Выберите отель из доступных отелей:"

NO_HOTEL_DETECTION_ERROR_MESSAGE = "❌ Не удалось определить отель. Выберите отель заново."

NOTIFICATION_MESSAGE = """🔔 Новый отзыв требует внимания

🏨 Отель: {hotel_name}
📍 Зона: {zone_name}
📞 Номер телефона гостя: {phone_number}
📅 Дата создания: {created_at}
🔍 Основные проблемы:
• {root_causes_text}
"""

NEUTRAL_FEEDBACK_MESSAGE = "Пожалуйста, обратитесь на стойку регистрации для уточнения вашего вопроса."
NEGATIVE_FEEDBACK_MESSAGE = "Спасибо за отзыв! Я уже передал информацию менеджеру. Мы свяжемся с вами в течение 12 часов для решения вопроса."
POSITIVE_FEEDBACK_MESSAGE = "Благодарим за обратную связь! Ваше мнение поможет сделать отдых наших гостей еще лучше."

NO_HOTELS_LOAD_ERROR_MESSAGE = "❌ Ошибка при загрузке списка филиалов"
NO_HOTEL_FOUND_MESSAGE = "❌ Отель не найден"
NO_HOTEL_ADD_ERROR_MESSAGE = "❌ Ошибка при добавлении отеля"
NO_ZONE_ADD_ERROR_MESSAGE = "❌ Ошибка при добавлении зоны"
NO_ZONE_ID_FOUND_MESSAGE = "❌ Ошибка: не найден ID зоны"
NO_ZONE_DELETE_ERROR_MESSAGE = "❌ Ошибка при удалении зоны"
NO_ZONE_EDIT_ERROR_MESSAGE = "❌ Ошибка при редактировании зоны"
NO_USER_DELETE_ERROR_MESSAGE = "❌ Ошибка при удалении пользователя"
SUCCESS_ZONE_DELETE_MESSAGE = """✅ <b>Зона удалена</b>

Зона <b>{zone_name}</b> была успешно удалена.
"""

ADMIN_ADD_HOTEL_MESSAGE = """➕ <b>Добавление нового отеля</b>

Введите название отеля:
"""

ADMIN_USER_MANAGEMENT_LIST_USERS_BUTTON = "👥 Список пользователей"
ADMIN_USER_MANAGEMENT_EDIT_USER_BUTTON = "✏️ Изменить пользователя"
ADMIN_USER_MANAGEMENT_ADD_USER_BUTTON = "➕ Добавить пользователя"
MAIN_MENU_BUTTON = "🏠 Главное меню"

ADMIN_BRANCH_MANAGEMENT_SELECT_BRANCH_BUTTON = "📍 Выбрать филиал"
ADMIN_BRANCH_MANAGEMENT_ADD_BRANCH_BUTTON = "➕ Добавить филиал"

ADMIN_HOTEL_MANAGEMENT_SELECT_ZONE_BUTTON = "📍 Выбрать зону"
ADMIN_HOTEL_MANAGEMENT_EDIT_NAME_BUTTON = "✏️ Редактировать название отеля"
ADMIN_HOTEL_MANAGEMENT_EDIT_DESCRIPTION_BUTTON = "📝 Редактировать описание отеля"
ADMIN_HOTEL_MANAGEMENT_BACK_BUTTON = "🔙 Назад к выбору филиала"

ADMIN_HOTEL_MANAGEMENT_ADD_ZONE_BUTTON = "➕ Добавить зону"
ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_NAME_BUTTON = "✏️ Редактировать название"
ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_DESCRIPTION_BUTTON = "📝 Редактировать описание вопроса для зоны"
ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_ADULT_BUTTON = "🔞 Изменить возрастное ограничение"
ADMIN_HOTEL_MANAGEMENT_DELETE_ZONE_BUTTON = "🗑️ Удалить зону"

ADMIN_USER_MANAGEMENT_PHONE_NUMBER_BUTTON = "👤 Номер телефона: "

FOR_ALL_AGES_MESSAGE = "🔞 Для всех возрастных групп"
FOR_CHILDREN_MESSAGE = "👶 Для детей"

SUCCESS_ZONE_ADDITION_MESSAGE = """✅ <b>Зона добавлена</b>

Название: {zone_name}
Короткое название: {short_name}
Возрастное ограничение: {adult_text}
"""

MANAGER_MENU_LIST_FEEDBACKS_BUTTON = "К списку отзывов"

MANAGER_MENU_NO_NEGATIVE_FEEDBACKS_MESSAGE = "Нет нерешенных негативных отзывов"
MANAGER_MENU_NEGATIVE_FEEDBACKS_PAGE_MESSAGE = "Нерешенные негативные отзывы (страница {page}):"
MANAGER_MENU_FEEDBACKS_BUTTON = "К негативному отзыву"
MANAGER_MENU_FEEDBACK_MEDIA_FILES_MESSAGE = "Медиафайлы, которые пользователь прикрепил с отзывом"

NO_ZONE_FOUND_MESSAGE = "❌ Зона не найдена"
NO_ZONE_MESSAGE = "❌ Зоны не найдены"
MANAGER_MENU_SELECT_ZONE_FOR_PROMPTS_BUTTON = "Выберите зону для просмотра инструкции:"

SELECT_ZONE_FOR_FEEDBACK = "Пожалуйста, выберите зону для отзыва:"

NO_ZONE_ADULT_CHANGE_ERROR_MESSAGE = "❌ Ошибка при изменении возрастного ограничения"

NO_ZONE_NAME_CHANGE_ERROR_MESSAGE = "❌ Ошибка при редактировании названия зоны"

SUCCESS_ZONE_ADULT_CHANGE_MESSAGE = """✅ <b>Возрастное ограничение изменено</b>

{adult_text}
"""

LOAD_REPORT_FOR_ALL_HOTELS_BUTTON = "Выгрузить отчет по всем отелям"

REPORT_PERIOD_WEEK_BUTTON = "Неделя"
REPORT_PERIOD_MONTH_BUTTON = "Месяц"
REPORT_PERIOD_HALF_YEAR_BUTTON = "Полгода"
REPORT_PERIOD_YEAR_BUTTON = "Год"
REPORT_PERIOD_CUSTOM_BUTTON = "Другой период"

# Custom period messages
CUSTOM_PERIOD_INPUT_MESSAGE = """📅 <b>Укажите период для отчета</b>

Введите даты текстом в формате:
<b>ОТ ДД.ММ.ГГГГ ДО ДД.ММ.ГГГГ</b>

📌 <b>Примеры:</b>
• <code>ОТ 01.01.2025 ДО 31.01.2025</code>
• <code>ОТ 15.10.2024 ДО 15.11.2024</code>
• <code>ОТ 01.12.2024 ДО 31.12.2024</code>

⚠️ <b>Обратите внимание:</b>
• Используйте точки для разделения дня, месяца и года
• Формат даты: ДД.ММ.ГГГГ (например, 01.01.2025)
• Дата начала должна быть раньше даты окончания"""

INVALID_DATE_FORMAT_MESSAGE = """❌ <b>Неверный формат дат</b>

Пожалуйста, введите даты в правильном формате:
<b>ОТ ДД.ММ.ГГГГ ДО ДД.ММ.ГГГГ</b>

📌 <b>Пример правильного ввода:</b>
<code>ОТ 01.01.2025 ДО 31.01.2025</code>

Попробуйте еще раз или нажмите "Назад" для возврата в меню."""

INVALID_DATE_RANGE_MESSAGE = """❌ <b>Некорректный период</b>

Дата начала должна быть раньше даты окончания.

Проверьте введенные даты и попробуйте еще раз."""

NO_USER_FOUND_MESSAGE = "❌ Пользователь не найден"

# Manager menu message
MANAGER_MENU_MESSAGE = """🏨 <b>{hotel_name}</b>

👨‍💼 <b>Панель управления менеджера</b>

📊 Доступные функции:
• Формирование отчетов и аналитики
• Управление негативными отзывами
• Генерация QR-кодов для зон
• Настройка инструкций для анализа отзывов
Выберите действие из меню ниже:
"""

UNSUCCESSFULL_SEARCH_INFO_LAST_FEEDBACK_MESSAGE = "Не удалось найти информацию о предыдущем отзыве."

FEEDBACK_ADDITION_CONTEXT_MESSAGE = """📝 Дополнение отзыва

Ваша оценка: {rating_display}
Зона: {zone_name}{hotel_info}{date_text}

Ваши предыдущие комментарии:"""

FEEDBACK_ADDITION_COMMENT_DISPLAY_MESSAGE = """💬 {block}

✍️ Теперь вы можете дополнить свой отзыв новыми комментариями.
"""

DEFAULT_FEEDBACK_ADDITION_COMMENT_DISPLAY_MESSAGE = "✍️ Теперь вы можете дополнить свой отзыв новыми комментариями."

FEEDBACK_ADDITION_NO_COMMENTS_DISPLAY_MESSAGE = """📝 Дополнение отзыва

Ваша оценка: {rating_display}
Зона: {zone_name}{hotel_info}{date_text}

У вас пока нет комментария к этому отзыву.
Вы можете добавить комментарий."""


CANCEL_BUTTON = "Отменить"

NO_HOTEL_OR_ZONE_FOUND_MESSAGE = "❌ Отель или зона не найдены"

NO_DEFAULT_VALUE_FOR_RESET_MESSAGE = "Нет значения по умолчанию для сброса"

INVALID_COMMAND_FORMAT_MESSAGE = "Неверный формат команды"

NO_FEEDBACK_FOUND_MESSAGE = "Отзыв не найден"

SHARE_PHONE_NUMBER_BUTTON = "📱 Поделиться номером телефона"

WELCOME_MESSAGE = """🏨 Добро пожаловать в {hotel_name}!

Для быстрой регистрации нажмите кнопку
{share_phone_number_button} внизу экрана.
Используйте тот же номер, который указали при бронировании.
"""

NO_CONSENT_MESSAGE = "Без согласия регистрация невозможна. Нажмите 'Согласен' для продолжения."

WELCOME_MESSAGE_NO_HOTEL_NAME = """🏨 Добро пожаловать!

Для быстрой регистрации нажмите кнопку
{share_phone_number_button} внизу экрана.
Используйте тот же номер, который указали при бронировании.
"""

CONSENT_REQUEST_MESSAGE = """Согласны на обработку персональных данных для регистрации в отеле {hotel_title}?

С политикой обработки персональных данных можно ознакомиться по кнопке ниже."""

DEFAULT_WELCOME_MESSAGE = "Добро пожаловать!"

RATING_REQUEST_MESSAGE = "Пожалуйста, оцените ваш опыт:"

RATING_REQUEST_MESSAGE_ZONE = "Пожалуйста, оцените ваш опыт в зоне «{zone_name}»:"

SUCCESS_REGISTRATION_MESSAGE = "🎉 Ваша регистрация завершена, рады видеть вас среди гостей отеля."

CONSENT_MESSAGE = "Для завершения регистрации необходимо согласие на обработку персональных данных. Согласны ли вы?"

NO_DATA_COMPLETE_MESSAGE = "Данные неполные. Попробуйте пройти регистрацию заново."

LEAVE_FEEDBACK_MESSAGE = "Чтобы оставить отзыв пожалуйста нажмите кнопку 'Оставить отзыв' и выберите зону."

ABOUT_BOT_MESSAGE = """🤖 <b>О боте обратной связи</b>

Этот бот создан для гостей отеля, чтобы вы могли легко поделиться мнением о своем проживании.

<b>🔧 Основные возможности:</b>
• Оставляйте свои отзывы о сервисе и проживании
• Ставьте оценки и комментируйте удобство, чистоту и атмосферу

<b>💡 Как работает бот:</b>
Ваш отзыв будет автоматически обработан и поможет сделать сервис в отеле лучше.
Оставить отзыв — просто и удобно: выберите зону, поставьте оценку, напишите комментарий и, при желании, добавьте медиафайлы.

Спасибо, что помогаете нам становиться лучше!
"""

HELP_MESSAGE = """❓ <b>Помощь и инструкции</b>

<b>📝 Как оставить отзыв:</b>
1. Нажмите 'Оставить отзыв'
2. Выберите зону отеля
3. Поставьте оценку (1-5 звезд или 👍/👎)
4. Добавьте комментарий
5. Нажмите 'Завершить отзыв'

<b>📝 Как дополнить отзыв:</b>
• Используйте кнопку 'Дополнить предыдущий отзыв'
• Добавьте новые комментарии или медиафайлы
• Завершите дополнение

<b>📱 Поддерживаемые форматы:</b>
• Текстовые сообщения

<b>⏰ Время сессии:</b>
На заполнение и редактирование отзыва дается {session_waiting_time_message} минут.
Если не успеете завершить — отзыв будет автоматически сохранен.

<b>🔄 Повторные отзывы:</b>
Вы можете оставить несколько отзывов в одном отеле для разных зон.
Если у вас есть вопросы, обратитесь к персоналу отеля.
"""

DISABLED_MESSAGE = "❌ Отключена"
ACTIVE_MESSAGE = "✅ Активна"

EDIT_ZONE_MESSAGE = """📍 <b>Редактирование зоны</b>

<b>Название:</b> {zone_name}
<b>Короткое название:</b> {zone_short_name}
<b>Возрастное ограничение:</b> {adult_text}
<b>Статус:</b> {disabled_text}

✏️ Введите новое название зоны:
"""

EDIT_ZONE_DESCRIPTION_MESSAGE = """📍 <b>Редактирование зоны</b>

<b>Название:</b> {zone_name}
<b>Короткое название:</b> {zone_short_name}
<b>Возрастное ограничение:</b> {adult_text}
<b>Статус:</b> {disabled_text}
<b>Описание вопроса для зоны:</b> {zone_description}
"""

EDIT_ZONE_DESCRIPTION_INPUT_MESSAGE = """📍 <b>Редактирование описания вопроса для зоны</b>

<b>Текущее описание:</b> {zone_description}

📝 Введите текстовое описание вопроса для зоны:
"""

ADMIN_ADD_ZONE_MESSAGE = """➕ <b>Добавление новой зоны</b>

Введите название зоны для отеля {hotel_name}:
"""

ADMIN_ZONES_LIST_PAGE_MESSAGE = """📍 <b>Управление зонами</b>

Выберите зону для редактирования (страница {page}):
"""

NO_ZONES_FOUND_MESSAGE = """📍 <b>Управление зонами</b>

❌ Нет доступных зон
"""

NO_ZONES_FOUND_ERROR_MESSAGE = """📍 <b>Управление зонами</b>

❌ У отеля нет зон
"""

INVALID_RESERVATION_STATUS_MESSAGE = """❌ Бронирование с вашим номером телефона найдено.

Статус бронирования: {status}

К сожалению, ваше бронирование уже завершено. Для регистрации в боте необходимо иметь активное бронирование со статусом "Заезд" или "Переселение".

Если вы ожидаете заезд, обратитесь на стойку регистрации отеля для уточнения статуса вашего бронирования."""

NO_HOTEL_FOUND_ERROR_MESSAGE = "❌ Отель с кодом {hotel_code} не найден"
ERROR_LOADING_ZONE_MESSAGE = "❌ Ошибка при загрузке зоны"
ERROR_LOADING_ROLES_MESSAGE = "❌ Ошибка при загрузке ролей"
ERROR_LOADING_ZONES_LIST_MESSAGE = "❌ Ошибка при загрузке списка зон"

ZONE_SUCCESSFULLY_DELETED_MESSAGE = "✅ Пользователь {phone_number} успешно удален из отеля {hotel_name}"

BACK_BUTTON = "🔙 Назад"

USER_INFORMATION_MESSAGE = """👤 <b>Информация о пользователе</b>

📱 <b>Telegram ID:</b> {telegram_id}
📞 <b>Номер телефона:</b> {phone_number}

🏨 <b>Отели:</b>
{hotels_text}
"""

ADMIN_USER_INFO_MESSAGE = """👤 <b>Информация о пользователе</b>

📱 <b>Telegram ID:</b> {user_telegram_id}
📞 <b>Номер телефона:</b> {user_phone_number}

🏨 <b>Отели:</b>
{hotels_text}"""


ADMIN_CHANGE_USER_STATUS_MESSAGE = """✏️ <b>Изменение статуса пользователя</b>

👤 <b>Телеграм ID пользователя:</b> {user_telegram_id}
📞 <b>Телефон:</b> {user_phone_number}
🏨 <b>Отель:</b> {target_hotel_name} ({target_hotel_code})
👔 <b>Роль:</b> {target_hotel_role}
📊 <b>Статус:</b> {status_text}

Выберите действие:
"""

HANDLE_ADMIN_LIST_MESSAGE = """👥 <b>Список пользователей</b>

Выберите отель для просмотра пользователей:
"""

ERROR_CHANGING_USER_STATUS_MESSAGE = "❌ Ошибка при изменении статуса пользователя"
ERROR_SEARCHING_USER_MESSAGE = "❌ Ошибка при поиске пользователя"

ADMIN_MENU_MESSAGE = """🔧 <b>Панель администратора</b>

Выберите действие:
"""

RATE_REQUEST_MESSAGE = "Оцените, пожалуйста, активность, а затем напишите развернутый отзыв"

RATING_MESSAGE = """Ваша оценка по зоне «{zone_name}»: {stars} ({rating}/5)"""

POSITIVE_THUMB_RATING_MESSAGE = "Ваша оценка по зоне «{zone_name}»: 👍 (Понравилось)"

NEGATIVE_THUMB_RATING_MESSAGE = "Ваша оценка по зоне «{zone_name}»: 👎 (Не понравилось)"

MAX_AVAILABLE_RATING_MESSAGE = "Вы уже оставили максимум отзывов за сегодня, новые отзывы можно будет оставить завтра"

USER_STATUS_CHANGED_MESSAGE = "✅ Статус пользователя успешно изменен"

SUCCESS_USER_ROLE_CHANGED_MESSAGE = "✅ Роль пользователя успешно изменена"

CHANGE_USER_ROLE_MESSAGE = """🔄 <b>Изменение роли пользователя</b>

Выберите новую роль:
"""

ERROR_CHANGING_USER_ROLE_MESSAGE = "❌ Ошибка при изменении роли пользователя"

ROLE_NOT_FOUND_MESSAGE = "❌ Роль не найдена"

NO_EMPTY_PROMPT_MESSAGE = "❌ Инструкция не может быть пустой"

NO_EDITING_SESSION_FOUND_MESSAGE = "❌ Сессия редактирования не найдена"

RESET_PROMPT_MESSAGE = """<b>Инструкция сброшена к значению по умолчанию!</b>

<b>Зона:</b> {zone_name}
<b>Инструкция:</b> <i>{prompt}</i>
"""

EDIT_PROMPT_BUTTON = "✏️ Редактировать инструкцию"
RESET_PROMPT_BUTTON = "🔄 Сбросить инструкцию"

SUCCESS_PROMPT_UPDATING_MESSAGE = """<b>Инструкция успешно обновлена!</b>

<b>Зона:</b> {zone_name}
<b>Новая инструкция:</b>
<i>{prompt}</i>
"""

EDIT_PROMPT_MESSAGE = """✏️ <b>Редактирование инструкции для зоны: {zone_name}</b>

<b>Текущий текст инструкции:</b>
<i>{current_prompt}</i>

Пожалуйста, отправьте новый текст инструкции <b>одним</b> сообщением.
<i>Эта инструкция будет использоваться для анализа отзывов по выбранной зоне. Сформулируйте текст чётко и по сути.
После отправки вы сможете отредактировать инструкцию повторно.</i>
"""

ZONE_PROMPT_DESCRIPTION_MESSAGE = """Инструкция для зоны {zone_name}:

{prompt_text}
"""


ERROR_REPORTING_MESSAGE = "❌ Ошибка при формировании отчета. Попробуйте позже."

ERROR_HAPPENED_MESSAGE = """❌ <b>Произошла ошибка</b>

Попробуйте еще раз.
"""

NO_FOUND_USER_MESSAGE = "❌ Пользователь с номером телефона {phone_number} не найден"

ACTIVE_USER_MESSAGE = "✅ Активен"
DEACTIVATED_USER_MESSAGE = "❌ Деактивирован"

CHANGE_USER_STATUS_MESSAGE = "✏️ Изменить статус в отеле {hotel_name}"

SUCCESS_REPORTING_MESSAGE = "Отчет за {period_name} по {scope_name} сформирован"

SELECT_PERIOD_FOR_REPORTING_MESSAGE = "Выберите период для формирования отчетности по отелю {hotel_name}"

ERROR_SENDING_QR_CODE_MESSAGE = "❌ Ошибка при отправке QR кода. Попробуйте позже."

SUCCESS_QR_CODE_GENERATION_MESSAGE = "Сгенерирован QR код для отеля {hotel_name} и зоны {zone_name}"

NO_ADMIN_ACCESS_MESSAGE = "У вас нет прав администратора"

CONSENT_APPROVE_MESSAGE = "Согласен"
CONSENT_REJECT_MESSAGE = "Не согласен"

COMPOSE_PROMPT_MESSAGE = """Пожалуйста, опишите вашу активность в формате текстового сообщения.

Фото, аудио и видео сейчас не поддерживаются.
Ваши подробные комментарии очень ценны для нас!
Вы можете отправить одно или несколько текстовых сообщений (до {max_messages} в одном отзыве) или завершить отзыв и вернуться в меню.
"""

FEEDBACK_RESPONSE_AFTER_FIRST_MESSAGE = """Спасибо большое за ваш отзыв! Если захотите добавить что-то ещё — просто напишите нам. А когда будете готовы, завершайте отзыв и возвращайтесь в меню. Хорошего дня!"""

FEEDBACK_RESPONSE_AFTER_ADDITIONAL_MESSAGE = """Здорово, что вы делитесь своими впечатлениями! Добавляйте новые комментарии, если есть желание, или завершайте отзыв, когда посчитаете нужным. Мы всегда рады вашим сообщениям!"""

ERROR_PROMPT_UPDATING_MESSAGE = "❌ Ошибка при обновлении инструкции. Попробуйте позже."

NO_RESERVATION_MESSAGE = """⏳ В ближайшее время функционал бота будет доступен.

В данный момент информация о вашем бронировании не найдена в системе.
Если вы ожидаете заезд в ближайшее время, дождитесь активации вашего бронирования или обратитесь на стойку регистрации отеля."""

ENTER_PHONE_NUMBER_MESSAGE = """📞 <b>Введите номер телефона</b>

Введите номер телефона пользователя:
"""

SUCCESS_USER_ADDITION_MESSAGE = """✅ <b>Пользователь успешно добавлен!</b>

Telegram ID: {telegram_id}
Телефон: {phone_number}
Отель: {hotel_name}
Роль: {role}
"""

ERROR_USER_ADDITION_MESSAGE = """❌ <b>Ошибка при добавлении пользователя</b>
Возможные причины:
• Номер телефона уже используется другим пользователем
• Неверный формат данных
• Проблемы с базой данных

Попробуйте еще раз или обратитесь к разработчику.
"""

SUCCESS_HOTEL_DESCRIPTION_UPDATING_MESSAGE = """✅ <b>Описание отеля успешно обновлено!</b>

🏨 <b>{hotel_name}</b> ({hotel_short_name})

<u>📝 <b>Описание:</b></u> {description}
<u>👥 <b>Зарегистрированных гостей:</b></u> {guests_count}

<u>📍 <b>Зоны отеля:</b></u>
{zones_text}
"""

ERROR_GETTING_HOTEL_INFO_MESSAGE = "❌ Ошибка при получении информации об отеле"
ERROR_HOTEL_DESCRIPTION_UPDATING_MESSAGE = "❌ Ошибка при обновлении описания отеля"
ERROR_HOTEL_NAME_UPDATING_MESSAGE = "❌ Ошибка при обновлении названия отеля"
NO_HOTEL_NAME_EMPTY_MESSAGE = "❌ Название отеля не может быть пустым"

UNSUCCESSFUL_HOTEL_SHORT_NAME_GENERATION_MESSAGE = (
    "❌ Не удалось создать уникальное короткое название для отеля {hotel_name}"
)
ERROR_ZONE_NAME_UPDATING_MESSAGE = "❌ Ошибка при обновлении названия зоны"
NO_HOTEL_CODE_FOUND_MESSAGE = "❌ Ошибка: не найден код отеля"
NO_ZONE_NAME_EMPTY_MESSAGE = "❌ Название зоны не может быть пустым"

SUCCESS_ZONE_NAME_UPDATING_MESSAGE = """✅ <b>Название зоны успешно обновлено!</b>

Новое название: {new_name}
"""

ERROR_ZONE_DESCRIPTION_UPDATING_MESSAGE = "❌ Ошибка при обновлении описания вопроса для зоны"

SUCCESS_ZONE_DESCRIPTION_UPDATING_MESSAGE = """✅ <b>Описание вопроса для зоны успешно обновлено!</b>

{new_description}
"""

NO_HOTEL_FOUND_WITH_CODE_MESSAGE = "❌ Отель с кодом {hotel_code} не найден"

UNSUCCESSFUL_ZONE_SHORT_NAME_GENERATION_MESSAGE = (
    "❌ Не удалось создать уникальное короткое название для зоны {zone_name}"
)

SUCCESS_HOTEL_NAME_UPDATING_MESSAGE = """✅ <b>Название отеля успешно обновлено!</b>

🏨 <b>{hotel_name}</b> ({hotel_short_name})

<u>📝 <b>Описание:</b></u> {description}
<u>👥 <b>Зарегистрированных гостей:</b></u> {guests_count}

<u>📍 <b>Зоны отеля:</b></u>
{zones_text}
"""

SUCCESS_HOTEL_ADDITION_MESSAGE = """✅ <b>Отель добавлен</b>

Название: {hotel_name}
Короткое название: {short_name}

💡 <b>Следующие шаги:</b>
• Добавить зоны можно в разделе "Выбрать филиал"
"""
