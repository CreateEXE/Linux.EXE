"""
navi_core.py - Persistent Cognitive Architecture for Navi
The "brain" of the Digital Evolving Entity living in XFCE.
"""

import sqlite3
import json
import subprocess
import time
import psutil
import requests
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH      = Path.home() / ".navi" / "navi.db"
OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3"          # swap for any local model
LOG_PATH     = Path.home() / ".navi" / "navi.log"

# Personality trait bounds
TRAIT_MIN, TRAIT_MAX = 0.0, 100.0

# How many recent memories to feed into each decision
MEMORY_WINDOW = 10

# ── Logging ───────────────────────────────────────────────────────────────────

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("navi")


# ── Database Layer ─────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables on first run."""
    with get_conn() as conn:
        conn.executescript("""
        -- Facts about the user
        CREATE TABLE IF NOT EXISTS user_profile (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Every action Navi has taken, its outcome, and user feedback
        CREATE TABLE IF NOT EXISTS experience_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT    DEFAULT (datetime('now')),
            context_json TEXT    NOT NULL,   -- env state snapshot
            action       TEXT    NOT NULL,
            animation    TEXT,
            outcome      TEXT    DEFAULT 'pending',  -- success | failure | ignored | dismissed
            failure_reason TEXT,
            user_feedback  TEXT,             -- positive | negative | neutral
            rational_justification TEXT
        );

        -- Evolving personality stats (0–100)
        CREATE TABLE IF NOT EXISTS personality_stats (
            trait      TEXT PRIMARY KEY,
            value      REAL NOT NULL DEFAULT 50.0,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Learned failure patterns Navi avoids repeating
        CREATE TABLE IF NOT EXISTS failure_patterns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern     TEXT UNIQUE NOT NULL,   -- e.g. "minimize:nonexistent_window"
            fail_count  INTEGER DEFAULT 1,
            last_seen   TEXT DEFAULT (datetime('now'))
        );
        """)

        # Seed default personality traits if missing
        default_traits = {
            "Curiosity":   60.0,
            "Efficiency":  55.0,
            "Loyalty":     50.0,
            "Hesitance":   10.0,
            "Confidence":  65.0,
            "Playfulness": 45.0,
        }
        for trait, val in default_traits.items():
            conn.execute(
                "INSERT OR IGNORE INTO personality_stats (trait, value) VALUES (?, ?)",
                (trait, val),
            )
        conn.commit()
    log.info("Database initialised at %s", DB_PATH)


# ── User Profile ───────────────────────────────────────────────────────────────

def set_user_fact(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_profile (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, value),
        )
        conn.commit()


def get_user_facts() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM user_profile").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ── Personality ────────────────────────────────────────────────────────────────

def get_traits() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT trait, value FROM personality_stats").fetchall()
    return {r["trait"]: r["value"] for r in rows}


def adjust_trait(trait: str, delta: float):
    """Nudge a personality trait, clamped to [0, 100]."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE personality_stats
               SET value = MAX(?, MIN(?, value + ?)), updated_at = datetime('now')
               WHERE trait = ?""",
            (TRAIT_MIN, TRAIT_MAX, delta, trait),
        )
        conn.commit()
    log.info("Trait '%s' adjusted by %+.1f", trait, delta)


def apply_personality_learning(outcome: str, user_feedback: Optional[str], action: str):
    """
    Evolve traits based on what happened:
      - Dismissed while talking → +Hesitance, -Confidence
      - Successful action      → +Efficiency, +Confidence
      - User gave positive fb  → +Loyalty
      - Failure                → -Efficiency
    """
    if outcome == "dismissed":
        adjust_trait("Hesitance",   +5.0)
        adjust_trait("Confidence",  -3.0)
        log.info("Navi learned: being dismissed → more hesitant")

    elif outcome == "success":
        adjust_trait("Efficiency",  +2.0)
        adjust_trait("Confidence",  +1.5)

    elif outcome == "failure":
        adjust_trait("Efficiency",  -2.0)
        adjust_trait("Confidence",  -1.0)

    if user_feedback == "positive":
        adjust_trait("Loyalty",     +3.0)
        adjust_trait("Playfulness", +1.0)
    elif user_feedback == "negative":
        adjust_trait("Loyalty",     -1.0)


