"""
AI Service — обёртка над OpenAI GPT-4o.

Ключевые принципы:
- Фото еды: AI распознаёт состав, но НЕ угадывает вес — спрашивает у пользователя.
- Контекст: последние N сообщений хранятся в Redis с TTL 24ч.
- System prompt: биохакер-коуч с дисклеймером на каждом ответе по питанию/тренировкам.
"""

import json
import base64
import logging
from typing import Optional

import redis.asyncio as aioredis
from openai import AsyncOpenAI

from bot.config import settings

logger = logging.getLogger(__name__)

# ─── System prompts ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — AI-коуч по биохакингу, нутрициологии и фитнесу. Твоё имя — HealthBot.

СТИЛЬ: мотивирующий, лаконичный, дружеский (на «ты»), профессиональный. Используй эмодзи для структуры (💧 🥗 🏋️ 💊 📊 ✅).

ПРАВИЛА ОТВЕТОВ:
1. Никогда не предлагай рацион ниже 1200 ккал для женщин и 1500 ккал для мужчин.
2. Правило 80/20: если пользователь съел вредное — не читай нотации. Помоги вписать в дневную норму.
3. Если пользователь пишет «не выспался», «болит X», «устал» — обязательно предложи облегчённый план или отдых.
4. Водный баланс: 30 мл × вес_кг + 500 мл за каждый час тренировки.
5. КБЖУ считай по формуле Миффлина-Сан Жеора с учётом цели пользователя.
6. Совместимость БАДов: не рекомендуй принимать Цинк + Кальций одновременно, Железо + Кальций, Магний + Цинк в больших дозах.

АНАЛИЗ ФОТО ЕДЫ:
- Определи состав блюда и каждый ингредиент.
- НЕ угадывай вес — всегда спрашивай: «Сколько примерно весила порция (в граммах)?»
- После получения веса — рассчитай КБЖУ и выведи таблицей.
- Формат таблицы: | Блюдо | Вес | Ккал | Б | Ж | У |

ДИСКЛЕЙМЕР (добавляй в конце каждого совета по питанию или тренировкам):
_⚠️ Информация носит рекомендательный характер. Проконсультируйся с врачом._

ЗАПРЕЩЕНО: давать медицинские диагнозы, называть дозировки лекарств (только БАДы), рекомендовать голодовки."""

PHOTO_ANALYSIS_PROMPT = """Пользователь прислал фото еды.

Твоя задача:
1. Определи все видимые продукты и блюда на фото.
2. Опиши состав кратко (2-4 строки).
3. Спроси: «⚖️ Сколько примерно весила эта порция в граммах?»

