"""
llm_providers.py — работа с локальной нейросетью через Ollama.

Почему Ollama, а не облако
--------------------------
Облачный ИИ здесь не используется: он недоступен из этого региона,
а обходные пути ненадёжны и требуют сторонних сервисов.

Локальная модель решает задачу без блокировок, без API-ключей и без
отправки твоих треков наружу.

Запасная модель
---------------
Если основная (qwen2.5:3b) не скачана или демон не отвечает,
подставляется qwen2.5-coder:7b - она уже была установлена в системе.
Работает хуже на классификации музыки, но лучше, чем ничего.
"""

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

# Основная модель: обычная instruct, хорошо понимает инструкции и быстрая
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# Запасные модели - подставляются по очереди, если основная недоступна.
# Первая в списке уже была в системе у пользователя.
FALLBACK_MODELS = [
    "qwen2.5-coder:7b",
    "qwen2.5-coder",
    "qwen2.5",
    "llama3.2",
    "qwen3",
]

DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# На these ключи Ollama возвращает чистый JSON без лишних слов
JSON_HINT = "Ответь ТОЛЬКО валидным JSON, без пояснений и без markdown."


class OllamaProvider:
    """
    Локальная модель через Ollama.

    Никаких ключей, никакого интернета наружу, без лимитов.
    """

    name = "ollama"

    def __init__(self, model: str = DEFAULT_MODEL, url: str = DEFAULT_OLLAMA_URL):
        self.requested_model = model
        self.url = url.rstrip("/")
        self._models: Optional[List[str]] = None
        self.active_model: Optional[str] = None

    # ------------------------------------------------------------------ #
    #  Доступность
    # ------------------------------------------------------------------ #
    def _list_models(self) -> List[str]:
        """Список установленных моделей. Пустой список = демон не отвечает."""
        if self._models is not None:
            return self._models
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=4) as r:
                data = json.loads(r.read())
            self._models = [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            self._models = []
        return self._models

    def available(self) -> bool:
        return bool(self._list_models())

    def _pick_model(self) -> Optional[str]:
        """
        Выбирает модель: сначала запрошенную, затем запасные по списку.

        Список FALLBACK_MODELS подобран так, чтобы пережить пропажу
        основной модели - у пользователя уже стоит qwen2.5-coder:7b.
        """
        models = self._list_models()
        if not models:
            return None

        if self.requested_model in models:
            return self.requested_model

        for candidate in FALLBACK_MODELS:
            for m in models:
                if m == candidate or m.startswith(candidate + ":"):
                    return m

        # вообще ничего не совпало - берём первую установленную
        return models[0]

    def is_fallback(self) -> bool:
        """
        True, если работаем не на основной модели.

        Сравниваем именно с основной моделью, а не с запрошенной. Иначе
        провайдер, который по своей просьбе работает на запасной модели,
        называл бы её «основной» и диагностика врала.
        """
        picked = self._pick_model()
        return bool(picked and picked != DEFAULT_MODEL)

    # ------------------------------------------------------------------ #
    #  Генерация
    # ------------------------------------------------------------------ #
    def generate(self, prompt: str, timeout: float = 120.0) -> Optional[str]:
        """
        Отправляет промпт и возвращает текст ответа.

        format='json' заставляет Ollama вернуть строгий JSON -
        это убирает мусор вокруг ответа, который ломал разбор.
        """
        model = self._pick_model()
        if not model:
            print("❌ [LLM] Ollama недоступна или нет установленных моделей.")
            print("   Что сделать (по порядку):")
            print("     1) ollama serve          - запустить демон")
            print("     2) ollama pull qwen2.5:3b - скачать основную модель")
            return None

        if self.is_fallback():
            print(f"⚠️ [LLM] Основная модель '{self.requested_model}' недоступна, "
                  f"использую запасную: {model}")

        payload = json.dumps({
            "model": model,
            "prompt": prompt + "\n\n" + JSON_HINT,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = json.loads(r.read()).get("response", "")
            self.active_model = model
            return text
        except urllib.error.HTTPError as e:
            print(f"❌ [LLM] Ollama вернула HTTP {e.code}")
            return None
        except Exception as e:
            print(f"❌ [LLM] Ollama не ответила: {type(e).__name__}: {str(e)[:90]}")
            return None

    def describe(self) -> str:
        model = self._pick_model()
        if not model:
            return "Ollama недоступна (демон не запущен или нет моделей)"
        tag = "запасная" if self.is_fallback() else "основная"
        return f"Ollama, модель {model} ({tag})"


def get_provider() -> OllamaProvider:
    """Единственный провайдер в проекте: локальная модель."""
    return OllamaProvider()


def get_fallback_provider() -> Optional[OllamaProvider]:
    """
    Провайдер с запасной моделью.

    Нужен там, где основная модель не годится по качеству. Сейчас это
    разбор треков по названию: на ответах вида «разбери по настроению»
    лёгкая qwen2.5:3b часто отвечает ерундой, а помощь всё же лучше
    угадывания.

    Возвращает None, если демон не отвечает или не установлено ни одной
    модели. Вызывающий код обязан это учитывать и иметь план Б.
    """
    if not FALLBACK_MODELS:
        return None
    # Просим запасную модель как основную. Дальше обычная логика
    # _pick_model: если её нет, поищет по списку, а в крайнем случае
    # возьмёт любую установленную.
    provider = OllamaProvider(model=FALLBACK_MODELS[0])
    return provider if provider.available() else None


def provider_status() -> dict:
    """Диагностика: что доступно прямо сейчас."""
    p = get_provider()
    return {
        "ollama_daemon": p.available(),
        "installed_models": p._list_models(),
        "wanted_model": p.requested_model,
        "active_model": p._pick_model(),
        "using_fallback": p.is_fallback(),
        "description": p.describe(),
    }


if __name__ == "__main__":
    print("Диагностика Ollama:\n")
    for k, v in provider_status().items():
        print(f"  {k:<20} {v}")
    p = get_provider()
    if p.available():
        print("\nТестовый запрос...")
        print("Ответ:", p.generate("Скажи одним словом: работает?"))
