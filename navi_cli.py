#!/usr/bin/env python3
"""
navi_cli.py - Debug & inspection tool for Navi's persistent state.
Usage:
  python navi_cli.py traits            # print personality stats
  python navi_cli.py memories [n]      # print last N memories (default 20)
  python navi_cli.py profile           # print user profile facts
  python navi_cli.py failures          # print known failure patterns
  python navi_cli.py set <key> <value> # set a user profile fact
  python navi_cli.py feedback <id> <pos|neg|neutral> [dismissed]
  python navi_cli.py cycle             # run one manual cognitive cycle
  python navi_cli.py reset-traits      # reset all traits to defaults
"""

import sys
import json
from datetime import datetime

# Ensure navi_core is importable from same dir
import os
sys.path.insert(0, os.path.dirname(__file__))

from navi_core import (
    init_db, get_traits, get_user_facts, get_recent_memories,
    set_user_fact, adjust_trait, record_user_feedback,
    cognitive_cycle, get_conn
)

BOLD = "\033[1m"
DIM  = "\033[2m"
CYAN = "\033[96m"
YEL  = "\033[93m"
GRN  = "\033[92m"
RED  = "\033[91m"
RST  = "\033[0m"


def bar(val: float, width: int = 20) -> str:
    filled = int(val / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {val:5.1f}"


def cmd_traits():
    traits = get_traits()
    print(f"\n{BOLD}{CYAN}── Navi Personality Traits ──{RST}")
    for trait, val in sorted(traits.items(), key=lambda x: -x[1]):
        color = GRN if val >= 60 else (YEL if val >= 35 else RED)
        print(f"  {trait:<14} {color}{bar(val)}{RST}")
    print()


def cmd_memories(n: int = 20):
    mems = get_recent_memories(n)
    print(f"\n{BOLD}{CYAN}── Last {len(mems)} Memories ──{RST}")
    for m in mems:
        ts   = m["timestamp"][:16]
        out  = m["outcome"] or "?"
        fb   = m.get("user_feedback") or "-"
        col  = GRN if out == "success" else (RED if out == "failure" else YEL)
        print(f"  {DIM}{ts}{RST}  {col}{out:<10}{RST} fb={fb:<9}  {m['action'][:50]}")
        if m.get("rational_justification"):
            print(f"              {DIM}{m['rational_justification'][:70]}{RST}")
    print()


def cmd_profile():
    facts = get_user_facts()
    print(f"\n{BOLD}{CYAN}── User Profile ──{RST}")
    if not facts:
        print("  (empty)")
    for k, v in facts.items():
        print(f"  {YEL}{k:<20}{RST} {v}")
    print()


def cmd_failures():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pattern, fail_count, last_seen FROM failure_patterns ORDER BY fail_count DESC"
        ).fetchall()
    print(f"\n{BOLD}{CYAN}── Known Failure Patterns ──{RST}")
    if not rows:
        print("  (none yet)")
    for r in rows:
        print(f"  {RED}✗{RST} {r['pattern']:<40} count={r['fail_count']}  last={r['last_seen'][:16]}")
    print()


def cmd_set(key: str, value: str):
    set_user_fact(key, value)
    print(f"{GRN}✓{RST} Set profile fact: {YEL}{key}{RST} = {value}")


def cmd_feedback(log_id: int, feedback: str, outcome: str = None):
    record_user_feedback(log_id, feedback, outcome)
    print(f"{GRN}✓{RST} Recorded feedback={feedback} outcome={outcome} for log #{log_id}")


def cmd_cycle():
    print(f"\n{BOLD}Running manual cognitive cycle…{RST}")
    result = cognitive_cycle()
    d = result["decision"]
    print(f"\n{CYAN}Decision:{RST}")
    print(f"  Action    : {GRN}{d['action']}{RST}")
    print(f"  Animation : {d['animation_state']}")
    print(f"  Reasoning : {DIM}{d['rational_justification']}{RST}")
    print(f"\n{CYAN}Outcome:{RST} {GRN if result['outcome']=='success' else RED}{result['outcome']}{RST}")
    if result["failure"]:
        print(f"  Failure   : {RED}{result['failure']}{RST}")
    print(f"\n{CYAN}Environment:{RST}")
    for k, v in result["env_state"].items():
        print(f"  {k:<16} {v}")
    print()


def cmd_reset_traits():
    defaults = {
        "Curiosity": 60.0, "Efficiency": 55.0, "Loyalty": 50.0,
        "Hesitance": 10.0, "Confidence": 65.0, "Playfulness": 45.0,
    }
    with get_conn() as conn:
        for trait, val in defaults.items():
            conn.execute(
                "UPDATE personality_stats SET value=?, updated_at=datetime('now') WHERE trait=?",
                (val, trait),
            )
        conn.commit()
    print(f"{GRN}✓{RST} Personality traits reset to defaults.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    init_db()
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "traits":
        cmd_traits()
    elif cmd == "memories":
        n = int(args[1]) if len(args) > 1 else 20
        cmd_memories(n)
    elif cmd == "profile":
        cmd_profile()
    elif cmd == "failures":
        cmd_failures()
    elif cmd == "set":
        if len(args) < 3:
            print("Usage: navi_cli.py set <key> <value>")
            sys.exit(1)
        cmd_set(args[1], " ".join(args[2:]))
    elif cmd == "feedback":
        if len(args) < 3:
            print("Usage: navi_cli.py feedback <log_id> <pos|neg|neutral> [dismissed]")
            sys.exit(1)
        log_id   = int(args[1])
        feedback = args[2]
        outcome  = args[3] if len(args) > 3 else None
        cmd_feedback(log_id, feedback, outcome)
    elif cmd == "cycle":
        cmd_cycle()
    elif cmd == "reset-traits":
        cmd_reset_traits()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
