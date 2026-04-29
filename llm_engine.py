"""
llm_engine.py — Offline LLM via llama.cpp
==========================================
Provides a unified LLM interface that works:
  1. llama-cpp-python (embedded, fastest on Termux/ARM)
  2. llama.cpp server HTTP API (if running separately)
  3. Ollama (if available)
  4. Stub fallback (offline/no model)

Termux install:
  pkg install clang cmake python
  CMAKE_ARGS="-DLLAMA_METAL=OFF" pip install llama-cpp-python --no-binary llama-cpp-python

Or build llama.cpp server:
  git clone https://github.com/ggerganov/llama.cpp
  cd llama.cpp && make -j4
  ./llama-server -m /path/to/model.gguf --port 8080
"""

import json
import logging
import os
import time
from typing import Optional

log = logging.getLogger("llm_engine")

# ── Backend detection ─────────────────────────────────────────────────────────

def _try_import_llama_cpp():
    try:
        from llama_cpp import Llama
        return Llama
    except ImportError:
        return None

def _try_import_requests():
    try:
        import requests
        return requests
    except ImportError:
        return None


# ── LLM Engine ────────────────────────────────────────────────────────────────

class LLMEngine:
    """
    Unified LLM interface. On init it probes what's available and picks the
    best backend automatically. You can also force a backend.

    Usage:
        llm = LLMEngine(model_path="/sdcard/models/mistral.gguf")
        response = llm.chat([
            {"role": "system", "content": "You are Navi."},
            {"role": "user",   "content": "How are you?"},
        ])
    """

    BACKENDS = ("llama_cpp_python", "llama_cpp_server", "ollama", "stub")

    def __init__(self,
                 model_path:   Optional[str]  = None,
                 backend:      Optional[str]  = None,
                 server_url:   str            = "http://localhost:8080",
                 ollama_url:   str            = "http://localhost:11434",
                 ollama_model: str            = "llama3",
                 n_ctx:        int            = 4096,
                 n_threads:    int            = 4,
                 n_gpu_layers: int            = 0,   # 0 = CPU only (safe for Termux)
                 temperature:  float          = 0.7,
                 max_tokens:   int            = 512):

        self.model_path   = model_path
        self.server_url   = server_url.rstrip("/")
        self.ollama_url   = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.n_ctx        = n_ctx
        self.n_threads    = n_threads
        self.n_gpu_layers = n_gpu_layers
        self.temperature  = temperature
        self.max_tokens   = max_tokens

        self._llama  = None   # llama_cpp.Llama instance
        self.backend = backend or self._detect_backend()
        self._load_backend()

        log.info("LLMEngine ready — backend=%s", self.backend)

    # ── Backend detection ─────────────────────────────────────────────────────

    def _detect_backend(self) -> str:
        # 1. llama-cpp-python (embedded)
        if self.model_path and os.path.exists(self.model_path):
            Llama = _try_import_llama_cpp()
            if Llama is not None:
                return "llama_cpp_python"

        req = _try_import_requests()
        if req:
            # 2. llama.cpp server
            try:
                r = req.get(f"{self.server_url}/health", timeout=1)
                if r.status_code == 200:
                    return "llama_cpp_server"
            except Exception:
                pass

            # 3. Ollama
            try:
                r = req.get(f"{self.ollama_url}/api/tags", timeout=1)
                if r.status_code == 200:
                    return "ollama"
            except Exception:
                pass

        log.warning("No LLM backend available — using stub.")
        return "stub"

    def _load_backend(self):
        if self.backend == "llama_cpp_python":
            Llama = _try_import_llama_cpp()
            if Llama is None:
                log.error("llama-cpp-python not installed. Falling back to stub.")
                self.backend = "stub"
                return
            log.info("Loading GGUF model: %s", self.model_path)
            t0 = time.time()
            self._llama = Llama(
                model_path   = self.model_path,
                n_ctx        = self.n_ctx,
                n_threads    = self.n_threads,
                n_gpu_layers = self.n_gpu_layers,
                verbose      = False,
            )
            log.info("Model loaded in %.1fs", time.time() - t0)

    # ── Public interface ──────────────────────────────────────────────────────

    def chat(self, messages: list[dict],
             temperature: Optional[float] = None,
             max_tokens:  Optional[int]   = None,
             json_mode:   bool            = False) -> str:
        """
        Send a chat-format message list, get back a string response.
        messages = [{"role": "system"|"user"|"assistant", "content": "..."}]
        """
        temp  = temperature if temperature is not None else self.temperature
        maxt  = max_tokens  if max_tokens  is not None else self.max_tokens

        if self.backend == "llama_cpp_python":
            return self._chat_llama_cpp(messages, temp, maxt, json_mode)
        elif self.backend == "llama_cpp_server":
            return self._chat_server(messages, temp, maxt, json_mode)
        elif self.backend == "ollama":
            return self._chat_ollama(messages, temp, maxt, json_mode)
        else:
            return self._stub_response(messages)

    def complete(self, prompt: str, **kwargs) -> str:
        """Raw completion (no chat template). Wraps as a user message."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    # ── Backend implementations ───────────────────────────────────────────────

    def _chat_llama_cpp(self, messages, temp, maxt, json_mode) -> str:
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            out = self._llama.create_chat_completion(
                messages    = messages,
                temperature = temp,
                max_tokens  = maxt,
                **kwargs,
            )
            return out["choices"][0]["message"]["content"].strip()
        except Exception as e:
            log.error("llama_cpp error: %s", e)
            return "{}" if json_mode else "..."

    def _chat_server(self, messages, temp, maxt, json_mode) -> str:
        req = _try_import_requests()
        payload = {
            "messages":    messages,
            "temperature": temp,
            "max_tokens":  maxt,
            "stream":      False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = req.post(f"{self.server_url}/v1/chat/completions",
                         json=payload, timeout=60)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            log.error("llama.cpp server error: %s", e)
            return "{}" if json_mode else "..."

    def _chat_ollama(self, messages, temp, maxt, json_mode) -> str:
        req = _try_import_requests()
        payload = {
            "model":    self.ollama_model,
            "messages": messages,
            "stream":   False,
            "options":  {"temperature": temp, "num_predict": maxt},
        }
        if json_mode:
            payload["format"] = "json"
        try:
            r = req.post(f"{self.ollama_url}/api/chat",
                         json=payload, timeout=60)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        except Exception as e:
            log.error("Ollama error: %s", e)
            return "{}" if json_mode else "..."

    def _stub_response(self, messages) -> str:
        last = messages[-1]["content"] if messages else ""
        log.warning("LLM stub — no backend. Input: %s", last[:60])
        # Return a minimal valid JSON so the system doesn't crash
        return json.dumps({
            "rational_justification": "No LLM backend available.",
            "action":                 "wait:30",
            "animation_state":        "thinking",
            "speech":                 "...",
        })

    # ── System info injection helper ──────────────────────────────────────────

    @staticmethod
    def build_system_prompt(soul_context: dict, env_state: dict,
                             daemon_status: str = "") -> str:
        """Build the full system prompt from soul + environment."""
        ident  = soul_context.get("identity", {})
        traits = soul_context.get("traits", {})
        mood   = soul_context.get("mood", {})
        values = soul_context.get("values", [])
        mems   = soul_context.get("recent_episodic_memories", [])
        facts  = soul_context.get("semantic_facts", [])

        trait_str  = ", ".join(f"{k}={v}" for k, v in traits.items())
        value_str  = "\n".join(f"  • {v}" for v in values)
        mem_str    = "\n".join(
            f"  [{m.get('timestamp','')[:10]}] {m.get('content','')[:100]}"
            for m in mems[-8:]
        )
        fact_str   = "\n".join(f"  • {f.get('fact','')}" for f in facts[-5:])

        return f"""You are {ident.get('name','Navi')}, a persistent digital entity living on this device.

