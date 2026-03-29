"""
AI Service — обёртка над OpenAI GPT-4o.

Ключевые принципы:
- Фото еды: AI распознаёт состав, но НЕ угадывает вес — спрашивает у пользователя.
- Контекст: последние N сообщений хранятся в Redis с TTL 24ч.
- System prompt: биохакер-коуч с дисклеймером на каждом ответе по питанию/тренировкам.
"""

import json
import asyncio
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
2. Опиши состав кратко (2-3 строки).
3. Предложи 3 варианта типичного веса порции для ЭТОГО конкретного блюда.

ФОРМАТ ОТВЕТА (строго):
Сначала описание блюда, затем в ПОСЛЕДНЕЙ строке — ровно 3 варианта веса
в формате PORTIONS:число1|число2|число3
Числа — граммы, через вертикальную черту, без пробелов и текста.

Примеры последней строки:
- Для тарелки борща: PORTIONS:250|350|500
- Для стейка: PORTIONS:150|200|300
- Для салата «Цезарь»: PORTIONS:180|250|350
- Для одного яблока: PORTIONS:130|180|220

Подбирай граммовки реалистично для конкретного блюда!
НЕ рассчитывай КБЖУ — только определи состав и предложи варианты веса."""


FOOD_JSON_PROMPT = """Проанализируй еду и верни результат СТРОГО в формате JSON.
Никакого текста до или после JSON — только валидный JSON-объект.

Формат:
{
  "items": [
    {
      "name": "название продукта",
      "weight_g": <оценка веса в граммах>,
      "calories": <ккал>,
      "protein": <белки в граммах>,
      "fat": <жиры в граммах>,
      "carbs": <углеводы в граммах>
    }
  ],
  "total": {
    "weight_g": <сумма весов>,
    "calories": <сумма ккал>,
    "protein": <сумма белков>,
    "fat": <сумма жиров>,
    "carbs": <сумма углеводов>
  },
  "comment": "<краткий комментарий коуча — 1 предложение, с эмодзи>",
  "confidence": "high" | "medium" | "low"
}

ПРАВИЛА:
- Если это фото: оцени вес КАЖДОГО компонента визуально по размеру тарелки/порции.
  Типичная тарелка ≈ 25 см. Используй визуальные подсказки для оценки.
- Если это текст: парси количество и вес из описания. Если вес не указан —
  используй стандартные порции (яйцо=60г, тост=30г, стакан=250мл).
- КБЖУ считай на основе оценённого веса по стандартным таблицам нутриентов.
- confidence: "high" — продукты чётко видны/описаны, вес легко оценить;
  "medium" — есть сомнения по составу или весу;
  "low" — сложно определить или фото нечёткое.
