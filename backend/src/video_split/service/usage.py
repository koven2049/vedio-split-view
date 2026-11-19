"""Usage ledger: record billed ASR / LLM calls and summarize them with cost.

Attribution uses a context variable: pipelines and request handlers call
``set_owner`` and every call recorded later in that context (including
asyncio tasks spawned from it) is billed to that user.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.config import ModelPrice, get_settings

logger = logging.getLogger(__name__)

_owner: ContextVar[tuple[int | None, int | None]] = ContextVar(
    "usage_owner", default=(None, None)
)

# Official 百炼 (华北2 北京) list prices in CNY, checked 2026-11-19.
# qwen-audio-3.1 bills tokens: input = audio tokens, output = text tokens.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "fun-asr": ModelPrice(per_second=0.00022),
    "paraformer-v2": ModelPrice(per_second=0.00008),
    "qwen3-asr-flash-filetrans": ModelPrice(per_second=0.00022),
    "qwen-audio-3.0-asr-flash-filetrans": ModelPrice(per_second=0.00022),
    "qwen-audio-3.1-asr-flash-filetrans": ModelPrice(input_per_m=0.8, output_per_m=2.7),
}


def set_owner(user_id: int | None, task_id: int | None = None) -> None:
    _owner.set((user_id, task_id))


async def record(
    kind: str,
    model: str,
    *,
    seconds: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """Append one ledger row. Never raises — accounting must not fail a job."""
    user_id, task_id = _owner.get()
    try:
        from video_split.database import _get_session_factory
        from video_split.models import UsageRecord

        async with _get_session_factory()() as session:
            session.add(UsageRecord(
                user_id=user_id, task_id=task_id, kind=kind, model=model,
                seconds=float(seconds or 0.0),
                prompt_tokens=int(prompt_tokens or 0),
                completion_tokens=int(completion_tokens or 0),
                total_tokens=int(total_tokens or 0),
            ))
            await session.commit()
    except Exception:
        logger.warning("[usage] failed to record %s usage for %s", kind, model, exc_info=True)


async def record_llm_response(model: str, response: dict[str, Any]) -> None:
    usage = response.get("usage") if isinstance(response, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or prompt + completion
    await record("llm", model, prompt_tokens=prompt, completion_tokens=completion,
                 total_tokens=total)


def _prices() -> dict[str, ModelPrice]:
    merged = {k.lower(): v for k, v in DEFAULT_PRICES.items()}
    merged.update({k.lower(): v for k, v in get_settings().pricing.items()})
    return merged


def _cost(price: ModelPrice | None, seconds: float, prompt: int, completion: int) -> float | None:
    if price is None:
        return None
    return (
        price.per_second * seconds
        + price.input_per_m * prompt / 1_000_000
        + price.output_per_m * completion / 1_000_000
    )


async def summarize(db: AsyncSession, user: Any) -> dict[str, Any]:
    """Per-model totals for ``user`` with estimated CNY cost.

    Unattributed rows (calls made outside any owner context) count toward
    admins — they run the API keys that pay for them.
    """
    from video_split.models import UsageRecord

    cond = UsageRecord.user_id == user.id
    if getattr(user, "role", "") == "admin":
        cond = or_(cond, UsageRecord.user_id.is_(None))
    rows = (await db.execute(select(UsageRecord).where(cond))).scalars().all()

    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        a = acc.setdefault((r.kind, r.model), {
            "model": r.model, "seconds": 0.0, "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "requests": 0,
        })
        a["seconds"] += r.seconds
        a["prompt_tokens"] += r.prompt_tokens
        a["completion_tokens"] += r.completion_tokens
        a["total_tokens"] += r.total_tokens
        a["requests"] += 1

    prices = _prices()
    asr: list[dict[str, Any]] = []
    llm: list[dict[str, Any]] = []
    total_cost: float | None = None
    for (kind, model), a in sorted(acc.items()):
        cost = _cost(prices.get(model.lower()), a["seconds"], a["prompt_tokens"],
                     a["completion_tokens"])
        if cost is not None:
            total_cost = (total_cost or 0.0) + cost
        row = {"model": model, "requests": a["requests"],
               "cost": None if cost is None else round(cost, 4)}
        if kind == "asr":
            asr.append({**row, "total_seconds": round(a["seconds"], 1)})
        else:
            llm.append({**row, "prompt_tokens": a["prompt_tokens"],
                        "completion_tokens": a["completion_tokens"],
                        "total_tokens": a["total_tokens"]})
    return {
        "asr": asr,
        "llm": llm,
        "total_cost": None if total_cost is None else round(total_cost, 4),
    }
