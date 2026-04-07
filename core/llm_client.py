"""
LM Studio client for ContractIQ.
Connects to LM Studio's OpenAI-compatible local API.
Base URL and timeout are read from config.json at the project root.
"""

import json
import requests
from requests.adapters import HTTPAdapter
from pathlib import Path
from typing import Optional, List, Dict, Any

# Tuple timeout for all LLM calls: (connect_timeout, read_timeout).
# read_timeout is the max seconds of *silence* between data chunks.
# A dead connection (e.g. after sleep or network drop) is detected
# when no data arrives for this long — even if the overall budget is larger.
_CONNECT_TIMEOUT = 30   # seconds to establish the TCP connection
_READ_TIMEOUT    = 600  # seconds of inactivity before treating connection as dead

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class LMStudioClient:
    """
    Thin wrapper around LM Studio's local OpenAI-compatible API.
    Defaults are read from config.json; constructor args override them.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None):
        cfg = _load_config()
        self.base_url = (
            base_url or cfg.get("lm_studio_base_url", "http://localhost:1234")
        ).rstrip("/")
        self.timeout = timeout if timeout is not None else cfg.get("lm_studio_timeout", 3600)
        self.api_url = f"{self.base_url}/v1/chat/completions"
        self.models_url = f"{self.base_url}/v1/models"
        self._session = requests.Session()

    def health_check(self) -> bool:
        try:
            resp = requests.get(self.models_url, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            resp = requests.get(self.models_url, timeout=5)
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception:
            return []

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        system_prompt: Optional[str] = None,
        context_label: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Send a chat request to LM Studio. Returns the assistant's text response."""
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        chars = sum(len(m.get("content", "")) for m in messages)

        try:
            resp = self._session.post(
                self.api_url,
                json=payload,
                # Tuple timeout: (connect_timeout, read_timeout).
                # read_timeout catches dead connections after sleep/network drop —
                # if no data arrives for _READ_TIMEOUT seconds the request fails.
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Lost connection to LM Studio at {self.base_url}. "
                "This may be caused by the computer sleeping or a network interruption. "
                "Please ensure LM Studio is running and re-analyse the document."
            )
        except requests.exceptions.Timeout:
            label = context_label or "request"
            raise TimeoutError(
                f"Lost connection to LM Studio on {label} — no data received for "
                f"{_READ_TIMEOUT}s. The prompt was {chars:,} chars. "
                "This is usually caused by the computer sleeping or a network interruption. "
                "Please ensure LM Studio is running and re-analyse the document."
            )
        except Exception as e:
            raise RuntimeError(f"LM Studio API error: {str(e)}")

    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        context_label: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Convenience wrapper: single prompt → response string."""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, temperature, max_tokens, system_prompt or None,
                         context_label=context_label, timeout=timeout)

    def complete_json(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.05,
        max_tokens: int = 4096,
        context_label: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send a prompt and parse the JSON response.
        Handles common LLM formatting issues (markdown fences etc).
        """
        full_system = (
            (system_prompt + "\n\n" if system_prompt else "") +
            "CRITICAL: Respond ONLY with valid JSON. No explanation, no markdown, no code fences. "
            "Start your response with { and end with }."
        )
        raw = self.complete(prompt, full_system, temperature, max_tokens,
                            context_label=context_label, timeout=timeout)
        return self._parse_json_response(raw)

    def _parse_json_response(self, raw: str) -> Dict[str, Any]:
        """Robustly parse JSON from LLM output."""
        text = raw.strip()

        # Strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        text = text.strip()

        # Find JSON object boundaries
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": "JSON parse failed", "raw_response": raw[:2000]}
