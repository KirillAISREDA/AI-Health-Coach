"""
Тесты timezone утилит.
Запуск: pytest tests/test_timezone.py -v
"""

import pytest
import os
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from datetime import date, datetime
from unittest.mock import MagicMock
import pytz

from bot.utils.timezone import (
    resolve_city_to_tz,
    local_today,
    local_now,
    get_user_timezone,
    utc_to_local_str,
    DEFAULT_TZ,
)


def make_user(tz: str):
    u = MagicMock()
    u.timezone = tz
    u.id = 1
    return u


class TestResolveCityToTz:

    def test_moscow(self):
        assert resolve_city_to_tz("Москва") == "Europe/Moscow"

    def test_moscow_lowercase(self):
        assert resolve_city_to_tz("москва") == "Europe/Moscow"

    def test_dubai(self):
        assert resolve_city_to_tz("Дубай") == "Asia/Dubai"

    def test_berlin(self):
        assert resolve_city_to_tz("Берлин") == "Europe/Berlin"

    def test_new_york(self):
        assert resolve_city_to_tz("Нью-Йорк") == "America/New_York"

    def test_tokyo(self):
        assert resolve_city_to_tz("Токио") == "Asia/Tokyo"

    def test_sydney(self):
        assert resolve_city_to_tz("Сидней") == "Australia/Sydney"

    def test_partial_match_novosibirsk(self):
        result = resolve_city_to_tz("новосиб")
        assert result == "Asia/Novosibirsk"

    def test_direct_iana_string(self):
        assert resolve_city_to_tz("Europe/London") == "Europe/London"

    def test_unknown_city_returns_none(self):
        assert resolve_city_to_tz("Мухосранск") is None

    def test_empty_string_returns_none(self):
        assert resolve_city_to_tz("") is None


class TestGetUserTimezone:

    def test_valid_timezone(self):
        user = make_user("Asia/Tokyo")
        tz = get_user_timezone(user)
        assert tz.zone == "Asia/Tokyo"

    def test_invalid_timezone_falls_back(self):
        user = make_user("Invalid/Zone")
        tz = get_user_timezone(user)
        assert tz.zone == DEFAULT_TZ

    def test_none_timezone_falls_back(self):
        user = make_user(None)
        tz = get_user_timezone(user)
        assert tz.zone == DEFAULT_TZ

    def test_empty_timezone_falls_back(self):
        user = make_user("")
        tz = get_user_timezone(user)
        assert tz.zone == DEFAULT_TZ


class TestLocalToday:

    def test_returns_date_object(self):
        user = make_user("Europe/Moscow")
        result = local_today(user)
        assert isinstance(result, date)

    def test_dubai_midnight_differs_from_utc(self):
        """В 21:30 UTC → Dubai 01:30 следующего дня."""
        user = make_user("Asia/Dubai")
        tz = pytz.timezone("Asia/Dubai")
        # Создаём момент когда UTC=21:30, Dubai=01:30 (следующий день)
        utc_time = datetime(2026, 3, 27, 21, 30, tzinfo=pytz.utc)
        dubai_time = utc_time.astimezone(tz)
        assert dubai_time.date() > utc_time.date(), \
            "Dubai дата должна быть на день вперёд UTC в этот момент"

    def test_ny_evening_same_as_utc_next_day(self):
        """В 00:30 UTC → NY всё ещё вчера (19:30 UTC-5)."""
        tz = pytz.timezone("America/New_York")
        utc_time = datetime(2026, 3, 27, 0, 30, tzinfo=pytz.utc)
        ny_time = utc_time.astimezone(tz)
        assert ny_time.date() < utc_time.date(), \
            "NY дата должна быть на день меньше UTC в этот момент"


class TestUtcToLocalStr:

    def test_moscow_utc3(self):
        user = make_user("Europe/Moscow")
        # 08:00 UTC → 11:00 Москва (UTC+3)
        result = utc_to_local_str("08:00", user)
        assert result == "11:00"

    def test_dubai_utc4(self):
        user = make_user("Asia/Dubai")
        # 08:00 UTC → 12:00 Dubai (UTC+4)
        result = utc_to_local_str("08:00", user)
        assert result == "12:00"

    def test_invalid_time_returns_original(self):
        user = make_user("Europe/Moscow")
        result = utc_to_local_str("invalid", user)
        assert result == "invalid"
