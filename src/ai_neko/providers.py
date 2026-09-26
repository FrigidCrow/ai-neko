"""Small OpenAI-compatible streaming adapter; graph owns all tool decisions."""

from __future__ import annotations

import asyncio
import json

import httpx

from ai_neko.tools import network


class ProviderError(RuntimeError):
    """Only stable safe codes/messages may cross the service/UI boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def http_error(status: int) -> ProviderError:
    if status in {401, 403}:
        return ProviderError("authentication_failed", "服务鉴权失败，请检查 API Key。")
    if status == 429:
        return ProviderError("rate_limited", "服务请求受限，请稍后重试。")
    return ProviderError("provider_http_error", "服务返回错误，请检查配置或稍后重试。")


class ModelAdapter:
    MAX_BYTES = 1_000_000
    MAX_TEXT = 80_000
    TIMEOUT = 120

    def __init__(self, config: dict, api_key: str | None):
        self.config = dict(config)
        self._key = api_key

    async def stream(self, messages: list, tools: list | None = None):
        model = self.config.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ProviderError("model_not_configured", "请先填写模型名称和模型服务。")
        base = self.config.get("model_base_url", "https://api.openai.com/v1")
        try:
            url = network.parse_url(base)
            if not self._key and not network.explicit_loopback(url.host):
                raise ProviderError("model_key_missing", "请先配置模型 API Key。")
            target, headers, extensions = await network.pin_url(
                str(url).rstrip("/") + "/chat/completions",
                allow_local=True,
            )
            headers["Accept"] = "text/event-stream"
            if self._key:
                headers["Authorization"] = "Bearer " + self._key
            payload = {
                "model": model,
                "messages": messages,
                "stream": True,
                "max_completion_tokens": 4096,
            }
            if url.host == "api.deepseek.com":
                # Official DeepSeek Chat Completions uses the legacy token field.
                payload["max_tokens"] = payload.pop("max_completion_tokens")
            if tools:
                payload.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
            async with asyncio.timeout(self.TIMEOUT):
                async with network.client() as client, client.stream(
                    "POST", target, json=payload, headers=headers, extensions=extensions
                ) as response:
                    if response.status_code != 200:
                        raise http_error(response.status_code)
                    if response.headers.get("content-encoding", "identity") not in {
                        "",
                        "identity",
                    }:
                        raise ProviderError("invalid_response", "模型流格式不受支持。")
                    calls: dict[int, dict] = {}
                    reasoning = ""
                    total_text = 0
                    finished = False
                    async for data in self._events(response):
                        if data == "[DONE]":
                            finished = True
                            break
                        packet = json.loads(data)
                        if not isinstance(packet, dict) or packet.get("error"):
                            raise ValueError
                        choices = packet.get("choices", [])
                        if not choices:
                            continue
                        choice = choices[0]
                        if choice.get("finish_reason") == "length":
                            raise ProviderError(
                                "output_limit", "模型输出达到长度上限，请缩小问题范围。"
                            )
                        delta = choice.get("delta", {})
                        thought = delta.get("reasoning_content")
                        if thought:
                            if not isinstance(thought, str):
                                raise ValueError
                            reasoning += thought
                            if len(reasoning) > self.MAX_TEXT:
                                raise ProviderError("output_limit", "模型输出达到长度上限。")
                        content = delta.get("content")
                        if content:
                            if not isinstance(content, str):
                                raise ValueError
                            total_text += len(content)
                            if total_text > self.MAX_TEXT:
                                raise ProviderError("output_limit", "模型输出达到长度上限。")
                            yield {"type": "text", "text": content}
                        for part in delta.get("tool_calls") or []:
                            index = part["index"]
                            if type(index) is not int or not 0 <= index < 8:
                                raise ValueError
                            call = calls.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            function = part.get("function", {})
                            for key, value in (
                                ("id", part.get("id", "")),
                                ("name", function.get("name", "")),
                                ("arguments", function.get("arguments", "")),
                            ):
                                if not isinstance(value, str):
                                    raise ValueError
                                call[key] += value
                                if len(call[key]) > (16_384 if key == "arguments" else 128):
                                    raise ValueError
                    if not finished:
                        raise ProviderError("stream_interrupted", "模型连接中断，请重试。")
                    if reasoning and calls:
                        # Protocol-only context: graph keeps it in memory, never emits it.
                        yield {"type": "assistant_context", "reasoning_content": reasoning}
                    for index in sorted(calls):
                        call = calls[index]
                        arguments = json.loads(call["arguments"])
                        if (
                            not call["id"]
                            or not call["name"]
                            or not isinstance(arguments, dict)
                        ):
                            raise ValueError
                        yield {"type": "tool_call", **call, "arguments": arguments}
        except ProviderError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderError("timeout", "模型请求超时，请重试。") from None
        except network.NetworkPolicyError:
            raise ProviderError("blocked_endpoint", "模型地址不可访问或不符合网络策略。") from None
        except httpx.HTTPError:
            raise ProviderError("network_error", "无法连接模型服务，请检查网络。") from None
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            raise ProviderError("invalid_response", "模型返回格式不正确。") from None

    async def _events(self, response):
        buffer = b""
        size = 0
        data = []
        async for chunk in response.aiter_raw():
            size += len(chunk)
            if size > self.MAX_BYTES:
                raise ProviderError("output_limit", "模型输出达到长度上限。")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.rstrip(b"\r")
                if len(line) > 65_536:
                    raise ValueError
                if not line:
                    if data:
                        yield "\n".join(data)
                        data = []
                elif line.startswith(b"data:"):
                    data.append(line[5:].lstrip(b" ").decode("utf-8"))
            if len(buffer) > 65_536:
                raise ValueError
        # An unterminated frame is a truncated stream, never a successful completion.