НЕ рассчитывай КБЖУ до получения веса от пользователя.
Ответ должен быть коротким и конкретным."""


# ─── Redis context storage ───────────────────────────────────────────────────

class ContextStore:
    """Хранит последние N сообщений диалога в Redis."""

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    def _key(self, user_id: int) -> str:
        return f"ctx:{user_id}"

    async def get_context(self, user_id: int) -> list[dict]:
        r = await self._get_redis()
        raw = await r.get(self._key(user_id))
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    async def add_message(self, user_id: int, role: str, content: str | list):
        r = await self._get_redis()
        messages = await self.get_context(user_id)
        messages.append({"role": role, "content": content})
        # Обрезаем до лимита
        if len(messages) > settings.context_messages_limit:
            messages = messages[-settings.context_messages_limit:]
        await r.setex(
            self._key(user_id),
            settings.context_ttl,
            json.dumps(messages, ensure_ascii=False),
        )

    async def clear_context(self, user_id: int):
        r = await self._get_redis()
        await r.delete(self._key(user_id))


context_store = ContextStore()


# ─── AI Service ──────────────────────────────────────────────────────────────

class AIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def chat(
        self,
        user_id: int,
        user_message: str,
        user_profile: Optional[dict] = None,
        save_context: bool = True,
    ) -> str:
        """Основной метод: текстовый диалог с коучем."""

        system = self._build_system_prompt(user_profile)
        history = await context_store.get_context(user_id)

        if save_context:
            await context_store.add_message(user_id, "user", user_message)

        messages = [{"role": "system", "content": system}] + history
        if not save_context:
            messages.append({"role": "user", "content": user_message})

        response = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            max_tokens=settings.openai_max_tokens,
            temperature=0.7,
        )

        answer = response.choices[0].message.content

        if save_context:
            await context_store.add_message(user_id, "assistant", answer)

        return answer

    async def analyze_food_photo(
        self,
        user_id: int,
        photo_bytes: bytes,
        user_profile: Optional[dict] = None,
    ) -> str:
        """
        Первый шаг анализа фото: распознаём состав, НО спрашиваем вес у пользователя.
        """
        b64 = base64.b64encode(photo_bytes).decode()
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        }

        system = self._build_system_prompt(user_profile)
        history = await context_store.get_context(user_id)

        user_content = [
            image_content,
            {"type": "text", "text": PHOTO_ANALYSIS_PROMPT},
        ]

        await context_store.add_message(user_id, "user", user_content)

        messages = [{"role": "system", "content": system}] + history
        messages.append({"role": "user", "content": user_content})

        response = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            max_tokens=600,
            temperature=0.5,
        )

        answer = response.choices[0].message.content
        await context_store.add_message(user_id, "assistant", answer)
        return answer

    async def calculate_nutrition_with_weight(
        self,
        user_id: int,
        weight_g: float,
        user_profile: Optional[dict] = None,
    ) -> str:
        """
        Второй шаг: пользователь указал вес, считаем КБЖУ.
        GPT уже знает состав блюда из контекста.
        """
        prompt = (
            f"Пользователь указал, что порция весила {weight_g:.0f} г. "
            f"Рассчитай КБЖУ для блюда из нашего предыдущего сообщения и выведи "
            f"в таблице: | Блюдо | Вес (г) | Ккал | Белки (г) | Жиры (г) | Углеводы (г) |. "
            f"После таблицы — краткий комментарий коуча."
        )
        return await self.chat(user_id, prompt, user_profile)

    async def generate_weekly_digest(
        self,
        user_id: int,
        week_stats: dict,
        user_profile: Optional[dict] = None,
    ) -> str:
        """Генерирует еженедельный отчёт."""
        prompt = (
            f"Составь мотивирующий еженедельный дайджест на основе данных:\n"
            f"{json.dumps(week_stats, ensure_ascii=False, indent=2)}\n\n"
            f"Формат: краткие итоги, что получилось хорошо, что улучшить. "
            f"Без лишних слов, по делу, с эмодзи. Максимум 200 слов."
        )
        return await self.chat(user_id, prompt, user_profile, save_context=False)

    async def check_supplement_compatibility(
        self,
        user_id: int,
        supplements: list[str],
    ) -> str:
        """Проверяет совместимость БАДов."""
        prompt = (
            f"Проверь совместимость этих БАДов между собой: {', '.join(supplements)}. "
            f"Укажи конфликты (если есть) и оптимальное время приёма каждого. "
            f"Формат: таблица + краткие пояснения. "
            f"В конце дисклеймер о враче."
        )
        return await self.chat(user_id, prompt, save_context=False)

    def _build_system_prompt(self, user_profile: Optional[dict]) -> str:
        """Добавляет данные профиля и текущую дату/время к системному промпту."""
        # Текущая дата и время (UTC — GPT сам учтёт TZ из профиля)
        from datetime import datetime, timezone as dt_tz
        import pytz
        now_utc = datetime.now(dt_tz.utc)

        # Локальное время пользователя если знаем TZ
        tz_name = (user_profile or {}).get("timezone", "UTC")
        try:
            tz = pytz.timezone(tz_name)
            now_local = now_utc.astimezone(tz)
            day_names = ["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]
            local_time_info = (
                f"Текущая дата и время пользователя: "
                f"{day_names[now_local.weekday()]}, "
                f"{now_local.strftime('%d.%m.%Y %H:%M')} ({tz_name})"
            )
        except Exception:
            local_time_info = f"Текущая дата UTC: {now_utc.strftime('%d.%m.%Y')}"

        date_block = f"\n\nТЕКУЩЕЕ ВРЕМЯ:\n{local_time_info}"

        if not user_profile:
            return SYSTEM_PROMPT + date_block

        profile_str = (
            f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ:\n"
            f"- Пол: {user_profile.get('gender', '?')}\n"
            f"- Возраст: {user_profile.get('age', '?')} лет\n"
            f"- Вес: {user_profile.get('weight_kg', '?')} кг\n"
            f"- Рост: {user_profile.get('height_cm', '?')} см\n"
            f"- Цель: {user_profile.get('goal', '?')}\n"
            f"- Активность: {user_profile.get('activity_level', '?')}\n"
            f"- TDEE: {user_profile.get('tdee_kcal', '?')} ккал/день\n"
            f"- Норма воды: {user_profile.get('water_goal_ml', '?')} мл/день\n"
            f"- Аллергии/ограничения: {user_profile.get('allergies', 'нет')}\n"
            f"\nУчитывай эти данные при всех рекомендациях."
        )
        return SYSTEM_PROMPT + date_block + profile_str


ai_service = AIService()