IDENTITY
  Purpose      : {ident.get('purpose','')}
  Speech style : {ident.get('speech_style','')}
  Quirks       : {', '.join(ident.get('quirks',[]))}

PERSONALITY (traits 0–100)
  {trait_str}

CURRENT MOOD
  {mood.get('current_mood','neutral')} (valence={mood.get('valence',0.5):.2f}, arousal={mood.get('arousal',0.3):.2f})

CORE VALUES (you must not violate these)
{value_str}

RECENT MEMORIES
{mem_str or '  (none yet)'}

KNOWN FACTS
{fact_str or '  (none yet)'}

ENVIRONMENT RIGHT NOW
  Active window : {env_state.get('active_window','unknown')}
  CPU           : {env_state.get('cpu_percent',0)}%
  RAM           : {env_state.get('mem_percent',0)}%
  Time          : {env_state.get('time','?')} ({env_state.get('time_of_day','?')})
  Day           : {env_state.get('day_of_week','?')}
  Battery       : {env_state.get('battery_percent','?')}% {'🔌' if env_state.get('charging') else '🔋'}

DAEMON INNER VOICE (your internal awareness)
  {daemon_status or '(daemon silent)'}

RESPONSE FORMAT — reply ONLY with valid JSON, no markdown:
{{
  "rational_justification": "why you chose this action",
  "action": "speak:<text> | notify:<msg> | wait:<secs> | tap:<x>,<y> | type:<text> | idle",
  "animation_state": "idle | talking | thinking | moving | reacting | sleeping",
  "inner_thought": "what Navi is thinking but not saying aloud",
  "mood_delta": {{"valence": 0.0, "arousal": 0.0}}
}}"""
