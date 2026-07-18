"""
Единый транспортный модуль для вызова LLM.

Два режима (определяется автоматически через config.LLM_MODE):
  - cloud: Alibaba GLM / OpenRouter / любой OpenAI-compatible endpoint с API-ключом
  - local: LM Studio / Ollama на localhost без авторизации (backward compat)

Конструкция:
  from core.ollama_service import generate
  result = await generate(messages=[...], model=..., temperature=..., max_tokens=...)
  result = {"content": "...", "model": "...", "usage": {...}} или raise LLMError

Override для дашборда (отдельная облачная LLM):
  from core.config import DASHBOARD_LLM_API_KEY, DASHBOARD_LLM_BASE_URL, DASHBOARD_MODEL_NAME
  result = await generate(messages=[...], api_key=DASHBOARD_LLM_API_KEY,
                           base_url=DASHBOARD_LLM_BASE_URL, model=DASHBOARD_MODEL_NAME)

Retry: 3 попытки с exponential backoff (1s, 2s, 4s).
Timeout: 30 сек (connect=10, read=30).

Важно: base_url БЕЗ /v1 — service сам добавляет /v1/chat/completions.
"""
import asyncio
import logging
import httpx

from core.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODE, LOCAL_AI_ENDPOINT, MODEL_NAME

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────
DEFAULT_TIMEOUT = 30  # seconds (task spec)
DEFAULT_TEMPERATURE = 0.02
DEFAULT_MAX_TOKENS = 1200
MAX_RETRIES = 3
RETRY_DELAYS = [1.0, 2.0, 4.0]  # exponential backoff


class LLMError(Exception):
    """All LLM transport failures (timeout, 5xx, network, no key in cloud mode)."""

    def __init__(self, message: str, *, status: int | None = None, attempt: int = 0):
        super().__init__(message)
        self.status = status
        self.attempt = attempt


# ── URL & headers resolution ──────────────────────────────
def _resolve_base_url(base_url: str = "") -> str:
    """
    Return base_url WITHOUT trailing /v1 or /chat/completions.
    The caller appends /v1/chat/completions itself.

    Args:
        base_url: explicit override (e.g. from DASHBOARD_LLM_BASE_URL).
                  If empty, falls back to config.LLM_BASE_URL → LOCAL_AI_ENDPOINT.
    """
    if base_url:
        return base_url.rstrip("/").removesuffix("/v1")
    if LLM_BASE_URL:
        return LLM_BASE_URL.rstrip("/").removesuffix("/v1")

    # Derive from LOCAL_AI_ENDPOINT (old config, still present)
    url = LOCAL_AI_ENDPOINT.rstrip("/")
    # Strip known OpenAI-compatible path suffixes
    for suffix in ("/chat/completions", "/v1/chat/completions", "/v1"):
        url = url.removesuffix(suffix)
    return url


def _build_headers(api_key: str = "") -> dict:
    """Build HTTP headers. Authorization only if key is provided."""
    h = {"Content-Type": "application/json"}
    key = api_key or LLM_API_KEY
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _endpoint_url(base_url: str = "") -> str:
    """Full POST URL: base_url + /v1/chat/completions."""
    return f"{_resolve_base_url(base_url)}/v1/chat/completions"


