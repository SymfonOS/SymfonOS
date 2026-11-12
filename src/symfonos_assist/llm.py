"""Lokale LLM-Backends: Ollama (Standard) oder llama.cpp `llama-server` (OpenAI-kompatibel)."""
from __future__ import annotations

import http.client
import json
import os
import tomllib
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

CONF = [Path("/etc/symfonos/assist.toml"),
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "symfonos/assist.toml"]


@dataclass
class Settings:
    backend: str = "ollama"                   # ollama | llamacpp
    url: str = "http://127.0.0.1:11434"
    model: str = "symfonos-assist"            # aus /usr/share/symfonos-assist/Modelfile erzeugt
    temperature: float = 0.2
    context: int = 8192

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        for f in CONF:
            if f.exists():
                for k, v in tomllib.loads(f.read_text()).items():
                    if hasattr(s, k):
                        setattr(s, k, v)
        for k in ("backend", "url", "model"):
            if os.environ.get(f"SYMFONOS_ASSIST_{k.upper()}"):
                setattr(s, k, os.environ[f"SYMFONOS_ASSIST_{k.upper()}"])
        if not _is_local(s.url):
            raise ValueError("Der Assistent darf nur lokale Modelle verwenden (127.0.0.1/localhost)")
        return s


def _is_local(url: str) -> bool:
    """Nur Modelle auf diesem Rechner: der Host muss exakt lokal sein (kein »localhost@fremd.example«)."""
    if url.startswith("unix:"):
        return True
    try:
        u = urllib.parse.urlsplit(url)
        host = u.hostname
        u.port  # ungültiger Port → ValueError
    except ValueError:
        return False
    if u.scheme != "http" or u.username is not None or u.password is not None or not host:
        return False
    return host in ("localhost", "::1") or (host.startswith("127.") and host.replace(".", "").isdigit()
                                             and host.count(".") == 3)


class LLMError(Exception):
    pass


def _post(url: str, payload: dict, timeout: int = 300):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        raise LLMError(f"Lokales Modell nicht erreichbar ({url}): {e}. "
                       f"Läuft der Dienst? `systemctl status {_service(url)}`; "
                       f"Modell einrichten: `sudo symfonos-assist-model`") from e


def _service(url: str) -> str:
    return "ollama" if ":11434" in url else "symfonos-assist-llm"


def chat(s: Settings, messages: list[dict]) -> Iterator[str]:
    """Streamt die Antwort tokenweise. Abbrüche und kaputte Antworten werden zu LLMError."""
    try:
        yield from _chat(s, messages)
    except LLMError:
        raise
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, http.client.HTTPException) as e:
        raise LLMError(f"Antwort des lokalen Modells unvollständig oder ungültig: {e}") from e


def _chat(s: Settings, messages: list[dict]) -> Iterator[str]:
    if s.backend == "ollama":
        r = _post(f"{s.url}/api/chat", {"model": s.model, "messages": messages, "stream": True,
                                        "options": {"temperature": s.temperature, "num_ctx": s.context}})
        for line in r:
            if not line.strip():
                continue
            d = json.loads(line)
            if "error" in d:
                raise LLMError(d["error"])
            yield d.get("message", {}).get("content", "")
            if d.get("done"):
                break
    else:
        r = _post(f"{s.url}/v1/chat/completions", {"model": s.model, "messages": messages,
                                                  "stream": True, "temperature": s.temperature})
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            d = json.loads(line[5:])
            yield d["choices"][0].get("delta", {}).get("content") or ""


def available(s: Settings) -> bool:
    path = "/api/tags" if s.backend == "ollama" else "/v1/models"
    try:
        with urllib.request.urlopen(s.url + path, timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False