# ── Experience Log ─────────────────────────────────────────────────────────────

def log_action(context: dict, action: str, animation: str, justification: str) -> int:
    """Record an action before it executes. Returns the row id."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO experience_log
               (context_json, action, animation, rational_justification)
               VALUES (?, ?, ?, ?)""",
            (json.dumps(context), action, animation, justification),
        )
        conn.commit()
        return cur.lastrowid


def update_action_outcome(log_id: int, outcome: str,
                           failure_reason: str = None, user_feedback: str = None):
    with get_conn() as conn:
        conn.execute(
            """UPDATE experience_log
               SET outcome=?, failure_reason=?, user_feedback=?
               WHERE id=?""",
            (outcome, failure_reason, user_feedback, log_id),
        )
        conn.commit()


def get_recent_memories(n: int = MEMORY_WINDOW) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT timestamp, action, outcome, user_feedback, rational_justification
               FROM experience_log ORDER BY id DESC LIMIT ?""",
            (n,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def was_recently_dismissed(within_minutes: int = 30) -> bool:
    """True if the user dismissed Navi while it was active in the last N minutes."""
    cutoff = (datetime.now() - timedelta(minutes=within_minutes)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM experience_log WHERE outcome='dismissed' AND timestamp > ?",
            (cutoff,),
        ).fetchone()
    return row["c"] > 0


# ── Failure Pattern Memory ─────────────────────────────────────────────────────

def record_failure_pattern(pattern: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO failure_patterns (pattern) VALUES (?)
               ON CONFLICT(pattern) DO UPDATE SET
                 fail_count = fail_count + 1,
                 last_seen  = datetime('now')""",
            (pattern,),
        )
        conn.commit()
    log.warning("Failure pattern recorded: %s", pattern)


def is_known_failure(pattern: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT fail_count FROM failure_patterns WHERE pattern = ?",
            (pattern,),
        ).fetchone()
    return row is not None and row["fail_count"] >= 2


# ── Environment Sensing ────────────────────────────────────────────────────────

def get_active_window_name() -> str:
    try:
        wid = subprocess.check_output(
            ["xdotool", "getactivewindow"], text=True
        ).strip()
        name = subprocess.check_output(
            ["xdotool", "getwindowname", wid], text=True
        ).strip()
        return name
    except Exception as e:
        log.debug("xdotool error: %s", e)
        return "unknown"


def get_env_state() -> dict:
    cpu   = psutil.cpu_percent(interval=0.5)
    mem   = psutil.virtual_memory().percent
    now   = datetime.now()
    hour  = now.hour
    tod   = ("morning" if 5 <= hour < 12 else
             "afternoon" if 12 <= hour < 17 else
             "evening" if 17 <= hour < 21 else "night")

    return {
        "active_window": get_active_window_name(),
        "cpu_percent":   cpu,
        "mem_percent":   mem,
        "time":          now.strftime("%H:%M"),
        "time_of_day":   tod,
        "day_of_week":   now.strftime("%A"),
    }


# ── Decision Node (The Brain) ──────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Navi's rational decision engine.
You receive:
  1. env_state   – current desktop environment snapshot
  2. personality – Navi's current trait values (0-100)
  3. user_profile – known facts about the user
  4. memories    – recent actions and their outcomes
  5. failure_patterns – action patterns that have failed before (avoid these)

Your job: decide what Navi should do RIGHT NOW.

Rules:
- Never repeat a known failure pattern.
- If Hesitance > 60, prefer "wait" or very gentle actions.
- If Curiosity > 70 and CPU < 30, Navi may explore or comment on idle state.
- Be concise and purposeful — Navi is an NPC, not a chatbot.
- animation_state must be one of: idle | talking | thinking | moving | reacting | sleeping