# ── Core generate() ───────────────────────────────────────
async def generate(
    messages: list[dict],
    *,
    model: str = "",
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT,
    retry: bool = True,
    api_key: str = "",
    base_url: str = "",
) -> dict:
    """
    Send a chat-completions request and return the parsed response.

    Args:
        messages: OpenAI messages list (system/user/assistant, may contain
            image_url parts for vision models).
        model: model name (defaults to config.MODEL_NAME).
        temperature: sampling temperature.
        max_tokens: max completion tokens.
        timeout: per-attempt timeout in seconds.
        retry: if True (default), retry on transient failures up to MAX_RETRIES.
        api_key: override API key (для дашборда — DASHBOARD_LLM_API_KEY).
        base_url: override base URL (для дашборда — DASHBOARD_LLM_BASE_URL).

    Returns:
        {"content": str, "model": str, "usage": dict, "raw": dict}

    Raises:
        LLMError: on persistent failure (all retries exhausted, or non-retryable).
    """
    effective_key = api_key or LLM_API_KEY
    is_override = bool(api_key)

    # Если передан api_key — режим cloud принудительно (для дашборда).
    # Иначе проверяем стандартную логику config.
    if is_override:
        if not effective_key:
            raise LLMError("api_key override is empty")
    elif LLM_MODE == "cloud" and not LLM_API_KEY:
        raise LLMError("LLM_MODE=cloud but LLM_API_KEY is empty")

    payload = {
        "model": model or MODEL_NAME,
        "temperature": temperature,
        "stream": False,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    headers = _build_headers(effective_key)
    url = _endpoint_url(base_url)
    httpx_timeout = httpx.Timeout(connect=10.0, read=float(timeout), write=60.0, pool=10.0)

    last_exc: Exception | None = None
    attempts = MAX_RETRIES if retry else 1

    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=httpx_timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)

            # Retry on 5xx and 429; 4xx (except 429) is non-retryable
            if resp.status_code >= 500 or resp.status_code == 429:
                try:
                    err_body = resp.json().get("error", {}).get("message", resp.text[:300])
                except Exception:
                    err_body = resp.text[:300]
                last_exc = LLMError(
                    f"HTTP {resp.status_code}: {err_body}",
                    status=resp.status_code,
                    attempt=attempt,
                )
                logger.warning("LLM attempt %d/%d failed: HTTP %d", attempt, attempts, resp.status_code)
                if attempt < attempts:
                    await asyncio.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])
                continue

            if resp.status_code != 200:
                try:
                    err_body = resp.json().get("error", {}).get("message", resp.text[:300])
                except Exception:
                    err_body = resp.text[:300]
                raise LLMError(
                    f"HTTP {resp.status_code}: {err_body}",
                    status=resp.status_code,
                    attempt=attempt,
                )

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            used_model = data.get("model", payload["model"])

            logger.info(
                "LLM OK: model=%s, tokens=%s, attempt=%d",
                used_model,
                usage.get("total_tokens", "?"),
                attempt,
            )
            return {
                "content": content,
                "model": used_model,
                "usage": usage,
                "raw": data,
            }

        except httpx.ReadTimeout:
            last_exc = LLMError(f"LLM timeout ({timeout}s)", attempt=attempt)
            logger.warning("LLM attempt %d/%d: ReadTimeout", attempt, attempts)
            if attempt < attempts:
                await asyncio.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])
            continue

        except httpx.ConnectError as e:
            last_exc = LLMError(f"Connect error: {e}", attempt=attempt)
            logger.warning("LLM attempt %d/%d: ConnectError: %s", attempt, attempts, e)
            if attempt < attempts:
                await asyncio.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])
            continue

        except LLMError:
            raise  # non-retryable 4xx already raised above

        except Exception as e:
            last_exc = LLMError(f"Unexpected: {e}", attempt=attempt)
            logger.exception("LLM attempt %d/%d: unexpected error", attempt, attempts)
            if attempt < attempts:
                await asyncio.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])
            continue

    raise last_exc or LLMError("All retries exhausted")


# ── Health check (optional, for diagnostics) ──────────────
async def health_check(base_url: str = "", api_key: str = "") -> bool:
    """Quick GET /v1/models to verify endpoint is reachable."""
    try:
        base = _resolve_base_url(base_url)
        headers = _build_headers(api_key)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base}/v1/models", headers=headers)
        return resp.status_code == 200
    except Exception as e:
        logger.debug("health_check failed: %s", e)
        return False


# ── Module info (for logging at startup) ──────────────────
def info() -> dict:
    """Return current LLM configuration (safe — no key)."""
    return {
        "mode": LLM_MODE,
        "base_url": _resolve_base_url(),
        "endpoint": _endpoint_url(),
        "model": MODEL_NAME,
        "has_key": bool(LLM_API_KEY),
    }


def dashboard_info() -> dict:
    """Return dashboard LLM configuration (safe — no key)."""
    from core.config import DASHBOARD_LLM_API_KEY, DASHBOARD_LLM_BASE_URL, DASHBOARD_MODEL_NAME
    return {
        "mode": "cloud" if DASHBOARD_LLM_API_KEY else "disabled",
        "base_url": _resolve_base_url(DASHBOARD_LLM_BASE_URL),
        "endpoint": _endpoint_url(DASHBOARD_LLM_BASE_URL),
        "model": DASHBOARD_MODEL_NAME,
        "has_key": bool(DASHBOARD_LLM_API_KEY),
    }
