"""Local Ollama transport and scripted model responses."""

import json
import subprocess
from collections.abc import Iterable
from typing import Protocol

from ..utils.contracts import decode_json


class ModelClient(Protocol):
    name: str

    def complete(self, prompt: str, request: bytes, *, timeout: float) -> bytes:
        """Return bounded response bytes. Enforce timeout at the transport."""


class ModelClientError(Exception):
    """A transport or provider failure with a reportable reason."""


class OllamaClient:
    url = "http://127.0.0.1:11434/api/chat"
    options = {"num_ctx": 8192, "num_predict": 2048, "num_gpu": 0, "temperature": 0, "seed": 42}
    settings = {**options, "deadline_seconds": 240.0, "http_response_bytes": 65_536,
                "think": 0, "truncate": 0, "shift": 0}

    def __init__(self, model: str = "qwen3:4b"):
        self.model = model
        self.name = f"ollama:{model}"

    def complete(self, prompt: str, request: bytes, *, timeout: float) -> bytes:
        payload = {
            "model": self.model, "stream": False, "think": False, "truncate": False, "shift": False,
            "format": decode_json(request)["response_schema"], "options": self.options,
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": request.decode()}],
        }
        command = [
            "curl", "--disable", "--silent", "--fail", "--noproxy", "*",
            "--max-time", f"{timeout:.3f}", "--max-filesize", str(self.settings["http_response_bytes"]),
            "--write-out", "\n%{http_code}", "--json", "@-", self.url,
        ]
        try:
            result = subprocess.run(command, input=json.dumps(payload).encode(), capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError from exc
        except FileNotFoundError as exc:
            raise ModelClientError("curl_unavailable") from exc

        body, _, http_status = result.stdout.rpartition(b"\n")
        if result.returncode == 28:
            raise TimeoutError
        if result.returncode:
            reason = {7: "ollama_unavailable", 22: "ollama_http_error", 63: "response_too_large"}
            raise ModelClientError("model_context_limit" if http_status == b"413"
                                   else reason.get(result.returncode, "ollama_transport_error"))
        if http_status != b"200":
            raise ModelClientError("ollama_http_error")
        if len(body) > self.settings["http_response_bytes"]:
            raise ModelClientError("response_too_large")
        try:
            response = decode_json(body)
            content = response["message"]["content"]
            if response.get("model") != self.model or response.get("remote_model") or response.get("remote_host"):
                raise ModelClientError("unexpected_model")
            if response.get("done") is not True or response.get("done_reason") != "stop":
                raise ModelClientError("truncated_response")
            if not isinstance(content, str):
                raise TypeError("Expected string content")
            return content.encode()
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ModelClientError("invalid_ollama_response") from exc


class FakeClient:
    name = "fake"

    def __init__(self, events: Iterable[dict] | None = None):
        self.events = None if events is None else iter(events)

    def complete(self, prompt: str, request: bytes, *, timeout: float) -> bytes:
        event = {"event": "valid"} if self.events is None else next(self.events, {"event": "exhausted"})
        kind = event["event"]
        if kind == "timeout":
            raise TimeoutError
        if kind == "raw_response":
            return event["body"].encode()
        if kind not in {"valid", "invented_citation", "contradictory_claim"}:
            raise ValueError("Unavailable fake response")
        request = decode_json(request)
        rows = [{
            "rule": item["rule"], "evidence_id": item["evidence_id"], "quote": item["effective"]["text"],
            "expected_route": item["effective"]["text"].rsplit("->", 1)[-1].strip(),
            "follow_up": event.get("follow_up", "none"),
        } for item in request["rules"]]
        if kind == "invented_citation":
            rows[0].update(rule=event["rule"], evidence_id=f'{event["artifact"]}@{event["version"]}:{event["location"]}')
        if kind == "contradictory_claim":
            row = next(item for item in rows if item["rule"] == event["rule"])
            row["expected_route"] = event["claim"].rstrip(".").split()[-1]
        return json.dumps({"request_id": request["request_id"], "rows": rows}).encode()
