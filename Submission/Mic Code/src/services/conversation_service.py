"""Bounded, fully local LLM dialogue with deterministic fallback replies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import Callable, Iterable
import urllib.error
import urllib.request

try:
    from llama_cpp import Llama
except ImportError:  # pragma: no cover - optional native dependency on non-Pi hosts
    Llama = None


@dataclass(frozen=True)
class ConversationReply:
    text: str
    provider: str
    fallback_reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


LOCAL_REPLIES = (
    "My local language model is unavailable, but my core robot systems are still ready.",
    "I cannot reach my local reasoning model right now. You can still ask me to follow, explore, remember, or report status.",
    "My conversational model is resting, while my wheels, sensors, vision, and memory remain operational.",
)


def _speech_text(value: str, max_words: int = 45, max_chars: int = 400) -> str:
    """Turn model output into short, safe-to-synthesize plain speech."""
    text = re.sub(r"<think>.*?</think>", " ", value or "", flags=re.IGNORECASE | re.DOTALL)
    if "</think>" in text.lower():
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    text = re.sub(r"<think>.*$", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", "a web link", text)
    text = re.sub(r"[*_`#>]", "", text)
    text = " ".join(text.split()).strip(" -:\t\r\n")
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]).rstrip(".,;:") + "."
    return text[:max_chars].strip()


class ConversationService:
    """Owns one local model and serializes all conversational inference."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        server_url: str = "",
        model: str = "qwen2.5-1.5b-instruct",
        timeout: float = 20.0,
        context_size: int = 2048,
        max_tokens: int = 96,
        threads: int | None = None,
        api_key: str | None = None,  # retained for compatibility; local inference needs no key
        llm_factory: Callable[..., object] | None = None,
        urlopen: Callable[..., object] | None = None,
    ):
        del api_key
        self.model_path = Path(model_path) if model_path else None
        self.server_url = server_url.strip().rstrip("/")
        if self.server_url.endswith("/v1"):
            self.server_url = self.server_url[:-3]
        self.model = model
        self.timeout = min(120.0, max(2.0, float(timeout)))
        self.context_size = min(8192, max(512, int(context_size)))
        self.max_tokens = min(256, max(16, int(max_tokens)))
        default_threads = min(4, max(1, (os.cpu_count() or 2) - 1))
        self.threads = min(16, max(1, int(threads or default_threads)))
        self._llm_factory = llm_factory or Llama
        self._urlopen = urlopen or urllib.request.urlopen

        self._llm = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._inference_lock = threading.Lock()
        self._load_thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self.last_error = "not initialized"
        self.backend = "unavailable"

    @property
    def configured(self) -> bool:
        return bool(self.server_url or (self.model_path and self.model_path.is_file()))

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and not self._stop.is_set()

    @property
    def loading(self) -> bool:
        return bool(self._load_thread and self._load_thread.is_alive() and not self.ready)

    def start(self) -> None:
        if self.ready or (self._load_thread and self._load_thread.is_alive()):
            return
        self._stop.clear()
        self._ready.clear()
        self._load_thread = threading.Thread(
            target=self._initialize_backend, name="local-llm-loader", daemon=True
        )
        self._load_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._ready.clear()
        if self._load_thread and self._load_thread.is_alive():
            self._load_thread.join(timeout=2.0)
        inference_stopped = self._inference_lock.acquire(timeout=3.0)
        with self._state_lock:
            llm = self._llm if inference_stopped else None
            if inference_stopped:
                self._llm = None
            self.backend = "unavailable"
        close = getattr(llm, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        if inference_stopped:
            self._inference_lock.release()

    def _initialize_backend(self) -> None:
        try:
            if self.server_url:
                request = urllib.request.Request(f"{self.server_url}/v1/models", method="GET")
                with self._urlopen(request, timeout=min(3.0, self.timeout)) as response:
                    if int(getattr(response, "status", 200)) >= 400:
                        raise RuntimeError(f"local LLM server returned HTTP {response.status}")
                backend = "llama.cpp-server"
                llm = None
            else:
                if self._llm_factory is None:
                    raise RuntimeError("llama-cpp-python is not installed")
                if self.model_path is None or not self.model_path.is_file():
                    raise RuntimeError(f"local LLM model missing: {self.model_path or 'not configured'}")
                llm = self._llm_factory(
                    model_path=str(self.model_path),
                    n_ctx=self.context_size,
                    n_batch=min(256, self.context_size),
                    n_threads=self.threads,
                    n_threads_batch=self.threads,
                    n_gpu_layers=0,
                    use_mmap=True,
                    verbose=False,
                )
                backend = "llama-cpp-python"
            if self._stop.is_set():
                close = getattr(llm, "close", None)
                if callable(close):
                    close()
                return
            with self._state_lock:
                self._llm = llm
                self.backend = backend
                self.last_error = ""
                self._ready.set()
        except Exception as exc:
            with self._state_lock:
                self.last_error = str(exc)[:240]
                self.backend = "unavailable"
                self._ready.clear()

    def local_reply(self, reason: str = "local LLM unavailable") -> ConversationReply:
        return ConversationReply(random.choice(LOCAL_REPLIES), "local", reason)

    @staticmethod
    def _messages(
        user_text: str,
        user_name: str | None,
        memories: Iterable[str],
        recent_interactions: Iterable[dict],
    ) -> list[dict[str, str]]:
        memory_text = "; ".join(str(item)[:160] for item in list(memories)[:5]) or "none"
        history: list[dict[str, str]] = []
        for entry in list(recent_interactions)[-3:]:
            if entry.get("input") and entry.get("response"):
                history.extend(
                    [
                        {"role": "user", "content": str(entry["input"])[:240]},
                        {"role": "assistant", "content": str(entry["response"])[:240]},
                    ]
                )
        system = (
            "You are AURUS, a warm, curious voice assistant embodied in a small mecanum rover. "
            "Answer the user directly in natural spoken language using no more than 45 words. "
            "Return only the words to speak: no markdown, stage directions, hidden reasoning, or JSON. "
            "You cannot operate hardware or call tools. Never claim that you moved, sensed, saw, "
            "recognized, or stored anything unless the supplied context explicitly says so. "
            f"Current person: {user_name or 'unknown'}. Stored facts about them: {memory_text}."
        )
        return [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": user_text[:800]},
        ]

    def _python_completion(self, messages: list[dict[str, str]]) -> str:
        with self._state_lock:
            llm = self._llm
        if llm is None:
            raise RuntimeError("local model is not loaded")
        deadline = time.monotonic() + self.timeout
        chunks = llm.create_chat_completion(
            messages=messages,
            temperature=0.65,
            top_p=0.9,
            repeat_penalty=1.08,
            max_tokens=self.max_tokens,
            stream=True,
        )
        output: list[str] = []
        for chunk in chunks:
            if self._stop.is_set() or time.monotonic() >= deadline:
                break
            choices = chunk.get("choices") or []
            if not choices:
                continue
            content = (choices[0].get("delta") or {}).get("content")
            if content:
                output.append(str(content))
        if self._stop.is_set():
            raise RuntimeError("local LLM generation stopped")
        if time.monotonic() >= deadline:
            raise TimeoutError("local LLM generation timed out")
        return "".join(output)

    def _server_completion(self, messages: list[dict[str, str]]) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": 0.65,
                "top_p": 0.9,
                "max_tokens": self.max_tokens,
                "stream": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.server_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer no-key"},
            method="POST",
        )
        with self._urlopen(request, timeout=self.timeout) as response:
            if int(getattr(response, "status", 200)) >= 400:
                raise RuntimeError(f"local LLM server returned HTTP {response.status}")
            body = json.loads(response.read().decode("utf-8"))
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("local LLM server returned no choices")
        return str((choices[0].get("message") or {}).get("content") or "")

    def reply(
        self,
        user_text: str,
        user_name: str | None = None,
        memories: Iterable[str] = (),
        recent_interactions: Iterable[dict] = (),
    ) -> ConversationReply:
        clean_input = " ".join((user_text or "").split())[:800]
        if not clean_input:
            return self.local_reply("empty conversation input")
        if not self.ready:
            if self.configured and not self.loading and not self._stop.is_set():
                self.start()
            reason = "local LLM is loading" if self.loading else self.last_error or "local LLM unavailable"
            return self.local_reply(reason)
        if not self._inference_lock.acquire(blocking=False):
            return self.local_reply("local LLM is already answering another request")

        try:
            messages = self._messages(clean_input, user_name, memories, recent_interactions)
            raw = (
                self._server_completion(messages)
                if self.backend == "llama.cpp-server"
                else self._python_completion(messages)
            )
            text = _speech_text(raw)
            if not text:
                raise RuntimeError("local LLM returned an empty response")
            self.last_error = ""
            return ConversationReply(text, self.backend)
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            self.last_error = str(exc)[:240]
            return self.local_reply(self.last_error)
        except Exception as exc:
            self.last_error = f"local LLM failed: {exc}"[:240]
            return self.local_reply(self.last_error)
        finally:
            if self._stop.is_set():
                with self._state_lock:
                    llm = self._llm
                    self._llm = None
                close = getattr(llm, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            self._inference_lock.release()
