"""
Утилиты для работы с датой и временем пользователя.

Главное правило: НИКОГДА не используем date.today() напрямую в хэндлерах.
Вместо этого — local_today(user) и local_now(user).

Это гарантирует корректную дату для пользователей из разных часовых поясов:
- Дубай в 01:30 ночи: локальная дата — ЗАВТРА, не сегодня
- Нью-Йорк в 23:00: локальная дата — ещё СЕГОДНЯ по UTC-5
"""

import pytz
import logging
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback если timezone не задан или невалидный
DEFAULT_TZ = "Europe/Moscow"

# Расширенный словарь для onboarding (быстрый путь без GPT)
CITY_TO_TZ: dict[str, str] = {
    # Россия и СНГ
    "москва": "Europe/Moscow", "питер": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow", "спб": "Europe/Moscow",
    # Юг России (UTC+3 = Москва)
    "волгоград": "Europe/Moscow", "волгодонск": "Europe/Moscow",
    "ростов": "Europe/Moscow", "ростов-на-дону": "Europe/Moscow",
    "краснодар": "Europe/Moscow", "сочи": "Europe/Moscow",
    "ставрополь": "Europe/Moscow", "астрахань": "Europe/Moscow",
    "новороссийск": "Europe/Moscow", "таганрог": "Europe/Moscow",
    "шахты": "Europe/Moscow", "новочеркасск": "Europe/Moscow",
    # Центр России (UTC+3 = Москва)
    "казань": "Europe/Moscow", "нижний новгород": "Europe/Moscow",
    "нн": "Europe/Moscow", "воронеж": "Europe/Moscow",
    "тула": "Europe/Moscow", "рязань": "Europe/Moscow",
    "тверь": "Europe/Moscow", "ярославль": "Europe/Moscow",
    "иваново": "Europe/Moscow", "калуга": "Europe/Moscow",
    "брянск": "Europe/Moscow", "орел": "Europe/Moscow",
    "курск": "Europe/Moscow", "белгород": "Europe/Moscow",
    "липецк": "Europe/Moscow", "тамбов": "Europe/Moscow",
    "владимир": "Europe/Moscow", "кострома": "Europe/Moscow",
    "смоленск": "Europe/Moscow", "псков": "Europe/Moscow",
    "новгород": "Europe/Moscow", "вологда": "Europe/Moscow",
    "архангельск": "Europe/Moscow", "петрозаводск": "Europe/Moscow",
    "мурманск": "Europe/Moscow", "сыктывкар": "Europe/Moscow",
    "чебоксары": "Europe/Moscow", "йошкар-ола": "Europe/Moscow",
    "саранск": "Europe/Moscow", "пенза": "Europe/Moscow",
    "ульяновск": "Europe/Moscow", "саратов": "Europe/Moscow",
    "балаково": "Europe/Moscow", "тольятти": "Europe/Moscow",
    "набережные челны": "Europe/Moscow",
    # Урал (UTC+5 = Екатеринбург)
    "новосибирск": "Asia/Novosibirsk", "екатеринбург": "Asia/Yekaterinburg",
    "екб": "Asia/Yekaterinburg", "челябинск": "Asia/Yekaterinburg",
    "пермь": "Asia/Yekaterinburg", "уфа": "Asia/Yekaterinburg",
    "самара": "Europe/Samara", "оренбург": "Asia/Yekaterinburg",
    "магнитогорск": "Asia/Yekaterinburg", "нижний тагил": "Asia/Yekaterinburg",
    "тюмень": "Asia/Yekaterinburg", "курган": "Asia/Yekaterinburg",
    # Сибирь
    "красноярск": "Asia/Krasnoyarsk", "иркутск": "Asia/Irkutsk",
    "омск": "Asia/Omsk", "томск": "Asia/Tomsk",
    "барнаул": "Asia/Barnaul", "кемерово": "Asia/Novokuznetsk",
    "новокузнецк": "Asia/Novokuznetsk", "абакан": "Asia/Krasnoyarsk",
    "чита": "Asia/Chita", "улан-удэ": "Asia/Irkutsk",
    "якутск": "Asia/Yakutsk",
    # Дальний Восток
    "владивосток": "Asia/Vladivostok", "хабаровск": "Asia/Vladivostok",
    "благовещенск": "Asia/Yakutsk", "южно-сахалинск": "Asia/Sakhalin",
    "магадан": "Asia/Magadan", "петропавловск-камчатский": "Asia/Kamchatka",
    # Запад России
    "калининград": "Europe/Kaliningrad",
    # Украина, Беларусь, Молдова
    "киев": "Europe/Kiev", "харьков": "Europe/Kiev",
    "одесса": "Europe/Kiev", "львов": "Europe/Kiev",
    "минск": "Europe/Minsk", "кишинёв": "Europe/Chisinau",
    # Казахстан, Центральная Азия
    "алматы": "Asia/Almaty", "астана": "Asia/Almaty",
    "нур-султан": "Asia/Almaty", "шымкент": "Asia/Almaty",
    "ташкент": "Asia/Tashkent", "бишкек": "Asia/Bishkek",
    "душанбе": "Asia/Dushanbe", "ашхабад": "Asia/Ashgabat",
    # Кавказ
    "баку": "Asia/Baku", "тбилиси": "Asia/Tbilisi",
    "ереван": "Asia/Yerevan",
    # Европа
    "берлин": "Europe/Berlin", "мюнхен": "Europe/Berlin",
    "вена": "Europe/Vienna", "варшава": "Europe/Warsaw",
    "прага": "Europe/Prague", "братислава": "Europe/Bratislava",
    "будапешт": "Europe/Budapest", "бухарест": "Europe/Bucharest",
    "белград": "Europe/Belgrade", "загреб": "Europe/Zagreb",
    "лондон": "Europe/London", "дублин": "Europe/Dublin",
    "париж": "Europe/Paris", "лион": "Europe/Paris",
    "мадрид": "Europe/Madrid", "барселона": "Europe/Madrid",
    "рим": "Europe/Rome", "милан": "Europe/Rome",
    "амстердам": "Europe/Amsterdam", "брюссель": "Europe/Brussels",
    "стокгольм": "Europe/Stockholm", "осло": "Europe/Oslo",
    "копенгаген": "Europe/Copenhagen", "хельсинки": "Europe/Helsinki",
    "рига": "Europe/Riga", "таллин": "Europe/Tallinn",
    "вильнюс": "Europe/Vilnius", "афины": "Europe/Athens",
    "стамбул": "Europe/Istanbul", "анкара": "Europe/Istanbul",
    # Ближний Восток
    "дубай": "Asia/Dubai", "dubai": "Asia/Dubai",
    "абу-даби": "Asia/Dubai", "доха": "Asia/Qatar",
    "эр-рияд": "Asia/Riyadh", "кувейт": "Asia/Kuwait",
    "тель-авив": "Asia/Jerusalem", "иерусалим": "Asia/Jerusalem",
    "амман": "Asia/Amman", "бейрут": "Asia/Beirut",
    # Азия
    "дели": "Asia/Kolkata", "мумбаи": "Asia/Kolkata",
    "бангалор": "Asia/Kolkata", "карачи": "Asia/Karachi",
    "лахор": "Asia/Karachi", "дакка": "Asia/Dhaka",
    "токио": "Asia/Tokyo", "осака": "Asia/Tokyo",
    "пекин": "Asia/Shanghai", "шанхай": "Asia/Shanghai",
    "гонконг": "Asia/Hong_Kong", "сингапур": "Asia/Singapore",
    "джакарта": "Asia/Jakarta", "бангкок": "Asia/Bangkok",
    "куала-лумпур": "Asia/Kuala_Lumpur", "манила": "Asia/Manila",
    "сеул": "Asia/Seoul", "тайпей": "Asia/Taipei",
    # Австралия
    "сидней": "Australia/Sydney", "мельбурн": "Australia/Melbourne",
    "брисбен": "Australia/Brisbane", "перт": "Australia/Perth",
    # Америка
    "нью-йорк": "America/New_York", "бостон": "America/New_York",
    "вашингтон": "America/New_York", "майами": "America/New_York",
    "чикаго": "America/Chicago", "даллас": "America/Chicago",
    "денвер": "America/Denver", "феникс": "America/Phoenix",
    "лос-анджелес": "America/Los_Angeles", "сан-франциско": "America/Los_Angeles",
    "сиэтл": "America/Los_Angeles", "лас-вегас": "America/Los_Angeles",
    "торонто": "America/Toronto", "ванкувер": "America/Vancouver",
    "монреаль": "America/Montreal", "мехико": "America/Mexico_City",
    "богота": "America/Bogota", "лима": "America/Lima",
    "сантьяго": "America/Santiago", "буэнос-айрес": "America/Argentina/Buenos_Aires",
    "сан-паулу": "America/Sao_Paulo", "рио-де-жанейро": "America/Sao_Paulo",
    # Африка
    "каир": "Africa/Cairo", "касабланка": "Africa/Casablanca",
    "найроби": "Africa/Nairobi", "лагос": "Africa/Lagos",
    "йоханнесбург": "Africa/Johannesburg",
}


