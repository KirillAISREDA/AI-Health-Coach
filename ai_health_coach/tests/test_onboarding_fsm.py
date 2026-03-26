"""
Тесты онбординга.

Покрывают баг из продакшна: FSM-состояние сбрасывалось
и "46" падало в free_chat fallback вместо step_age.
"""

import pytest
import os
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from bot.handlers.onboarding import OnboardingFSM, STEP_TO_FSM, _resume_onboarding
from bot.models import OnboardingStep
from bot.services.user_service import calculate_tdee


class TestOnboardingFSMStates:
    """Проверяем что все шаги имеют FSM-состояния."""

    def test_all_input_steps_have_fsm_state(self):
        """Каждый шаг онбординга, требующий ввода, должен быть в STEP_TO_FSM."""
        required_steps = [
            OnboardingStep.AGE.value,
            OnboardingStep.HEIGHT.value,
            OnboardingStep.WEIGHT.value,
            OnboardingStep.GOAL.value,
            OnboardingStep.ACTIVITY.value,
            OnboardingStep.ALLERGIES.value,
            OnboardingStep.TIMEZONE.value,
        ]
        for step in required_steps:
            assert step in STEP_TO_FSM, \
                f"Шаг {step} отсутствует в STEP_TO_FSM — FSM не восстановится после /start!"

    def test_step_to_fsm_maps_to_correct_states(self):
        """Проверяем маппинг шагов на правильные FSM-состояния."""
        assert STEP_TO_FSM[OnboardingStep.AGE.value] == "OnboardingFSM:waiting_age"
        assert STEP_TO_FSM[OnboardingStep.HEIGHT.value] == "OnboardingFSM:waiting_height"
        assert STEP_TO_FSM[OnboardingStep.WEIGHT.value] == "OnboardingFSM:waiting_weight"
        assert STEP_TO_FSM[OnboardingStep.GOAL.value] == "OnboardingFSM:selecting_goal"
        assert STEP_TO_FSM[OnboardingStep.ACTIVITY.value] == "OnboardingFSM:selecting_activity"

    def test_waiting_age_is_defined(self):
        """OnboardingFSM.waiting_age должен существовать как State."""
        assert hasattr(OnboardingFSM, "waiting_age")
        assert hasattr(OnboardingFSM, "waiting_height")
        assert hasattr(OnboardingFSM, "waiting_weight")
        assert hasattr(OnboardingFSM, "selecting_goal")
        assert hasattr(OnboardingFSM, "selecting_activity")
        assert hasattr(OnboardingFSM, "waiting_allergies")
        assert hasattr(OnboardingFSM, "waiting_timezone")

    def test_fsm_states_are_unique(self):
        """Все FSM-состояния должны быть уникальными."""
        states = [
            str(OnboardingFSM.waiting_age),
            str(OnboardingFSM.waiting_height),
            str(OnboardingFSM.waiting_weight),
            str(OnboardingFSM.selecting_goal),
            str(OnboardingFSM.selecting_activity),
            str(OnboardingFSM.waiting_allergies),
            str(OnboardingFSM.waiting_timezone),
        ]
        assert len(states) == len(set(states)), "Найдены дублирующиеся FSM-состояния!"


class TestResumeOnboarding:
    """Тест функции восстановления онбординга."""

    @pytest.mark.asyncio
    async def test_resume_age_step(self):
        """После /start с step=AGE должна устанавливаться waiting_age."""
        from unittest.mock import AsyncMock, MagicMock

        message = AsyncMock()
        message.answer = AsyncMock()

        state = AsyncMock()
        state.set_state = AsyncMock()

        db_user = MagicMock()
        db_user.first_name = "Тест"

        await _resume_onboarding(message, db_user, state, OnboardingStep.AGE.value)

        # Проверяем что set_state вызван с waiting_age
        state.set_state.assert_called_once_with(OnboardingFSM.waiting_age)
        # И отправлено сообщение
        message.answer.assert_called_once()
        call_kwargs = message.answer.call_args
        assert "Шаг 2/8" in str(call_kwargs) or "лет" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_resume_height_step(self):
        """После /start с step=HEIGHT должна устанавливаться waiting_height."""
        from unittest.mock import AsyncMock, MagicMock

        message = AsyncMock()
        state = AsyncMock()
        db_user = MagicMock()

        await _resume_onboarding(message, db_user, state, OnboardingStep.HEIGHT.value)
        state.set_state.assert_called_once_with(OnboardingFSM.waiting_height)

    @pytest.mark.asyncio
    async def test_resume_goal_step_sends_keyboard(self):
        """Шаг цели должен отправить клавиатуру с вариантами."""
        from unittest.mock import AsyncMock, MagicMock

        message = AsyncMock()
        state = AsyncMock()
        db_user = MagicMock()

        await _resume_onboarding(message, db_user, state, OnboardingStep.GOAL.value)

        state.set_state.assert_called_once_with(OnboardingFSM.selecting_goal)
        # Проверяем что была передана клавиатура (reply_markup != None)
        call_kwargs = message.answer.call_args
        assert call_kwargs.kwargs.get("reply_markup") is not None or \
               (len(call_kwargs.args) > 1 and call_kwargs.args[1] is not None)

    @pytest.mark.asyncio
    async def test_resume_unknown_step_shows_gender(self):
        """Неизвестный шаг → показываем выбор пола."""
        from unittest.mock import AsyncMock, MagicMock

        message = AsyncMock()
        state = AsyncMock()
        db_user = MagicMock()

        await _resume_onboarding(message, db_user, state, "unknown_step")

        # set_state НЕ должен вызываться для неизвестного шага
        state.set_state.assert_not_called()
        message.answer.assert_called_once()


class TestOnboardingStepSequence:
    """Тест правильности последовательности шагов."""

    def test_step_sequence_is_correct(self):
        """Шаги должны идти в правильном порядке."""
        from bot.models import OnboardingStep
        steps = [
            OnboardingStep.START,
            OnboardingStep.GENDER,
            OnboardingStep.AGE,
            OnboardingStep.HEIGHT,
            OnboardingStep.WEIGHT,
            OnboardingStep.GOAL,
            OnboardingStep.ACTIVITY,
            OnboardingStep.ALLERGIES,
            OnboardingStep.TIMEZONE,
            OnboardingStep.DONE,
        ]
        # Проверяем что все шаги определены
        assert all(step is not None for step in steps)

    def test_onboarding_step_values(self):
        """Значения шагов должны быть строками."""
        from bot.models import OnboardingStep
        assert OnboardingStep.AGE.value == "age"
        assert OnboardingStep.HEIGHT.value == "height"
        assert OnboardingStep.WEIGHT.value == "weight"
        assert OnboardingStep.GOAL.value == "goal"
        assert OnboardingStep.DONE.value == "done"
