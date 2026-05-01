import json
import os

import litellm
from litellm import AuthenticationError, BadRequestError, PermissionDeniedError

DEFAULT_MODEL_CHAIN = [
    "gemini/gemini-2.5-flash-lite",
    "gemini/gemini-2.5-flash",
    "gemini/gemini-3.1-flash-lite-preview",
    "gemini/gemini-3-flash-preview",
]

def _load_model_chain() -> list[str]:
    raw = os.getenv("LLM_MODEL_CHAIN")
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    single = os.getenv("LLM_MODEL_NAME")
    if single:
        return [single]
    return DEFAULT_MODEL_CHAIN

MODEL_CHAIN = _load_model_chain()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL_NAME", "gemini/gemini-embedding-001")

_api_key = os.getenv("LLM_API_KEY")
if _api_key:
    os.environ.setdefault("GEMINI_API_KEY", _api_key)


async def call_llm(
    system_prompt: str,
    user_message: str,
    response_format: str = "json",
    response_schema: dict | None = None,
) -> dict | str:
    """Single LLM call with fallback chain. Returns parsed JSON dict or raw string.

    When response_schema is provided, it is passed to the provider as a
    server-side decoding constraint (Gemini response_schema / OpenAI json_schema).
    The model is forced to produce tokens matching the schema, eliminating
    parse failures for structured calls like coverage and SMS.
    """

    rf_param: dict | str | None = None
    if response_schema:
        rf_param = {
            "type": "json_object",
            "response_schema": response_schema,
        }

    async def _attempt(model: str):
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        }
        if rf_param:
            kwargs["response_format"] = rf_param
        response = await litellm.acompletion(**kwargs)
        content = response.choices[0].message.content
        if response_format != "json":
            return content
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)

    _NO_RETRY = (AuthenticationError, PermissionDeniedError, BadRequestError)

    last_err: Exception | None = None
    for model in MODEL_CHAIN:
        try:
            return await _attempt(model)
        except _NO_RETRY:
            raise
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("No models available in chain")


async def call_llm_with_state(
    system_prompt: str,
    extracted_fields: dict,
    last_agent_reply: str | None,
    user_text: str,
    system_note: str | None = None,
) -> dict:
    """Intake LLM call using a state snapshot instead of full conversation history.
    Prompt size is O(1) in turn count."""
    known = "\n".join(
        f"- {k}: {v if v is not None else '(not yet provided)'}"
        for k, v in extracted_fields.items()
    )
    prev = f"\nYOUR PREVIOUS REPLY: {last_agent_reply}\n" if last_agent_reply else ""
    note = f"\nSYSTEM NOTE (address this on your next turn): {system_note}\n" if system_note else ""
    user_message = f"KNOWN SO FAR:\n{known}\n{prev}{note}CUSTOMER: {user_text}"
    return await call_llm(system_prompt, user_message, response_format="json")


async def get_embedding(text: str) -> list[float]:
    """Single-text embedding via the configured embedding model. No fallback (fixed model)."""
    response = await litellm.aembedding(
        model=EMBEDDING_MODEL,
        input=[text],
    )
    return response.data[0]["embedding"]


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch embeddings. Falls back to per-text calls if the provider doesn't support batching."""
    try:
        response = await litellm.aembedding(model=EMBEDDING_MODEL, input=texts)
        return [d["embedding"] for d in response.data]
    except Exception:
        return [await get_embedding(t) for t in texts]
