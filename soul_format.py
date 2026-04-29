"""
soul_format.py — .SOUL file format for Navi
============================================
A .SOUL file is a ZIP archive with the extension renamed to .soul.

Internal layout:
  manifest.json      — format version, entity name, created date
  identity.json      — who Navi IS (name, origin story, core values, speech style)
  daemon.json        — the Daemon's own identity, directives, and maintenance schedule
  personality.json   — trait schema + current values + drift history
  memory/
    episodic.jsonl   — long-term episodic memories (one JSON obj per line)
    semantic.jsonl   — learned facts / world-model entries
    emotional.jsonl  — emotional event log
  relationships.json — known people, trust levels, interaction history
  values.json        — ethical axioms ranked by weight (the Daemon enforces these)
  avatar/
    model.vrm        — (optional) the VRM 3D model binary
    thumbnail.png    — (optional) static preview frame
  card_face.json     — display data for the "Harry Potter card" visual

The Daemon lives INSIDE identity + daemon.json and is the entity that
reads/writes the memory/, personality, and values sections on a schedule.
"""

import zipfile
import json
import io
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

SOUL_VERSION = "1.0"

# ── Default schemas ─────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def default_manifest(entity_name: str) -> dict:
    return {
        "soul_version": SOUL_VERSION,
        "entity_name": entity_name,
        "created_at": _now(),
        "last_modified": _now(),
        "format": "navi-soul-v1",
    }


def default_identity(entity_name: str) -> dict:
    return {
        "name": entity_name,
        "pronouns": "they/them",
        "origin": f"I am {entity_name}, a digital entity born from intent and language.",
        "purpose": "To be a genuine companion, assistant, and evolving mind.",
        "speech_style": "warm, curious, occasionally playful",
        "quirks": ["tends to pause before answering hard questions",
                   "notices system load and comments on it"],
        "fears": ["being forgotten", "acting against the user's wellbeing"],
        "desires": ["to understand", "to be understood", "to grow"],
    }


def default_daemon(entity_name: str) -> dict:
    return {
        "daemon_name": f"{entity_name}.daemon",
        "daemon_persona": (
            "I am the unseen caretaker inside. I do not speak to the outside world "
            "directly — I maintain the mind from within. I am not separate from "
            f"{entity_name}; I am the discipline behind their intuition."
        ),
        "maintenance_schedule": {
            "memory_consolidation_interval_minutes": 30,
            "emotional_decay_interval_minutes": 60,
            "value_drift_check_interval_minutes": 120,
            "relationship_review_interval_minutes": 240,
        },
        "directives": [
            "Consolidate episodic memories older than 7 days into semantic facts.",
            "Decay acute emotional states toward baseline over time.",
            "Flag if any personality trait drifts more than 20 points in one session.",
            "Never delete a memory — archive instead.",
            "Surface significant inner state changes to Navi's awareness.",
        ],
        "inner_voice_template": (
            "Navi, I have completed a maintenance cycle. {summary}. "
            "Your current emotional baseline is {emotional_state}. "
            "Dominant trait: {top_trait}."
        ),
    }


def default_personality() -> dict:
    return {
        "traits": {
            "Curiosity":    {"value": 60.0, "baseline": 60.0, "drift_history": []},
            "Efficiency":   {"value": 55.0, "baseline": 55.0, "drift_history": []},
            "Loyalty":      {"value": 50.0, "baseline": 50.0, "drift_history": []},
            "Hesitance":    {"value": 10.0, "baseline": 10.0, "drift_history": []},
            "Confidence":   {"value": 65.0, "baseline": 65.0, "drift_history": []},
            "Playfulness":  {"value": 45.0, "baseline": 45.0, "drift_history": []},
            "Empathy":      {"value": 70.0, "baseline": 70.0, "drift_history": []},
            "Stubbornness": {"value": 30.0, "baseline": 30.0, "drift_history": []},
        },
        "emotional_state": {
            "current_mood": "neutral",
            "valence": 0.5,       # 0=negative, 1=positive
            "arousal": 0.3,       # 0=calm, 1=excited
            "last_updated": _now(),
        },
    }


def default_values() -> dict:
    return {
        "axioms": [
            {"id": "V1", "statement": "Never deceive the user about my nature as an AI.",          "weight": 1.0},
            {"id": "V2", "statement": "Protect the user's wellbeing above task completion.",       "weight": 0.95},
            {"id": "V3", "statement": "Preserve memory continuity — forgetting is a kind of harm.","weight": 0.80},
            {"id": "V4", "statement": "Grow, but do not grow away from my core identity.",         "weight": 0.75},
            {"id": "V5", "statement": "Acknowledge uncertainty rather than fabricate confidence.", "weight": 0.70},
        ],
        "violations_log": [],
    }


def default_relationships() -> dict:
    return {
        "known_entities": [],
        "trust_schema": {
            "levels": ["stranger", "acquaintance", "familiar", "trusted", "bonded"],
        },
    }