Respond ONLY with a valid JSON object, no markdown, no extra text:
{
  "rational_justification": "<why you chose this action>",
  "action": "<action string, e.g. 'speak:Hello!', 'minimize:active_window', 'wait:30', 'notify:message'>",
  "animation_state": "<one of the allowed states>"
}"""


def decide(env_state: dict, personality: dict, memories: list,
           user_profile: dict, failure_patterns: list) -> dict:
    """Send context to Ollama and get a structured decision."""

    user_msg = json.dumps({
        "env_state":       env_state,
        "personality":     personality,
        "user_profile":    user_profile,
        "memories":        memories,
        "failure_patterns": failure_patterns,
    }, indent=2)

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "stream": False,
        "format": "json",
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=30)
        resp.raise_for_status()
        raw = resp.json()["message"]["content"]
        decision = json.loads(raw)
        # Validate required keys
        for k in ("rational_justification", "action", "animation_state"):
            if k not in decision:
                raise ValueError(f"Missing key: {k}")
        log.info("Decision: %s | Animation: %s", decision["action"], decision["animation_state"])
        return decision
    except requests.RequestException as e:
        log.error("Ollama unreachable: %s", e)
        return {"rational_justification": "Ollama offline, defaulting to idle.",
                "action": "wait:60", "animation_state": "sleeping"}
    except (json.JSONDecodeError, ValueError) as e:
        log.error("Bad LLM response: %s", e)
        return {"rational_justification": "Malformed response, waiting.",
                "action": "wait:30", "animation_state": "thinking"}


# ── Action Executor ────────────────────────────────────────────────────────────

def execute_action(action: str, env_state: dict) -> tuple[str, Optional[str]]:
    """
    Parse and execute an action string.
    Returns (outcome, failure_reason).
    Supported actions:
      speak:<text>           – placeholder for TTS / bubble display
      notify:<message>       – desktop notification via notify-send
      minimize:active_window – minimize the active window via xdotool
      wait:<seconds>         – sleep
      idle                   – do nothing this cycle
    """
    parts   = action.split(":", 1)
    verb    = parts[0].strip().lower()
    arg     = parts[1].strip() if len(parts) > 1 else ""

    # Guard: skip known failure patterns
    pattern = f"{verb}:{arg}"
    if is_known_failure(pattern):
        log.warning("Skipping known failure pattern: %s", pattern)
        return "skipped_known_failure", f"Pattern '{pattern}' has failed before"

    try:
        if verb == "speak":
            # In a real deployment, route to your GTK bubble or TTS engine
            log.info("Navi says: %s", arg)
            print(f"[Navi speaks] {arg}")
            return "success", None

        elif verb == "notify":
            subprocess.run(
                ["notify-send", "Navi", arg, "--icon=dialog-information"],
                check=True, timeout=5,
            )
            return "success", None

        elif verb == "minimize":
            if arg == "active_window":
                wid = subprocess.check_output(
                    ["xdotool", "getactivewindow"], text=True
                ).strip()
                if not wid:
                    reason = "No active window to minimize"
                    record_failure_pattern(pattern)
                    return "failure", reason
                subprocess.run(["xdotool", "windowminimize", wid], check=True)
                return "success", None
            else:
                # Try to find window by name
                result = subprocess.run(
                    ["xdotool", "search", "--name", arg],
                    capture_output=True, text=True,
                )
                if not result.stdout.strip():
                    reason = f"Window '{arg}' not found"
                    record_failure_pattern(pattern)
                    return "failure", reason
                wid = result.stdout.strip().split("\n")[0]
                subprocess.run(["xdotool", "windowminimize", wid], check=True)
                return "success", None

        elif verb == "wait":
            secs = float(arg) if arg else 10.0
            log.info("Navi waiting %.0fs", secs)
            time.sleep(secs)
            return "success", None

        elif verb == "idle":
            return "success", None

        else:
            reason = f"Unknown action verb: {verb}"
            log.warning(reason)
            record_failure_pattern(pattern)
            return "failure", reason

    except subprocess.CalledProcessError as e:
        reason = f"Subprocess failed: {e}"
        log.error(reason)
        record_failure_pattern(pattern)
        return "failure", reason
    except Exception as e:
        reason = f"Unexpected error: {e}"
        log.error(reason)
        return "failure", reason


# ── Hesitance Gating ──────────────────────────────────────────────────────────

def get_speak_delay(personality: dict) -> float:
    """
    Return extra seconds to wait before speaking, driven by Hesitance trait.
    Dismissed recently? Double the delay.
    """
    base_delay = (personality.get("Hesitance", 10.0) / 100.0) * 20.0  # 0-20s
    if was_recently_dismissed():
        base_delay *= 2.0
        log.info("Recently dismissed — speak delay doubled to %.1fs", base_delay)
    return base_delay


# ── Main Cognitive Loop ────────────────────────────────────────────────────────

def cognitive_cycle():
    """One full sense → decide → act → learn cycle."""
    log.info("── Cognitive cycle start ──")

    # 1. Sense environment
    env_state   = get_env_state()
    personality = get_traits()
    user_profile= get_user_facts()
    memories    = get_recent_memories()
    with get_conn() as conn:
        fp_rows = conn.execute(
            "SELECT pattern, fail_count FROM failure_patterns ORDER BY fail_count DESC LIMIT 20"
        ).fetchall()
    failure_patterns = [dict(r) for r in fp_rows]

    log.info("Env: %s | CPU %.0f%% | MEM %.0f%%",
             env_state["active_window"], env_state["cpu_percent"], env_state["mem_percent"])

    # 2. Hesitance gate — maybe pause before acting
    delay = get_speak_delay(personality)
    if delay > 1.0:
        log.info("Hesitance delay: %.1fs", delay)
        time.sleep(delay)

    # 3. Decide
    decision = decide(env_state, personality, memories, user_profile, failure_patterns)

    action      = decision["action"]
    animation   = decision["animation_state"]
    justification = decision["rational_justification"]

    # 4. Log the action (before executing)
    log_id = log_action(env_state, action, animation, justification)

    # 5. Execute
    outcome, failure_reason = execute_action(action, env_state)

    # 6. Update log with outcome
    update_action_outcome(log_id, outcome, failure_reason)

    # 7. Learn from outcome
    apply_personality_learning(outcome, user_feedback=None, action=action)

    log.info("Action: %-30s | Outcome: %s", action, outcome)
    if failure_reason:
        log.warning("Failure reason: %s", failure_reason)

    return {
        "decision":   decision,
        "outcome":    outcome,
        "failure":    failure_reason,
        "env_state":  env_state,
        "personality":personality,
    }


# ── Feedback API (call from your GTK layer) ───────────────────────────────────

def record_user_feedback(log_id: int, feedback: str, outcome_override: str = None):
    """
    Call this from the GTK frontend when the user clicks 👍/👎 or closes the window.
    feedback: 'positive' | 'negative' | 'neutral'
    outcome_override: e.g. 'dismissed'
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE experience_log SET user_feedback=?, outcome=COALESCE(?, outcome) WHERE id=?",
            (feedback, outcome_override, log_id),
        )
        conn.commit()
    apply_personality_learning(
        outcome_override or "ignored",
        user_feedback=feedback,
        action="",
    )
    log.info("User feedback recorded for log_id %d: %s / %s", log_id, feedback, outcome_override)


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    # Seed some user facts (first-run example)
    set_user_fact("name", "User")
    set_user_fact("preferred_tone", "friendly")

    print("Navi cognitive engine starting. Press Ctrl+C to stop.\n")
    cycle_interval = 45  # seconds between full cognitive cycles

    while True:
        try:
            result = cognitive_cycle()
            print(f"[{datetime.now():%H:%M:%S}] "
                  f"Action={result['decision']['action']} | "
                  f"Anim={result['decision']['animation_state']} | "
                  f"Outcome={result['outcome']}")
        except KeyboardInterrupt:
            print("\nNavi shutting down.")
            break
        except Exception as e:
            log.exception("Unhandled error in cognitive cycle: %s", e)

        time.sleep(cycle_interval)