def get_user_timezone(user) -> pytz.BaseTzInfo:
    """Возвращает pytz timezone для пользователя. Никогда не падает."""
    tz_name = getattr(user, "timezone", DEFAULT_TZ) or DEFAULT_TZ
    try:
        return pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{tz_name}' for user {getattr(user, 'id', '?')}, using {DEFAULT_TZ}")
        return pytz.timezone(DEFAULT_TZ)


def local_now(user) -> datetime:
    """Текущее datetime в часовом поясе пользователя."""
    tz = get_user_timezone(user)
    return datetime.now(pytz.utc).astimezone(tz)


def local_today(user) -> date:
    """Текущая дата в часовом поясе пользователя. Используй вместо date.today()."""
    return local_now(user).date()


def local_time_str(user) -> str:
    """Текущее время строкой: 'пятница, 14:32 (Europe/Moscow)'"""
    now = local_now(user)
    day_names = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    day = day_names[now.weekday()]
    tz_name = getattr(user, "timezone", DEFAULT_TZ) or DEFAULT_TZ
    return f"{day}, {now.strftime('%d.%m.%Y %H:%M')} ({tz_name})"


def resolve_city_to_tz(city_input: str) -> Optional[str]:
    """
    Пытается определить timezone по названию города.
    Возвращает строку вида 'Europe/Moscow' или None если не найдено.
    """
    normalized = city_input.strip().lower()

    if not normalized:
        return None

    # Прямое совпадение
    if normalized in CITY_TO_TZ:
        return CITY_TO_TZ[normalized]

    # Частичное совпадение (например, 'новосиб' → 'новосибирск')
    for city, tz in CITY_TO_TZ.items():
        if normalized in city or city in normalized:
            return tz

    # Попытка использовать ввод напрямую как IANA timezone ('Europe/Moscow')
    if "/" in city_input:
        try:
            pytz.timezone(city_input)
            return city_input
        except pytz.exceptions.UnknownTimeZoneError:
            pass

    return None


def utc_to_local_str(utc_time_str: str, user) -> str:
    """
    Конвертирует UTC-строку 'HH:MM' в локальное время пользователя.
    Используется для отображения расписания напоминаний.
    """
    try:
        h, m = map(int, utc_time_str.split(":"))
        utc_dt = datetime.now(pytz.utc).replace(hour=h, minute=m, second=0, microsecond=0)
        local_dt = utc_dt.astimezone(get_user_timezone(user))
        return local_dt.strftime("%H:%M")
    except Exception:
        return utc_time_str
