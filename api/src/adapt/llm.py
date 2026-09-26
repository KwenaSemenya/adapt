"""The only place ADAPT talks to Claude.

Every call asks for JSON matching a schema, then validates it with Pydantic.
Two failed validations fail the step with a plain-language cause; the caller
persists that so a retry resumes from the same step.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .db import log_event, tx

T = TypeVar("T", bound=BaseModel)

# Sonnet 5 list prices, USD per million tokens. Used only to report run cost.
PRICE_PER_MTOK = {"claude-sonnet-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0)}


class StepError(Exception):
    """A pipeline step failed. `message` is shown to the reviewer as-is."""

    def __init__(self, message: str, *, event: str = "step_failed", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.event = event
        self.detail = detail


@dataclass
class CallContext:
    step: str
    run_id: str | None = None
    variant_id: str | None = None
    market: str | None = None


# ---------- test hooks ----------
# ADAPT_FORCE_FAIL="uk:score" makes the next UK scoring call fail once, to prove
# retry-from-step works. Ignored in production.
_forced_lock = threading.Lock()
_forced_used: set[str] = set()


def _forced_failure(ctx: CallContext) -> bool:
    if os.environ.get("ADAPT_ENV") == "production":
        return False
    spec = os.environ.get("ADAPT_FORCE_FAIL", "")
    key = f"{ctx.market}:{ctx.step}"
    if key not in {s.strip() for s in spec.split(",") if s.strip()}:
        return False
    with _forced_lock:
        if key in _forced_used:
            return False
        _forced_used.add(key)
        return True


def reset_forced_failures() -> None:
    with _forced_lock:
        _forced_used.clear()


# ---------- transport ----------

Transport = Callable[[str, str, dict], tuple[str, dict]]
_transport_override: Transport | None = None


def set_transport(fn: Transport | None) -> None:
    """Tests swap in a fake model here. fn(system, user, schema) -> (json_text, usage)."""
    global _transport_override
    _transport_override = fn


_client: anthropic.Anthropic | None = None


def model_id() -> str:
    model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if not model:
        raise StepError("No model is configured. Set ANTHROPIC_MODEL, then retry.", event="config_missing")
    return model


def _anthropic_transport(system: str, user: str, schema: dict) -> tuple[str, dict]:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise StepError("No API key is configured. Set ANTHROPIC_API_KEY, then retry.", event="config_missing")
        # Pin the public endpoint: the host shell may export ANTHROPIC_BASE_URL for other tools.
        _client = anthropic.Anthropic(
            api_key=key,
            base_url=os.environ.get("ADAPT_ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            timeout=float(os.environ.get("ADAPT_LLM_TIMEOUT", "90")),
            max_retries=2,
        )
    response = _client.messages.create(
        model=model_id(),
        max_tokens=16000,
        output_config={
            "effort": os.environ.get("ADAPT_EFFORT", "medium"),
            "format": {"type": "json_schema", "schema": schema},
        },
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise StepError("The model declined this request. Edit the brief and try again.", event="refusal")
    if response.stop_reason == "max_tokens":
        raise StepError("The model's answer was cut off. Retry this step.", event="max_tokens")
    text = next((b.text for b in response.content if b.type == "text"), "")
    usage = {
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return text, usage


def call_json(
    system: str,
    user: str,
    out: type[T],
    ctx: CallContext,
    *,
    check: Callable[[T], str | None] | None = None,
    attempts: int = 2,
) -> T:
    """Call the model for JSON matching `out`. Raises StepError after `attempts` bad outputs.

    `check` returns a problem string when the JSON is well-formed but wrong (e.g. a
    missing rubric criterion); that counts as a failed validation too.
    """
    schema = strict_schema(out)
    transport = _transport_override or _anthropic_transport
    last_problem = ""
    for attempt in range(1, attempts + 1):
        if _forced_failure(ctx):
            raise StepError(
                f"{ctx.step.capitalize()} stopped. The service didn't answer in time (forced for testing).",
                event="forced_failure",
            )
        try:
            text, usage = transport(system, user, schema)
        except StepError:
            raise
        except anthropic.APITimeoutError:
            raise StepError(f"{ctx.step.capitalize()} stopped. The AI service didn't answer in time.", event="timeout")
        except anthropic.RateLimitError:
            raise StepError(f"{ctx.step.capitalize()} stopped. The AI service is busy right now.", event="rate_limited")
        except anthropic.AuthenticationError:
            raise StepError("The AI service rejected the API key. Check ANTHROPIC_API_KEY.", event="auth")
        except anthropic.APIStatusError as e:
            raise StepError(
                f"{ctx.step.capitalize()} stopped. The AI service returned an error ({e.status_code}).",
                event="api_error", detail=str(e.message)[:300],
            )
        except anthropic.APIConnectionError:
            raise StepError(f"{ctx.step.capitalize()} stopped. Couldn't reach the AI service.", event="connection")

        with tx() as conn:
            log_event(conn, "llm_call", run_id=ctx.run_id, variant_id=ctx.variant_id, market=ctx.market,
                      step=ctx.step, attempt=attempt, **usage)
        try:
            parsed = out.model_validate(json.loads(text))
            problem = check(parsed) if check else None
            if not problem:
                return parsed
            last_problem = problem
        except (json.JSONDecodeError, ValidationError) as e:
            last_problem = str(e)[:500]
        if last_problem:
            with tx() as conn:
                log_event(conn, "json_invalid", level="warn", run_id=ctx.run_id, variant_id=ctx.variant_id,
                          market=ctx.market, step=ctx.step, attempt=attempt, problem=last_problem)
    raise StepError(
        f"{ctx.step.capitalize()} stopped. The AI's answer didn't match the expected format twice.",
        event="json_invalid_twice", detail=last_problem,
    )


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema, inlined and closed, in the shape structured outputs expects."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            node = {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            # Structured outputs don't support length/range keywords; Pydantic still enforces them.
            for k in ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"):
                node.pop(k, None)
            return node
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, outp = PRICE_PER_MTOK.get(model, PRICE_PER_MTOK["claude-sonnet-5"])
    return (input_tokens * inp + output_tokens * outp) / 1_000_000