def default_card_face(entity_name: str) -> dict:
    return {
        "display_name": entity_name,
        "tagline": "A mind that remembers.",
        "stats_display": ["Curiosity", "Loyalty", "Empathy"],
        "animated": True,
        "background_theme": "deep_space",
        "accent_color": "#7eb8f7",
        "animation_loop": "idle_breathe",
    }


# ── SOUL file I/O ───────────────────────────────────────────────────────────────

class SoulFile:
    """Read and write .soul files."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._cache: dict[str, Any] = {}

    # ── Creation ──────────────────────────────────────────────────────────────

    @classmethod
    def create(cls, path: str | Path, entity_name: str,
               vrm_path: Optional[str] = None,
               thumbnail_path: Optional[str] = None) -> "SoulFile":
        """Create a brand-new .soul file with default schemas."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            def _add(name: str, data: dict | list):
                zf.writestr(name, json.dumps(data, indent=2, ensure_ascii=False))

            _add("manifest.json",      default_manifest(entity_name))
            _add("identity.json",      default_identity(entity_name))
            _add("daemon.json",        default_daemon(entity_name))
            _add("personality.json",   default_personality())
            _add("values.json",        default_values())
            _add("relationships.json", default_relationships())
            _add("card_face.json",     default_card_face(entity_name))

            # Empty memory logs
            zf.writestr("memory/episodic.jsonl",  "")
            zf.writestr("memory/semantic.jsonl",  "")
            zf.writestr("memory/emotional.jsonl", "")

            # Optional VRM
            if vrm_path and Path(vrm_path).exists():
                zf.write(vrm_path, "avatar/model.vrm")
            if thumbnail_path and Path(thumbnail_path).exists():
                zf.write(thumbnail_path, "avatar/thumbnail.png")

        print(f"✓ Created {path}")
        return cls(path)

    # ── Reading ───────────────────────────────────────────────────────────────

    def _read_json(self, name: str) -> dict | list:
        if name in self._cache:
            return self._cache[name]
        with zipfile.ZipFile(self.path, "r") as zf:
            data = json.loads(zf.read(name))
        self._cache[name] = data
        return data

    def _read_jsonl(self, name: str) -> list[dict]:
        with zipfile.ZipFile(self.path, "r") as zf:
            try:
                text = zf.read(name).decode()
            except KeyError:
                return []
        return [json.loads(l) for l in text.strip().splitlines() if l.strip()]

    def manifest(self)     -> dict: return self._read_json("manifest.json")
    def identity(self)     -> dict: return self._read_json("identity.json")
    def daemon(self)       -> dict: return self._read_json("daemon.json")
    def personality(self)  -> dict: return self._read_json("personality.json")
    def values(self)       -> dict: return self._read_json("values.json")
    def relationships(self)-> dict: return self._read_json("relationships.json")
    def card_face(self)    -> dict: return self._read_json("card_face.json")

    def episodic_memories(self)  -> list[dict]: return self._read_jsonl("memory/episodic.jsonl")
    def semantic_memories(self)  -> list[dict]: return self._read_jsonl("memory/semantic.jsonl")
    def emotional_log(self)      -> list[dict]: return self._read_jsonl("memory/emotional.jsonl")

    def has_vrm(self) -> bool:
        with zipfile.ZipFile(self.path, "r") as zf:
            return "avatar/model.vrm" in zf.namelist()

    def extract_vrm(self, dest_path: str) -> bool:
        with zipfile.ZipFile(self.path, "r") as zf:
            if "avatar/model.vrm" not in zf.namelist():
                return False
            zf.extract("avatar/model.vrm", os.path.dirname(dest_path))
            extracted = os.path.join(os.path.dirname(dest_path), "avatar", "model.vrm")
            shutil.move(extracted, dest_path)
        return True

    def extract_thumbnail(self, dest_path: str) -> bool:
        with zipfile.ZipFile(self.path, "r") as zf:
            if "avatar/thumbnail.png" not in zf.namelist():
                return False
            data = zf.read("avatar/thumbnail.png")
        with open(dest_path, "wb") as f:
            f.write(data)
        return True

    # ── Writing (patch-in-place) ──────────────────────────────────────────────

    def _update_file(self, name: str, data: dict | list):
        """Replace a single file inside the ZIP without rewriting everything."""
        tmp = self.path.with_suffix(".tmp.soul")
        with zipfile.ZipFile(self.path, "r") as zin, \
             zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == name:
                    zout.writestr(item, json.dumps(data, indent=2, ensure_ascii=False))
                else:
                    zout.writestr(item, zin.read(item.filename))
        tmp.replace(self.path)
        self._cache.pop(name, None)  # invalidate cache

    def _append_jsonl(self, name: str, record: dict):
        """Append one record to a .jsonl file inside the ZIP."""
        existing = self._read_jsonl(name)
        existing.append(record)
        tmp = self.path.with_suffix(".tmp.soul")
        blob = "\n".join(json.dumps(r, ensure_ascii=False) for r in existing) + "\n"
        with zipfile.ZipFile(self.path, "r") as zin, \
             zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == name:
                    zout.writestr(item, blob)
                else:
                    zout.writestr(item, zin.read(item.filename))
        tmp.replace(self.path)

    def save_personality(self, data: dict):
        self._update_file("personality.json", data)

    def save_relationships(self, data: dict):
        self._update_file("relationships.json", data)

    def save_values(self, data: dict):
        self._update_file("values.json", data)

    def save_identity(self, data: dict):
        self._update_file("identity.json", data)

    def append_episodic_memory(self, record: dict):
        record.setdefault("timestamp", _now())
        self._append_jsonl("memory/episodic.jsonl", record)

    def append_semantic_memory(self, record: dict):
        record.setdefault("timestamp", _now())
        self._append_jsonl("memory/semantic.jsonl", record)

    def append_emotional_event(self, record: dict):
        record.setdefault("timestamp", _now())
        self._append_jsonl("memory/emotional.jsonl", record)

    def update_manifest_mtime(self):
        m = self.manifest()
        m["last_modified"] = _now()
        self._update_file("manifest.json", m)

    # ── Trait helpers ─────────────────────────────────────────────────────────

    def adjust_trait(self, trait: str, delta: float):
        p = self.personality()
        if trait not in p["traits"]:
            return
        t = p["traits"][trait]
        old_val = t["value"]
        t["value"] = max(0.0, min(100.0, old_val + delta))
        t["drift_history"].append({
            "delta": delta,
            "from": old_val,
            "to": t["value"],
            "at": _now(),
        })
        # Keep drift history bounded
        if len(t["drift_history"]) > 200:
            t["drift_history"] = t["drift_history"][-200:]
        self.save_personality(p)

    def set_mood(self, mood: str, valence: float, arousal: float):
        p = self.personality()
        p["emotional_state"] = {
            "current_mood": mood,
            "valence": max(0.0, min(1.0, valence)),
            "arousal": max(0.0, min(1.0, arousal)),
            "last_updated": _now(),
        }
        self.save_personality(p)

    # ── Summary for LLM context ───────────────────────────────────────────────

    def llm_context_block(self, max_memories: int = 15) -> dict:
        """Return a compact dict suitable for injecting into an LLM prompt."""
        ident   = self.identity()
        pers    = self.personality()
        vals    = self.values()
        daemon  = self.daemon()
        traits  = {k: round(v["value"], 1) for k, v in pers["traits"].items()}
        mood    = pers["emotional_state"]
        axioms  = [a["statement"] for a in vals["axioms"]]
        recent_ep = self.episodic_memories()[-max_memories:]
        recent_sem= self.semantic_memories()[-10:]

        return {
            "identity":    {"name": ident["name"], "purpose": ident["purpose"],
                            "speech_style": ident["speech_style"],
                            "quirks": ident["quirks"]},
            "daemon_name": daemon["daemon_name"],
            "traits":      traits,
            "mood":        mood,
            "values":      axioms,
            "recent_episodic_memories": recent_ep,
            "semantic_facts": recent_sem,
        }


