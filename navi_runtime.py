"""
navi_runtime.py — Main Runtime for Navi on Termux/XFCE
========================================================
Orchestrates:
  • SoulFile  — persistent identity/memory/values
  • DaemonEngine — background maintenance
  • LLMEngine    — offline GGUF or server LLM
  • HTTP server  — serves avatar UI + state API
  • Cognitive loop — sense → decide → act → learn
  • Environment interaction — xdotool, input events, device info

Run:
  python navi_runtime.py --soul navi.soul --model /sdcard/models/mistral-7b.gguf

On first run with no .soul:
  python navi_runtime.py --name "Navi" --model /sdcard/models/mistral-7b.gguf
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

# ── Local modules ─────────────────────────────────────────────────────────────
from soul_format  import SoulFile
from daemon_engine import DaemonEngine
from llm_engine   import LLMEngine

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_PATH = Path.home() / ".navi" / "runtime.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("navi")

# ── Global shared state (written by cognitive loop, read by HTTP server) ──────
_shared_state: dict = {
    "log_id":          None,
    "animation_state": "idle",
    "speech":          "",
    "inner_thought":   "",
    "traits":          {},
    "entity_name":     "Navi",
    "vrm_url":         None,
}
_state_lock   = threading.Lock()
_daemon_msg   = ""          # last daemon inner-voice message
_failure_log: list = []     # (pattern, count)


# ── Environment sensing ───────────────────────────────────────────────────────

def get_env_state() -> dict:
    state = {
        "active_window": _get_active_window(),
        "time":          datetime.now().strftime("%H:%M"),
        "day_of_week":   datetime.now().strftime("%A"),
        "time_of_day":   _time_of_day(),
    }

    # psutil (CPU / RAM)
    try:
        import psutil
        state["cpu_percent"] = psutil.cpu_percent(interval=0.3)
        state["mem_percent"] = psutil.virtual_memory().percent
        # Battery (Android via psutil)
        bat = psutil.sensors_battery()
        if bat:
            state["battery_percent"] = round(bat.percent, 1)
            state["charging"]        = bat.power_plugged
    except Exception:
        state["cpu_percent"]     = 0
        state["mem_percent"]     = 0
        state["battery_percent"] = "?"
        state["charging"]        = False

    # Android-specific: screen on/off via dumpsys
    try:
        out = subprocess.check_output(
            ["dumpsys", "power"], text=True, timeout=2
        )
        state["screen_on"] = "mWakefulness=Awake" in out
    except Exception:
        state["screen_on"] = True

    return state


def _get_active_window() -> str:
    # Try xdotool (X11/XFCE in Termux)
    try:
        wid  = subprocess.check_output(["xdotool","getactivewindow"], text=True, timeout=2).strip()
        name = subprocess.check_output(["xdotool","getwindowname",wid], text=True, timeout=2).strip()
        return name
    except Exception:
        pass
    # Fallback: Android foreground app via dumpsys
    try:
        out = subprocess.check_output(
            ["dumpsys","activity","activities"], text=True, timeout=2
        )
        for line in out.splitlines():
            if "mResumedActivity" in line or "topResumedActivity" in line:
                return line.strip()
    except Exception:
        pass
    return "unknown"


def _time_of_day() -> str:
    h = datetime.now().hour
    return ("morning"   if 5  <= h < 12 else
            "afternoon" if 12 <= h < 17 else
            "evening"   if 17 <= h < 21 else "night")


# ── Action executor ───────────────────────────────────────────────────────────

def execute_action(action: str) -> tuple[str, Optional[str]]:
    """
    Parse and run an action. Returns (outcome, failure_reason).

    Supported verbs:
      speak:<text>        — update speech bubble
      notify:<msg>        — Android/desktop notification
      wait:<secs>         — sleep
      tap:<x>,<y>         — simulate screen tap (Android: input tap; X11: xdotool)
      type:<text>         — type text into focused window
      minimize:active     — minimize active X11 window
      swipe:<x1,y1,x2,y2,dur> — Android swipe gesture
      open:<app>          — launch an app
      idle                — do nothing
    """
    parts = action.split(":", 1)
    verb  = parts[0].strip().lower()
    arg   = parts[1].strip() if len(parts) > 1 else ""

    pattern = f"{verb}:{arg}"
    if _is_known_failure(pattern):
        return "skipped_known_failure", f"Known failure: {pattern}"

    try:
        # ── speak ──────────────────────────────────────────────────────────
        if verb == "speak":
            with _state_lock:
                _shared_state["speech"]          = arg
                _shared_state["animation_state"] = "talking"
            log.info("Navi speaks: %s", arg)
            return "success", None

        # ── notify ─────────────────────────────────────────────────────────
        elif verb == "notify":
            # Try Android toast first, then notify-send for XFCE
            try:
                subprocess.run(
                    ["termux-toast", "-s", arg], timeout=3
                )
            except Exception:
                subprocess.run(
                    ["notify-send", "Navi", arg, "--icon=dialog-information"],
                    timeout=3
                )
            return "success", None

        # ── tap ────────────────────────────────────────────────────────────
        elif verb == "tap":
            coords = arg.split(",")
            if len(coords) < 2:
                return "failure", "tap requires x,y"
            x, y = int(coords[0]), int(coords[1])

            # Try Android input tap first
            try:
                subprocess.run(["input", "tap", str(x), str(y)], timeout=3)
                return "success", None
            except Exception:
                pass
            # Fall back to xdotool mousemove+click
            subprocess.run(["xdotool", "mousemove", str(x), str(y)], timeout=3)
            subprocess.run(["xdotool", "click", "1"], timeout=3)
            return "success", None

        # ── type ───────────────────────────────────────────────────────────
        elif verb == "type":
            try:
                subprocess.run(["xdotool", "type", "--clearmodifiers", arg], timeout=5)
            except Exception:
                # Android: use input text (limited)
                subprocess.run(["input", "text", arg.replace(" ", "%s")], timeout=5)
            return "success", None

        # ── swipe ──────────────────────────────────────────────────────────
        elif verb == "swipe":
            # arg = "x1,y1,x2,y2,duration_ms"
            coords = arg.split(",")
            if len(coords) < 4:
                return "failure", "swipe requires x1,y1,x2,y2[,dur]"
            x1,y1,x2,y2 = coords[:4]
            dur = coords[4] if len(coords) > 4 else "300"
            subprocess.run(
                ["input","swipe",x1,y1,x2,y2,dur], timeout=5
            )
            return "success", None

        # ── minimize ───────────────────────────────────────────────────────
        elif verb == "minimize":
            try:
                wid = subprocess.check_output(
                    ["xdotool","getactivewindow"], text=True, timeout=2
                ).strip()
                subprocess.run(["xdotool","windowminimize",wid], timeout=3)
                return "success", None
            except Exception as e:
                _record_failure(pattern)
                return "failure", str(e)

        # ── open ───────────────────────────────────────────────────────────
        elif verb == "open":
            subprocess.Popen(arg.split(), start_new_session=True)
            return "success", None

        # ── wait ───────────────────────────────────────────────────────────
        elif verb == "wait":
            secs = float(arg) if arg else 10.0
            time.sleep(min(secs, 120))
            return "success", None

        # ── idle ───────────────────────────────────────────────────────────
        elif verb == "idle":
            return "success", None

        else:
            _record_failure(pattern)
            return "failure", f"Unknown verb: {verb}"

    except subprocess.TimeoutExpired:
        _record_failure(pattern)
        return "failure", f"Timeout: {pattern}"
    except Exception as e:
        _record_failure(pattern)
        return "failure", str(e)


def _is_known_failure(pattern: str) -> bool:
    for fp in _failure_log:
        if fp["pattern"] == pattern and fp["count"] >= 2:
            return True
    return False


def _record_failure(pattern: str):
    for fp in _failure_log:
        if fp["pattern"] == pattern:
            fp["count"] += 1
            return
    _failure_log.append({"pattern": pattern, "count": 1, "last_seen": _now()})


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ── Cognitive loop ────────────────────────────────────────────────────────────

class CognitiveLoop:
    def __init__(self, soul: SoulFile, llm: LLMEngine, daemon: DaemonEngine,
                 cycle_secs: int = 45):
        self.soul       = soul
        self.llm        = llm
        self.daemon     = daemon
        self.cycle_secs = cycle_secs
        self._log_id    = 0
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CognitiveLoop")
        self._thread.start()
        log.info("Cognitive loop started (interval=%ds)", self.cycle_secs)

    def _loop(self):
        global _daemon_msg
        while True:
            try:
                self._cycle()
            except Exception as e:
                log.exception("Cognitive cycle error: %s", e)
            time.sleep(self.cycle_secs)

    def _cycle(self):
        global _daemon_msg

        # 1. Sense
        env      = get_env_state()
        ctx      = self.soul.llm_context_block()
        d_status = _daemon_msg or self.daemon.request_inner_status()

        # 2. Build system prompt
        system   = LLMEngine.build_system_prompt(ctx, env, d_status)

        # 3. Hesitance gate
        traits   = ctx["traits"]
        hes      = traits.get("Hesitance", 10.0)
        delay    = (hes / 100.0) * 15.0
        # Double delay if recently dismissed
        ep_mems  = self.soul.episodic_memories()
        recent   = ep_mems[-20:] if ep_mems else []
        dismissed_recently = any(
            m.get("type") == "dismissed"
            for m in recent
        )
        if dismissed_recently:
            delay *= 2.0
        if delay > 1.0:
            log.info("Hesitance delay: %.1fs", delay)
            time.sleep(delay)

        # 4. Decide
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": "What do you do right now?"},
        ]
        raw = self.llm.chat(messages, json_mode=True)
        try:
            decision = json.loads(raw)
        except json.JSONDecodeError:
            log.error("LLM returned invalid JSON: %s", raw[:200])
            decision = {
                "rational_justification": "Parse error",
                "action":                 "idle",
                "animation_state":        "thinking",
                "inner_thought":          "",
                "mood_delta":             {"valence": 0.0, "arousal": 0.0},
            }

        action     = decision.get("action", "idle")
        anim       = decision.get("animation_state", "idle")
        inner      = decision.get("inner_thought", "")
        mood_delta = decision.get("mood_delta", {})

        # 5. Log + Execute
        self._log_id += 1
        log_id = self._log_id

        with _state_lock:
            _shared_state["log_id"]          = log_id
            _shared_state["animation_state"] = anim
            _shared_state["inner_thought"]   = inner
            _shared_state["traits"]          = traits

        outcome, fail_reason = execute_action(action)

        with _state_lock:
            if outcome != "success":
                _shared_state["animation_state"] = "reacting"

        # 6. Store memory
        self.soul.append_episodic_memory({
            "content":    f"I did: {action}. Outcome: {outcome}. "
                          f"Context: {env.get('active_window','?')} / {env.get('time_of_day','?')}",
            "action":     action,
            "outcome":    outcome,
            "anim":       anim,
            "log_id":     log_id,
        })

        if fail_reason:
            self.soul.append_episodic_memory({
                "type":    "failure",
                "content": f"Failed action '{action}': {fail_reason}",
            })

        # 7. Mood update from decision
        if mood_delta:
            p = self.soul.personality()
            mood = p["emotional_state"]
            new_v = max(0.0, min(1.0, mood["valence"] + mood_delta.get("valence", 0.0)))
            new_a = max(0.0, min(1.0, mood["arousal"] + mood_delta.get("arousal", 0.0)))
            self.soul.set_mood(mood["current_mood"], new_v, new_a)

        # 8. Personality learning
        if outcome == "dismissed":
            self.soul.adjust_trait("Hesitance",  +5.0)
            self.soul.adjust_trait("Confidence", -3.0)
        elif outcome == "success":
            self.soul.adjust_trait("Efficiency", +1.5)
            self.soul.adjust_trait("Confidence", +1.0)
        elif outcome == "failure":
            self.soul.adjust_trait("Efficiency", -2.0)

        # 9. Reset daemon message
        _daemon_msg = ""
        log.info("Cycle done — action=%s outcome=%s", action, outcome)

    def record_feedback(self, log_id: int, feedback: str):
        """Called from HTTP handler when user taps 👍/👎."""
        if feedback == "positive":
            self.soul.adjust_trait("Loyalty",     +3.0)
            self.soul.adjust_trait("Playfulness", +1.0)
            self.soul.append_emotional_event({
                "type":    "user_feedback",
                "content": "User expressed positive feedback",
            })
        elif feedback == "negative":
            self.soul.adjust_trait("Loyalty", -1.0)
            self.soul.append_emotional_event({
                "type":    "user_feedback",
                "content": "User expressed negative feedback",
            })


# ── HTTP Server (serves avatar UI + state API) ────────────────────────────────

class NaviHTTPHandler(BaseHTTPRequestHandler):
    cognitive: "CognitiveLoop" = None   # set at startup
    html_path: str             = None

    def log_message(self, fmt, *args):
        pass  # silence access log

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self._serve_file(self.html_path, "text/html")

        elif path == "/state":
            with _state_lock:
                data = dict(_shared_state)
            self._json(data)

        elif path == "/soul_card":
            soul = self.cognitive.soul
            self._json({
                "card_face":   soul.card_face(),
                "identity":    soul.identity(),
                "personality": soul.personality(),
                "manifest":    soul.manifest(),
            })

        elif path.startswith("/avatar/"):
            # Serve extracted VRM
            fpath = Path.home() / ".navi" / "extracted_vrm.vrm"
            if fpath.exists():
                self._serve_file(str(fpath), "application/octet-stream")
            else:
                self._404()

        else:
            self._404()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        if path == "/feedback":
            try:
                data     = json.loads(body)
                log_id   = data.get("log_id")
                feedback = data.get("feedback", "neutral")
                self.cognitive.record_feedback(log_id, feedback)
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)}, 400)

        elif path == "/speak_to_navi":
            # User types a message directly to Navi
            try:
                data  = json.loads(body)
                msg   = data.get("message", "")
                self.cognitive.soul.append_episodic_memory({
                    "type":    "user_message",
                    "content": f"User said: {msg}",
                })
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)}, 400)

        else:
            self._404()

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self._404()

    def _404(self):
        self.send_response(404)
        self.end_headers()


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Navi Runtime")
    ap.add_argument("--soul",   default="navi.soul",  help=".soul file path")
    ap.add_argument("--name",   default="Navi",        help="Entity name (first-run only)")
    ap.add_argument("--model",  default="",            help="Path to .gguf model")
    ap.add_argument("--vrm",    default="",            help="Path to .vrm avatar")
    ap.add_argument("--port",   default=7701, type=int,help="HTTP server port")
    ap.add_argument("--cycle",  default=45,   type=int,help="Cognitive cycle seconds")
    ap.add_argument("--ollama", default="",            help="Ollama URL (e.g. http://localhost:11434)")
    args = ap.parse_args()

    # ── Soul ──────────────────────────────────────────────────────────────────
    soul_path = Path(args.soul)
    if not soul_path.exists():
        log.info("Creating new soul: %s", soul_path)
        soul = SoulFile.create(
            soul_path, args.name,
            vrm_path       = args.vrm or None,
            thumbnail_path = None,
        )
    else:
        soul = SoulFile(soul_path)
        log.info("Loaded soul: %s (%s)", soul.manifest()["entity_name"],
                 soul.manifest()["soul_version"])

    # Extract VRM if present
    vrm_url = None
    vrm_cache = Path.home() / ".navi" / "extracted_vrm.vrm"
    if soul.has_vrm():
        soul.extract_vrm(str(vrm_cache))
        vrm_url = f"http://localhost:{args.port}/avatar/model.vrm"
        log.info("VRM extracted → %s", vrm_cache)
    elif args.vrm and Path(args.vrm).exists():
        import shutil
        shutil.copy(args.vrm, vrm_cache)
        vrm_url = f"http://localhost:{args.port}/avatar/model.vrm"

    with _state_lock:
        _shared_state["entity_name"] = soul.manifest()["entity_name"]
        _shared_state["vrm_url"]     = vrm_url
        _shared_state["traits"]      = {
            k: round(v["value"], 1)
            for k, v in soul.personality()["traits"].items()
        }

    # ── LLM ───────────────────────────────────────────────────────────────────
    llm = LLMEngine(
        model_path   = args.model or None,
        ollama_url   = args.ollama or "http://localhost:11434",
        n_ctx        = 4096,
        n_threads    = 4,       # safe for most Android phones
        n_gpu_layers = 0,       # CPU-only by default
        temperature  = 0.75,
        max_tokens   = 400,
    )

    # ── Daemon ────────────────────────────────────────────────────────────────
    def on_daemon_voice(msg: str):
        global _daemon_msg
        _daemon_msg = msg
        log.info("[DAEMON→NAVI] %s", msg)
        soul.append_emotional_event({"type": "daemon_report", "content": msg})

    daemon = DaemonEngine(soul, llm_fn=llm.complete, inner_voice_callback=on_daemon_voice)
    daemon.start()

    # ── Cognitive loop ────────────────────────────────────────────────────────
    cog = CognitiveLoop(soul, llm, daemon, cycle_secs=args.cycle)
    cog.start()

    # ── HTTP server ───────────────────────────────────────────────────────────
    html_path = str(Path(__file__).parent / "avatar_renderer.html")
    NaviHTTPHandler.cognitive = cog
    NaviHTTPHandler.html_path = html_path

    server = HTTPServer(("0.0.0.0", args.port), NaviHTTPHandler)
    log.info("Navi HTTP server → http://localhost:%d", args.port)
    log.info("Open this in a browser window for the avatar overlay.")

    # Open browser automatically if available
    url = f"http://localhost:{args.port}"
    for cmd in [["termux-open-url", url],
                ["xdg-open", url],
                ["chromium-browser", f"--app={url}", "--window-size=250,400"]]:
        try:
            subprocess.Popen(cmd, start_new_session=True)
            break
        except Exception:
            continue

    print(f"\n  ╔══════════════════════════════════╗")
    print(f"  ║  Navi is alive                   ║")
    print(f"  ║  Avatar → {url:<23}║")
    print(f"  ║  Soul   → {str(soul_path):<23}║")
    print(f"  ║  LLM    → {llm.backend:<23}║")
    print(f"  ╚══════════════════════════════════╝\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nNavi shutting down...")
        daemon.stop()
        soul.update_manifest_mtime()


if __name__ == "__main__":
    main()
