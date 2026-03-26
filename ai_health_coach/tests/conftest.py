"""
Фикстуры для тестов.

Используем SQLite in-memory для изоляции и скорости.
Каждый тест получает чистую БД.
"""

import os
import pytest
import pytest_asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("BOT_TOKEN",          "test_token")
os.environ.setdefault("OPENAI_API_KEY",     "test_key")
os.environ.setdefault("POSTGRES_PASSWORD",  "test_password")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from bot.models import Base


# ── Async engine (SQLite in-memory) ─────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default asyncio policy."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as s:
        yield s


# ── Стандартные объекты ──────────────────────────────────────────────────────

@pytest.fixture
def mock_user():
    """Полностью заполненный пользователь."""
    from bot.models import User
    user = User(
        id=100500,
        username="testuser",
        first_name="Тест",
        gender="male",
        age=30,
        height_cm=178.0,
        weight_kg=80.0,
        goal="lose_weight",
        activity_level="moderate",
        timezone="Europe/Moscow",
        onboarding_done=True,
        tdee_kcal=1900.0,
        water_goal_ml=2400.0,
    )
    return user


@pytest_asyncio.fixture
async def db_user(session, mock_user):
    """Пользователь, сохранённый в тестовой БД."""
    session.add(mock_user)
    await session.commit()
    await session.refresh(mock_user)
    return mock_user


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.send_document = AsyncMock()
    bot.get_file     = AsyncMock()
    bot.download_file = AsyncMock()
    return bot


@pytest.fixture
def mock_message(mock_user):
    msg = AsyncMock()
    msg.from_user.id        = mock_user.id
    msg.from_user.username  = mock_user.username
    msg.from_user.first_name = mock_user.first_name
    msg.answer  = AsyncMock()
    msg.reply   = AsyncMock()
    return msg


@pytest.fixture
def mock_call(mock_user):
    call = AsyncMock()
    call.from_user.id        = mock_user.id
    call.from_user.username  = mock_user.username
    call.from_user.first_name = mock_user.first_name
    call.data   = ""
    call.answer = AsyncMock()
    call.message = AsyncMock()
    call.message.edit_text = AsyncMock()
    call.message.answer    = AsyncMock()
    return call


# ── Хелперы для наполнения тестовых данных ───────────────────────────────────

@pytest_asyncio.fixture
async def food_logs_week(session, db_user):
    """7 дней логов питания для пользователя."""
    from bot.models import FoodLog
    today = date.today()
    logs = []
    for i in range(7):
        d = today - timedelta(days=i)
        log = FoodLog(
            user_id=db_user.id,
            raw_input=f"test meal day -{i}",
            meal_date=d,
            calories=1800.0 + i * 50,
            protein_g=140.0 + i * 5,
            fat_g=60.0,
            carbs_g=200.0,
            is_photo=False,
            weight_confirmed=False,
        )
        logs.append(log)
        session.add(log)
    await session.commit()
    return logs


@pytest_asyncio.fixture
async def water_logs_week(session, db_user):
    """7 дней логов воды."""
    from bot.models import WaterLog
    today = date.today()
    logs = []
    for i in range(7):
        d = today - timedelta(days=i)
        log = WaterLog(
            user_id=db_user.id,
            log_date=d,
            amount_ml=2000 + i * 100,
        )
        logs.append(log)
        session.add(log)
    await session.commit()
    return logs