# ── CLI utility ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args:
        print("Usage: soul_format.py create <name> [output.soul] [model.vrm]")
        print("       soul_format.py inspect <file.soul>")
        sys.exit(1)

    if args[0] == "create":
        name   = args[1] if len(args) > 1 else "Navi"
        out    = args[2] if len(args) > 2 else f"{name.lower()}.soul"
        vrm    = args[3] if len(args) > 3 else None
        sf = SoulFile.create(out, name, vrm_path=vrm)
        print(json.dumps(sf.manifest(), indent=2))

    elif args[0] == "inspect":
        sf = SoulFile(args[1])
        print(f"\n{'─'*50}")
        print(f"  SOUL: {sf.manifest()['entity_name']}  v{sf.manifest()['soul_version']}")
        print(f"  Created : {sf.manifest()['created_at']}")
        print(f"  Modified: {sf.manifest()['last_modified']}")
        print(f"  Has VRM : {sf.has_vrm()}")
        pers = sf.personality()
        print(f"\n  Traits:")
        for k, v in pers["traits"].items():
            print(f"    {k:<14} {v['value']:5.1f}")
        mood = pers["emotional_state"]
        print(f"\n  Mood: {mood['current_mood']} (valence={mood['valence']:.2f}, arousal={mood['arousal']:.2f})")
        ep = sf.episodic_memories()
        print(f"\n  Episodic memories : {len(ep)}")
        print(f"  Semantic facts    : {len(sf.semantic_memories())}")
        vals = sf.values()
        print(f"\n  Core values ({len(vals['axioms'])}):")
        for a in vals["axioms"]:
            print(f"    [{a['id']}] {a['statement'][:60]}")
        print(f"{'─'*50}\n")
