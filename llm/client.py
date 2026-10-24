import os
import re
import json

from llm.providers import providers, Provider, meterless_status, NVIDIA_MODELS

DEFAULT_MODEL, DEFAULT_FALLBACK = NVIDIA_MODELS


class LLMError(RuntimeError):
    pass


def sample_mode_default() -> bool:
    return os.getenv("SAMPLE_MODE", "true").lower() == "true"


def live_available() -> bool:
    return bool(providers())


def route() -> str:
    chain = providers()
    return " then ".join(p.name for p in chain) if chain else "none configured"


def models() -> list[str]:
    return [m for p in providers() for m in p.models] or [DEFAULT_MODEL]


def power_status() -> dict:
    return {"providers": [{"name": p.name, "base_url": p.base_url, "models": p.models} for p in providers()],
            "meterless": {k: v for k, v in meterless_status().items() if k != "token"}}


# Sample mode: deterministic stand-ins so the demo runs offline

def _sample_triage(p: dict) -> dict:
    brand = p.get("brand", "")
    words = brand.split()
    aliases = [brand]
    # Deliberately simple: odd spellings are left to the brand resolver and the Critic
    if len(words) >= 2:
        aliases.append(" ".join(words[:2]))
        initials = "".join(w[0] for w in words if w[0].isupper())
        if len(initials) >= 2:
            aliases.append(initials)
    return {"brand_aliases": list(dict.fromkeys(a for a in aliases if a)), "search_strategy": "rules (sample mode)"}


def _sample_critic(p: dict) -> dict:
    # No reviewer model offline: the Critic falls back to its rule verdicts alone
    return {"verdicts": [], "missing_aliases": []}


def _sample_reporter(p: dict) -> dict:
    n_pull, n_checks = p.get("pull_count", 0), p.get("check_count", 0)
    n_agencies, n_dist, cases = p.get("agency_count", 0), p.get("dist_count", 0), p.get("cases", 0)
    parts = []
    if n_pull:
        parts.append(f"Pull {n_pull} item{'s' if n_pull != 1 else ''} ({cases:g} units still in the warehouse) now")
    if n_checks:
        parts.append(f"{n_checks} item{'s need' if n_checks != 1 else ' needs'} a person to read the physical label")
    if n_agencies:
        parts.append(f"{n_dist} shipment{'s' if n_dist != 1 else ''} already went to {n_agencies} partner "
                     f"agenc{'ies' if n_agencies != 1 else 'y'}; notices are drafted")
    if not parts:
        parts.append("No stock or shipments match this recall")
    return {"verdict_summary": ". ".join(parts) + "."}


_SAMPLE = {"triage": _sample_triage, "critic": _sample_critic, "reporter": _sample_reporter}


# Live mode: Nemotron through Meterless Runtime or NVIDIA's API, both OpenAI-compatible

def _client(provider: Provider):
    from openai import OpenAI
    return OpenAI(api_key=provider.api_key, base_url=provider.base_url, timeout=provider.timeout, max_retries=1,
                  default_headers=provider.headers)


def _create(provider: Provider, model: str, **kwargs):
    return _client(provider).chat.completions.create(
        model=model, extra_headers=provider.chat_headers, extra_body=provider.extra_body or None, **kwargs)


def _label(provider: Provider, model: str, served: str | None = None) -> str:
    # Routing modes such as meterless/auto pick the real model at request time; record what actually answered
    name = model if not served or served == model else f"{model} ({served})"
    return name if provider.name == "NVIDIA API" else f"{name} via {provider.name}"


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def _call(provider: Provider, model: str, system: str, user: str) -> tuple[dict, str]:
    resp = _create(provider, model,
                   messages=[{"role": "system", "content": system + " Respond with a single JSON object and nothing else."},
                             {"role": "user", "content": user}],
                   temperature=0.1, max_tokens=1500)
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise ValueError("answer cut off at the token limit")
    return _extract_json(choice.message.content), getattr(resp, "model", None) or model


def call_llm(agent: str, system: str, user: str, payload: dict | None = None,
             sample: bool | None = None) -> tuple[dict, str]:
    if sample if sample is not None else sample_mode_default():
        return _SAMPLE[agent](payload or {}), "rules (sample mode)"
    chain = providers()
    errors = []
    # Each provider in turn, each of its models in turn
    for provider in chain:
        for model in provider.models:
            try:
                answer, served = _call(provider, model, system, user)
                return answer, _label(provider, model, served)
            except Exception as exc:
                errors.append(f"{_label(provider, model)}: {str(exc)[:160]}")
    if not chain:
        errors.append("no provider configured (Meterless Runtime offline, no NVIDIA key)")
    if os.getenv("LLM_FALLBACK", "true").lower() == "true":
        # Every decision is made by rules anyway; carry on without the model and say so in the report
        return _SAMPLE[agent](payload or {}), f"rules (Nemotron unavailable: {errors[-1][:120]})"
    raise LLMError(f"{agent} could not get a valid answer from any provider. " + " | ".join(errors))


def run_tools(system: str, user: str, tools: list[dict], handlers: dict, max_steps: int = 10) -> tuple[dict, str, list[dict]]:
    """Tool loop: run the model's tool calls on the data layer until it answers."""
    chain = providers()
    if not chain:
        raise LLMError("no model provider configured")
    errors = []
    for provider in chain:
        for model in provider.models:
            try:
                answer, calls = _tool_loop(provider, model, system, user, tools, handlers, max_steps)
                return answer, _label(provider, model), calls
            except Exception as exc:
                errors.append(f"{_label(provider, model)}: {str(exc)[:160]}")
    raise LLMError(" | ".join(errors))


def _tool_loop(provider: Provider, model: str, system: str, user: str, tools: list[dict], handlers: dict,
               max_steps: int) -> tuple[dict, list[dict]]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    calls = []
    for _ in range(max_steps):
        msg = _create(provider, model, messages=messages, tools=tools, temperature=0, max_tokens=2000).choices[0].message
        if not msg.tool_calls:
            return _extract_json(msg.content), calls
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                        for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = handlers[tc.function.name](**args)
            except Exception as exc:
                result = {"error": str(exc)[:200]}
            calls.append({"tool": tc.function.name, "args": tc.function.arguments})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)[:4000]})
    raise LLMError(f"tool loop did not finish in {max_steps} steps")


def test_connection() -> list[dict]:
    """Send one short request through each configured provider; the UI's Test connection action."""
    import time
    out = []
    for provider in providers():
        t = time.time()
        try:
            resp = _create(provider, provider.models[0], max_tokens=20,
                           messages=[{"role": "user", "content": "Reply with one word: ready"}])
            out.append({"provider": provider.name, "ok": True, "model": provider.models[0],
                        "served_by": getattr(resp, "model", None), "seconds": round(time.time() - t, 2)})
        except Exception as exc:
            out.append({"provider": provider.name, "ok": False, "model": provider.models[0], "error": str(exc)[:160]})
    return out
