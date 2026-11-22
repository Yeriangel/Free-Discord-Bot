"""AI providers besides Claude: ChatGPT (OpenAI) and Grok (xAI).

Both speak the OpenAI "chat completions" format. The chat cog builds its requests in Anthropic's message format
(system + messages with tool_use / tool_result blocks), and this module translates them both ways, so tools,
confirmations, history and cost tracking work the same for every provider.
"""

import json
import logging
from types import SimpleNamespace

import httpx

log = logging.getLogger("botd.ai")

PROVIDERS = {
    "claude": {"name": "Claude", "secret": "anthropic_key"},
    "openai": {"name": "ChatGPT", "secret": "openai_key", "base": "https://api.openai.com/v1", "strict": True,
               "max_field": "max_completion_tokens",
               "models": {"luna": "gpt-6-luna", "terra": "gpt-6.1-terra", "sol": "gpt-6.2-sol", "astra": "gpt-6.2-astra"}},
    "grok": {"name": "Grok", "secret": "xai_key", "base": "https://api.x.ai/v1", "strict": False, "max_field": "max_tokens",
             "models": {"grok-4.3": "grok-4.3", "grok-4.7": "grok-4.7"}},
}
# list prices per million tokens (input, output), for the cost estimate only
PRICES = {
    "gpt-6-luna": (0.10, 0.50), "gpt-6.1-terra": (1.0, 5.0), "gpt-6.2-sol": (2.0, 10.0), "gpt-6.2-astra": (8.0, 40.0),
    "grok-4.3": (1.25, 2.50), "grok-4.7": (2.0, 6.0),
}


class ProviderError(Exception):
    def __init__(self, kind: str, status: int = 0, detail: str = ""):
        super().__init__(f"{kind} {status} {detail}".strip())
        self.kind, self.status, self.detail = kind, status, detail  # kind: auth | permission | rate | status | connection


def model_for(provider: str, cfg: dict) -> str:
    custom = str(cfg.get("custom_model") or "").strip()
    if custom:
        return custom
    p = PROVIDERS[provider]
    key = cfg.get("openai_model" if provider == "openai" else "grok_model")
    return p["models"].get(key) or next(iter(p["models"].values()))


def _blocks(content) -> list:
    """Anthropic content (str, SDK blocks, dicts or our SimpleNamespaces) as a list of dicts."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for b in content or []:
        if isinstance(b, dict):
            out.append(b)
        elif hasattr(b, "model_dump"):
            out.append(b.model_dump())
        else:
            out.append(dict(vars(b)))
    return out


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        blocks = _blocks(m["content"])
        if m["role"] == "assistant":
            text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in blocks if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if results:
            for r in results:
                content = r.get("content")
                out.append({"role": "tool", "tool_call_id": r["tool_use_id"],
                            "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)})
        else:
            out.append({"role": "user", "content": "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")})
    return out


def to_openai_tools(tools: list[dict], strict: bool) -> list[dict]:
    out = []
    for t in tools:
        fn = {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}
        if strict:
            fn["strict"] = True
        out.append({"type": "function", "function": fn})
    return out


def from_openai_response(data: dict, model: str) -> SimpleNamespace:
    """OpenAI-style completion -> an object shaped like an Anthropic message (content, stop_reason, usage, model)."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = []
    if msg.get("content"):
        content.append(SimpleNamespace(type="text", text=msg["content"]))
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        content.append(SimpleNamespace(type="tool_use", id=call.get("id", ""), name=fn.get("name", ""),
                                       input=args if isinstance(args, dict) else {}))
    finish = choice.get("finish_reason")
    stop = {"tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}.get(finish, "end_turn")
    if msg.get("refusal") and not msg.get("content"):
        stop = "refusal"
    usage = data.get("usage") or {}
    return SimpleNamespace(content=content, stop_reason=stop, model=data.get("model") or model,
                           usage=SimpleNamespace(input_tokens=int(usage.get("prompt_tokens") or 0),
                                                 output_tokens=int(usage.get("completion_tokens") or 0)))


async def chat(provider: str, key: str, model: str, system: str, messages: list[dict], tools: list[dict],
               max_tokens: int, base_url: str | None = None) -> SimpleNamespace:
    p = PROVIDERS[provider]
    body = {"model": model, "messages": to_openai_messages(system, messages), p["max_field"]: max_tokens}
    if tools:
        body["tools"] = to_openai_tools(tools, p["strict"])
    url = (base_url or p["base"]).rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise ProviderError("connection", 0, type(exc).__name__) from None
    if resp.status_code == 401:
        raise ProviderError("auth", 401)
    if resp.status_code == 403:
        raise ProviderError("permission", 403)
    if resp.status_code == 429:
        raise ProviderError("rate", 429)
    if resp.status_code >= 400:
        try:
            detail = (resp.json().get("error") or {}).get("message", "")
        except ValueError:
            detail = ""
        raise ProviderError("status", resp.status_code, str(detail)[:300])
    return from_openai_response(resp.json(), model)


async def test_key(provider: str, key: str) -> bool:
    """True if the key works (lists models, which costs nothing)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(PROVIDERS[provider]["base"] + "/models", headers={"Authorization": f"Bearer {key}"})
    if resp.status_code in (401, 403):
        return False
    resp.raise_for_status()
    return True