- Числа округляй до целых (кроме граммов белков/жиров/углеводов — до 1 знака).
- comment: дружеский, мотивирующий. Если еда вредная — без нотаций, правило 80/20.
- Отвечай ТОЛЬКО JSON. Ничего кроме JSON."""


import re as _re_portions

def parse_portion_options(ai_response: str) -> list[int]:
    """
    Извлекает варианты веса порций из ответа AI.
    Ищет строку формата PORTIONS:150|300|450
    Возвращает список из 3 чисел или дефолт [150, 300, 450].
    """
    match = _re_portions.search(r"PORTIONS:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)", ai_response)
    if match:
        portions = [int(match.group(i)) for i in (1, 2, 3)]
        if all(20 <= p <= 3000 for p in portions):
            return sorted(portions)
    return [150, 300, 450]


def clean_portions_tag(ai_response: str) -> str:
    """Убирает техническую строку PORTIONS: из текста перед показом пользователю."""
    return _re_portions.sub(r"\n?PORTIONS:\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*$", "", ai_response).strip()


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
        caption: Optional[str] = None,
    ) -> str:
        """
        Первый шаг анализа фото: распознаём состав, НО спрашиваем вес у пользователя.
        Если пользователь приложил подпись к фото — используем как уточнение.
        """
        b64 = base64.b64encode(photo_bytes).decode()
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        }

        system = self._build_system_prompt(user_profile)
        history = await context_store.get_context(user_id)

        # Если пользователь написал что именно на фото — добавляем как контекст
        text_prompt = PHOTO_ANALYSIS_PROMPT
        if caption:
            text_prompt = (
                f"Пользователь сообщил что на фото: «{caption}».\n\n"
                + PHOTO_ANALYSIS_PROMPT
            )

        user_content = [
            image_content,
            {"type": "text", "text": text_prompt},
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
        # Сохраняем в контекст очищенный текст (без PORTIONS:)
        clean_answer = clean_portions_tag(answer)
        await context_store.add_message(user_id, "assistant", clean_answer)
        # Возвращаем оригинал — хэндлер сам распарсит порции
        return answer

    async def calculate_nutrition_with_weight(
        self,
        user_id: int,
        weight_g: float,
        user_profile: Optional[dict] = None,
    ) -> str:
        """
        Второй шаг: пользователь указал вес, считаем КБЖУ.
        GPT уже знает состав блюда из контекста (Redis).

        ФИКС: Промпт запрещает повторять распознанные продукты,
        чтобы не дублировать ответ из шага 1.
        """
        prompt = (
            f"Пользователь указал, что общий вес порции: {weight_g:.0f} г.\n\n"
            f"Рассчитай КБЖУ для блюда, которое ты распознал в предыдущем сообщении.\n"
            f"НЕ повторяй список продуктов — пользователь его уже видел.\n"
            f"Сразу выведи таблицу:\n"
            f"| Продукт | Вес (г) | Ккал | Белки (г) | Жиры (г) | Углеводы (г) |\n"
            f"И строку ИТОГО.\n"
            f"Распредели {weight_g:.0f} г пропорционально между продуктами.\n"
            f"После таблицы — краткий комментарий коуча (1-2 предложения)."
        )
        return await self.chat(user_id, prompt, user_profile)

    async def analyze_food_complete(
        self,
        user_id: int,
        photo_bytes: bytes | None = None,
        text_input: str | None = None,
        user_profile: dict | None = None,
    ) -> dict | None:
        """
        Единый метод анализа еды: фото и/или текст → JSON с КБЖУ.
        1 вызов AI вместо 2, JSON mode, timeout 30с + 1 retry.
        """
        import base64

        system = self._build_system_prompt(user_profile)
        content = []

        if photo_bytes:
            b64 = base64.b64encode(photo_bytes).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
            })

        prompt_parts = []
        if text_input:
            prompt_parts.append(f"Пользователь описал еду: «{text_input}»\n")
        if photo_bytes:
            prompt_parts.append("Пользователь прислал фото еды.\n")
        prompt_parts.append(FOOD_JSON_PROMPT)
        content.append({"type": "text", "text": "\n".join(prompt_parts)})

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]

        last_error = None
        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=settings.openai_model,
                        messages=messages,
                        max_tokens=800,
                        temperature=0.3,
                        response_format={"type": "json_object"},
                    ),
                    timeout=30.0,
                )

                raw = response.choices[0].message.content
                result = json.loads(raw)

                if not isinstance(result.get("items"), list):
                    logger.warning(f"AI: no items array. Raw: {raw[:200]}")
                    last_error = "invalid structure"
                    continue

                total = result.get("total", {})
                if not total.get("calories"):
                    total = self._sum_items(result["items"])
                    result["total"] = total

                if not total.get("calories"):
                    logger.warning(f"AI: 0 calories. Raw: {raw[:200]}")
                    last_error = "zero calories"
                    continue

                try:
                    summary = (
                        f"Пользователь съел: "
                        + ", ".join(i.get("name", "?") for i in result["items"])
                        + f". Итого: {total['calories']:.0f} ккал."
                    )
                    await context_store.add_message(user_id, "user", summary)
                    await context_store.add_message(
                        user_id, "assistant",
                        f"Записал в дневник: {total['calories']:.0f} ккал, "
                        f"Б:{total.get('protein',0):.0f}г, "
                        f"Ж:{total.get('fat',0):.0f}г, "
                        f"У:{total.get('carbs',0):.0f}г."
                    )
                except Exception:
                    pass

                logger.info(
                    f"AI food analysis OK: user={user_id} "
                    f"cal={total['calories']:.0f} items={len(result['items'])} "
                    f"attempt={attempt+1}"
                )
                return result

            except asyncio.TimeoutError:
                logger.warning(f"AI timeout: user={user_id} attempt={attempt+1}")
                last_error = "timeout"
            except json.JSONDecodeError as e:
                logger.error(f"AI JSON parse error: {e}")
                last_error = "json_error"
            except Exception as e:
                logger.error(f"AI call error: {e}", exc_info=True)
                last_error = str(e)

        logger.error(f"AI food analysis FAILED: user={user_id} last_error={last_error}")
        return None

    @staticmethod
    def _sum_items(items: list[dict]) -> dict:
        """Суммирует КБЖУ из списка items."""
        total = {"weight_g": 0, "calories": 0, "protein": 0, "fat": 0, "carbs": 0}
        for item in items:
            for key in total:
                try:
                    total[key] += float(item.get(key, 0) or 0)
                except (ValueError, TypeError):
                    pass
        total["calories"] = round(total["calories"])
        total["weight_g"] = round(total["weight_g"])
        total["protein"]  = round(total["protein"], 1)
        total["fat"]      = round(total["fat"], 1)
        total["carbs"]    = round(total["carbs"], 1)
        return total

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
