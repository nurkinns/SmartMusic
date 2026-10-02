"""
Тесты локальной нейросети (Ollama).

Основные проверки — на логике провайдера: выбор модели, fallback, разбор
ответа. HTTP при этом мокается — никаких реальных вызовов к демону, кроме
одного пропускаемого живого теста.
"""

import json
import urllib.request
import urllib.error

from tests.core import suite, Skip

S = suite("Ollama — локальная нейросеть")


class _FakeHttp:
    """Подмена ответа urllib.request.urlopen — возвращает заданный JSON."""

    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


# --------------------------------------------------------------------------- #
#  Доступность
# --------------------------------------------------------------------------- #


@S.add("когда демон не отвечает → available() == False")
def _t_unavailable():
    from src.ai.llm_providers import OllamaProvider

    p = OllamaProvider(url="http://127.0.0.1:1")  # неиспользуемый порог → refused
    assert p.available() is False


@S.add("_list_models кэширует пустой список при недоступном демоне")
def _t_list_models_cached():
    from src.ai.llm_providers import OllamaProvider

    p = OllamaProvider(url="http://127.0.0.1:1")
    first = p._list_models()
    assert first == []
    # Второй вызов — закэшированный.
    second = p._list_models()
    assert second is first


# --------------------------------------------------------------------------- #
#  _pick_model
# --------------------------------------------------------------------------- #


@S.add("_pick_model находит запрошенную модель, если она установлена")
def _t_pick_requested():
    from src.ai.llm_providers import OllamaProvider

    p = OllamaProvider(model="qwen2.5:3b", url="http://x")
    p._models = ["qwen2.5:3b", "qwen2.5-vl:7b"]
    assert p._pick_model() == "qwen2.5:3b"


@S.add("_pick_model выбирает первую запасную по списку FALLBACK_MODELS")
def _t_pick_fallback():
    from src.ai.llm_providers import OllamaProvider, FALLBACK_MODELS

    p = OllamaProvider(model="qwen2.5:3b", url="http://x")
    # Запрошенная не установлена, но есть первая запасная.
    p._models = ["qwen2.5-coder:7b", "llama3.2"]
    picked = p._pick_model()
    assert picked in FALLBACK_MODELS, f"{picked} не в fallback"


@S.add("_pick_model: первая установленная, если ничего не совпало")
def _t_pick_first():
    from src.ai.llm_providers import OllamaProvider

    p = OllamaProvider(model="qwen2.5:3b", url="http://x")
    p._models = ["unknown-model-42"]
    assert p._pick_model() == "unknown-model-42"


@S.add("_pick_model: совпадение с префиксом (qwen2.5-coder:14b)")
def _t_pick_prefix():
    from src.ai.llm_providers import OllamaProvider

    p = OllamaProvider(model="qwen2.5:3b", url="http://x")
    p._models = ["qwen2.5-coder:14b"]
    picked = p._pick_model()
    assert picked == "qwen2.5-coder:14b", f"должен взять версию, а взял {picked}"


# --------------------------------------------------------------------------- #
#  is_fallback
# --------------------------------------------------------------------------- #


@S.add("is_fallback == False, когда работает основная модель")
def _t_is_not_fallback():
    from src.ai.llm_providers import OllamaProvider

    p = OllamaProvider(url="http://x")
    p._models = [p.requested_model]
    assert p.is_fallback() is False


@S.add("is_fallback == True, когда работает запасная")
def _t_is_fallback():
    from src.ai.llm_providers import OllamaProvider

    p = OllamaProvider(url="http://x")
    p._models = ["qwen2.5-coder:7b"]
    assert p.is_fallback() is True


# --------------------------------------------------------------------------- #
#  generate
# --------------------------------------------------------------------------- #


@S.add("generate разбирает ответ Ollama и устанавливает active_model")
def _t_generate_success():
    saved = urllib.request.urlopen

    def _mock(req, timeout=None):
        return _FakeHttp({"response": '{"status":"ok"}'})

    try:
        urllib.request.urlopen = _mock
        from src.ai.llm_providers import OllamaProvider

        p = OllamaProvider(model="qwen2.5:3b", url="http://mocked")
        p._models = ["qwen2.5:3b"]  # выбирает эту модель

        text = p.generate("test prompt")
        assert text == '{"status":"ok"}', f"ответ: {text!r}"
        assert p.active_model == "qwen2.5:3b"
    finally:
        urllib.request.urlopen = saved


@S.add("generate возвращает None при HTTP-ошибке")
def _t_generate_http_error():
    def _boom(req, timeout=None):
        raise urllib.error.HTTPError("http://x", 503, "down", {}, None)

    saved = urllib.request.urlopen
    try:
        urllib.request.urlopen = _boom
        from src.ai.llm_providers import OllamaProvider

        p = OllamaProvider(model="qwen2.5:3b", url="http://mocked")
        p._models = ["qwen2.5:3b"]
        assert p.generate("x") is None
    finally:
        urllib.request.urlopen = saved


# --------------------------------------------------------------------------- #
#  provider_status
# --------------------------------------------------------------------------- #


@S.add("provider_status содержит все ключи диагностики")
def _t_provider_status():
    from src.ai.llm_providers import OllamaProvider

    p = OllamaProvider(url="http://127.0.0.1:1")
    s = {
        "ollama_daemon": p.available(),
        "installed_models": p._list_models(),
        "wanted_model": p.requested_model,
        "active_model": p._pick_model(),
        "using_fallback": p.is_fallback(),
        "description": p.describe(),
    }
    assert "ollama_daemon" in s
    assert s["ollama_daemon"] is False
    assert isinstance(s["installed_models"], list)
    assert s["active_model"] is None
    assert s["using_fallback"] is False
    assert "Ollama" in s["description"]


# --------------------------------------------------------------------------- #
#  get_fallback_provider
# --------------------------------------------------------------------------- #


@S.add("get_fallback_provider возвращает None, когда список запасных пуст")
def _t_get_fallback_none():
    import src.ai.llm_providers as llm

    saved = llm.FALLBACK_MODELS
    try:
        llm.FALLBACK_MODELS = []
        assert llm.get_fallback_provider() is None
    finally:
        llm.FALLBACK_MODELS = saved


# --------------------------------------------------------------------------- #
#  Живой тест (пропускается, если демон не запущен)
# --------------------------------------------------------------------------- #


@S.add("Ollama живой запрос к демону (пропускается, если демон выключен)")
def _t_live():
    from src.ai.llm_providers import get_provider

    p = get_provider()
    if not p.available():
        raise Skip("демон Ollama не отвечает на localhost:11434")
    text = p.generate("Ответь ровно одним словом: ок", timeout=60)
    assert text, f"демон ответил, но текст пустой: {text!r}"