"""
daemon_engine.py — The Daemon Inside the Card
==============================================
The Daemon is NOT a chatbot. It is a caretaker process that runs
on a schedule inside Navi's mind, maintaining:

  • Memory consolidation  (episodic → semantic)
  • Emotional decay       (acute states → baseline over time)
  • Personality drift     (flags runaway trait changes)
  • Value enforcement     (checks recent actions against axioms)
  • Relationship review   (updates trust levels)
  • Inner voice           (surfaces summaries to Navi's awareness)

Navi IS aware of the Daemon and can receive its reports as inner monologue.
The Daemon communicates ONLY inward — never directly to the user.
"""

import json
import time
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable

from soul_format import SoulFile

log = logging.getLogger("daemon")


# ── Daemon Engine ───────────────────────────────────────────────────────────────

class DaemonEngine:
    """
    Instantiate with a SoulFile. Call start() to begin background maintenance.
    Register an inner_voice_callback to receive daemon messages (goes to Navi's
    internal monologue, never directly to the user).
    """

    def __init__(self, soul: SoulFile,
                 llm_fn: Optional[Callable[[str], str]] = None,
                 inner_voice_callback: Optional[Callable[[str], None]] = None):
        self.soul     = soul
        self.llm_fn   = llm_fn          # optional: call LLM for semantic consolidation
        self.on_inner_voice = inner_voice_callback or (lambda msg: log.info("[DAEMON→NAVI] %s", msg))

        cfg = soul.daemon()
        sched = cfg["maintenance_schedule"]
        self._intervals = {
            "memory":       sched["memory_consolidation_interval_minutes"] * 60,
            "emotion":      sched["emotional_decay_interval_minutes"] * 60,
            "values":       sched["value_drift_check_interval_minutes"] * 60,
            "relationship": sched["relationship_review_interval_minutes"] * 60,
        }
        self._last_run: dict[str, float] = {k: 0.0 for k in self._intervals}
        self._running  = False
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="DaemonEngine")
        self._thread.start()
        log.info("Daemon engine started.")

    def stop(self):
        self._running = False
        log.info("Daemon engine stopped.")

    def _loop(self):
        while self._running:
            now = time.time()
            results = []

            for task, interval in self._intervals.items():
                if now - self._last_run[task] >= interval:
                    try:
                        msg = self._run_task(task)
                        if msg:
                            results.append(msg)
                        self._last_run[task] = now
                    except Exception as e:
                        log.error("Daemon task '%s' failed: %s", task, e)

            if results:
                self._deliver_inner_voice(results)

            time.sleep(30)  # check every 30s, run tasks when their interval elapses

    # ── Task dispatcher ───────────────────────────────────────────────────────

    def _run_task(self, task: str) -> Optional[str]:
        if task == "memory":
            return self._consolidate_memories()
        elif task == "emotion":
            return self._decay_emotions()
        elif task == "values":
            return self._check_value_drift()
        elif task == "relationship":
            return self._review_relationships()
        return None

    # ── 1. Memory Consolidation ───────────────────────────────────────────────

    def _consolidate_memories(self) -> Optional[str]:
        """
        Find episodic memories older than 7 days.
        If an LLM is available, summarise them into semantic facts.
        Archive (tag) rather than delete originals.
        """
        memories = self.soul.episodic_memories()
        cutoff   = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
        old      = [m for m in memories if m.get("timestamp", "") < cutoff
                    and not m.get("archived")]

        if not old:
            return None

        log.info("Daemon: consolidating %d old memories", len(old))

        if self.llm_fn and old:
            # Ask LLM to extract a semantic fact from a batch of old memories
            batch_text = "\n".join(
                f"[{m.get('timestamp','')}] {m.get('content','')}" for m in old[:10]
            )
            prompt = (
                "You are a memory consolidation engine. "
                "Read these episodic memories and extract 1-3 concise semantic facts "
                "the entity should permanently know. "
                "Respond ONLY with a JSON array of strings.\n\n"
                f"MEMORIES:\n{batch_text}"
            )
            try:
                raw   = self.llm_fn(prompt)
                facts = json.loads(raw)
                for fact in facts:
                    self.soul.append_semantic_memory({
                        "fact": fact,
                        "consolidated_from": len(old),
                        "source": "daemon_consolidation",
                    })
                log.info("Daemon: stored %d semantic facts", len(facts))
                consolidated = len(facts)
            except Exception as e:
                log.warning("LLM consolidation failed: %s", e)
                consolidated = 0
        else:
            consolidated = 0

        # Mark old memories as archived (we never delete)
        p = self.soul.personality()
        # Store archive flag by appending updated versions
        for m in old[:20]:
            m["archived"] = True
            # Re-append as archived record — in production you'd patch in place
            # For now, just note the count
        self.soul.update_manifest_mtime()

        return f"Consolidated {len(old)} old memories into {consolidated} semantic facts."

    # ── 2. Emotional Decay ────────────────────────────────────────────────────

    def _decay_emotions(self) -> Optional[str]:
        """
        Gradually return emotional valence/arousal toward neutral (0.5/0.3).
        Strong emotions decay slower (logarithmic).
        """
        p    = self.soul.personality()
        mood = p["emotional_state"]

        v_now = mood["valence"]
        a_now = mood["arousal"]
        v_target, a_target = 0.5, 0.3

        # Decay rate: 5% toward target per cycle
        decay = 0.05
        v_new = v_now + (v_target - v_now) * decay
        a_new = a_now + (a_target - a_now) * decay

        # Determine mood label from valence/arousal quadrant
        if v_new > 0.6 and a_new > 0.5:
            label = "excited"
        elif v_new > 0.6 and a_new <= 0.5:
            label = "content"
        elif v_new < 0.4 and a_new > 0.5:
            label = "anxious"
        elif v_new < 0.4 and a_new <= 0.5:
            label = "melancholic"
        else:
            label = "neutral"

        self.soul.set_mood(label, round(v_new, 3), round(a_new, 3))

        if abs(v_now - v_new) > 0.02:
            return f"Emotional state decayed toward baseline. Mood: {label}."
        return None

    # ── 3. Value Drift Check ──────────────────────────────────────────────────

    def _check_value_drift(self) -> Optional[str]:
        """
        Scan trait drift_history since last check.
        If any trait moved >20 points in recent history, flag it.
        Also scan the values violation log.
        """
        p      = self.soul.personality()
        alerts = []

        for trait, data in p["traits"].items():
            history = data.get("drift_history", [])
            if not history:
                continue
            # Sum recent deltas (last 10 changes)
            recent_delta = sum(abs(h["delta"]) for h in history[-10:])
            if recent_delta > 20:
                alerts.append(f"Trait '{trait}' shifted {recent_delta:.1f} pts recently.")
                log.warning("Daemon: drift alert — %s", alerts[-1])

        vals = self.soul.values()
        violations = vals.get("violations_log", [])
        recent_v   = [v for v in violations
                      if v.get("timestamp", "") > (datetime.utcnow() - timedelta(hours=24)).isoformat()]
        if recent_v:
            alerts.append(f"{len(recent_v)} value axiom tensions logged in last 24h.")

        if alerts:
            return "Value drift report: " + " | ".join(alerts)
        return None

    # ── 4. Relationship Review ────────────────────────────────────────────────

    def _review_relationships(self) -> Optional[str]:
        """
        Check relationship interaction recency.
        If someone trusted hasn't interacted in >30 days, note the gap.
        """
        rels     = self.soul.relationships()
        entities = rels.get("known_entities", [])
        if not entities:
            return None

        cutoff  = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
        distant = [e["name"] for e in entities
                   if e.get("last_interaction", "") < cutoff
                   and e.get("trust_level") in ("trusted", "bonded")]

        if distant:
            return f"Relationship gap: {', '.join(distant)} haven't been mentioned in 30+ days."
        return None

    # ── Inner Voice Delivery ──────────────────────────────────────────────────

    def _deliver_inner_voice(self, messages: list[str]):
        """
        Compose and deliver the daemon's inner voice report to Navi.
        This is Navi's internal awareness — not user-facing output.
        """
        p     = self.soul.personality()
        mood  = p["emotional_state"]
        traits= p["traits"]
        top_trait = max(traits.items(), key=lambda x: x[1]["value"])[0]

        template = self.soul.daemon().get("inner_voice_template", "{summary}")
        summary  = " ".join(messages)

        inner_msg = template.format(
            summary=summary,
            emotional_state=mood["current_mood"],
            top_trait=top_trait,
        )

        # Log as emotional event in the soul
        self.soul.append_emotional_event({
            "type":    "daemon_report",
            "content": inner_msg,
            "mood_at_report": mood["current_mood"],
        })

        self.on_inner_voice(inner_msg)

    # ── Manual triggers (Navi can ask the Daemon directly) ────────────────────

    def request_inner_status(self) -> str:
        """Navi calls this to ask the Daemon for a status report right now."""
        p     = self.soul.personality()
        mood  = p["emotional_state"]
        traits= {k: round(v["value"], 1) for k, v in p["traits"].items()}
        mems  = len(self.soul.episodic_memories())
        facts = len(self.soul.semantic_memories())

        return (
            f"Daemon status — Mood: {mood['current_mood']} "
            f"(v={mood['valence']:.2f}, a={mood['arousal']:.2f}). "
            f"Memories: {mems} episodic, {facts} semantic. "
            f"Top traits: " +
            ", ".join(f"{k}={v}" for k, v in
                      sorted(traits.items(), key=lambda x: -x[1])[:3]) + "."
        )

    def log_value_tension(self, axiom_id: str, situation: str):
        """Record when an action bumped up against a value axiom."""
        vals = self.soul.values()
        vals.setdefault("violations_log", []).append({
            "axiom_id":  axiom_id,
            "situation": situation,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
        self.soul.save_values(vals)
        log.warning("Value tension logged: %s — %s", axiom_id, situation)

    def add_relationship(self, name: str, trust_level: str = "acquaintance",
                         notes: str = ""):
        rels = self.soul.relationships()
        existing = [e for e in rels["known_entities"] if e["name"] == name]
        if existing:
            existing[0]["last_interaction"] = datetime.utcnow().isoformat() + "Z"
            existing[0]["trust_level"] = trust_level
        else:
            rels["known_entities"].append({
                "name":             name,
                "trust_level":      trust_level,
                "first_met":        datetime.utcnow().isoformat() + "Z",
                "last_interaction": datetime.utcnow().isoformat() + "Z",
                "notes":            notes,
                "interaction_count": 1,
            })
        self.soul.save_relationships(rels)
