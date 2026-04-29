"""
navi_sprite.py - GTK4 Desktop Overlay for Navi
The visible body: draggable sprite, speech bubble, feedback buttons.
Runs as a transparent always-on-top XFCE desktop window.

Requires: python3-gi, GTK4, Cairo
Install:  sudo apt install python3-gi gir1.2-gtk-4.0
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Gdk, GLib
import threading
import time
from datetime import datetime

# Import brain
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from navi_core import (
    init_db, cognitive_cycle, record_user_feedback,
    get_traits, was_recently_dismissed
)

# ── Animation state → simple emoji avatar ─────────────────────────────────────
ANIM_FACES = {
    "idle":      "( ˘ω˘ )",
    "talking":   "( •̀ᴗ•́ )",
    "thinking":  "( ´ . .̫ . ` )",
    "moving":    "( ∿°○° )∿",
    "reacting":  "( ⊙_⊙ )",
    "sleeping":  "( -_-)ᶻᶻ",
}

ANIM_COLORS = {
    "idle":      "#7eb8f7",
    "talking":   "#a8e6cf",
    "thinking":  "#ffd3b6",
    "moving":    "#ff8b94",
    "reacting":  "#ffaaa5",
    "sleeping":  "#b0c4de",
}


class NaviWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Navi")
        self.set_default_size(200, 220)
        self.set_decorated(False)
        self.set_resizable(False)

        # Always on top, skip taskbar
        self.set_keep_above(True)

        # Transparent background
        self.add_css_class("navi-window")

        # State
        self.current_log_id   = None
        self.animation_state  = "idle"
        self.speech_text      = ""
        self.last_decision    = {}
        self._drag_start      = None

        self._build_ui()
        self._apply_css()
        self._start_brain()

    def _apply_css(self):
        css = b"""
        .navi-window {
            background: transparent;
        }
        .navi-body {
            background: rgba(20, 20, 35, 0.88);
            border-radius: 18px;
            border: 2px solid rgba(120, 180, 255, 0.5);
            padding: 12px;
        }
        .navi-face {
            font-size: 28px;
            font-family: monospace;
            color: #c8d8ff;
        }
        .navi-bubble {
            background: rgba(255,255,255,0.08);
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.15);
            padding: 8px 10px;
            color: #e8f0ff;
            font-size: 12px;
            font-family: "DejaVu Sans", sans-serif;
        }
        .navi-trait-label {
            color: rgba(200, 220, 255, 0.7);
            font-size: 10px;
        }
        button.fb-pos {
            background: rgba(100, 200, 130, 0.3);
            color: #a8f0c0;
            border-radius: 8px;
            border: 1px solid rgba(100,200,130,0.4);
            font-size: 14px;
            padding: 4px 8px;
        }
        button.fb-neg {
            background: rgba(200, 100, 100, 0.3);
            color: #f0a8a8;
            border-radius: 8px;
            border: 1px solid rgba(200,100,100,0.4);
            font-size: 14px;
            padding: 4px 8px;
        }
        button.fb-pos:hover { background: rgba(100,200,130,0.5); }
        button.fb-neg:hover { background: rgba(200,100,100,0.5); }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        self.set_child(outer)

        # Main body card
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.add_css_class("navi-body")
        outer.append(body)

        # Face / avatar
        self.face_label = Gtk.Label(label=ANIM_FACES["idle"])
        self.face_label.add_css_class("navi-face")
        body.append(self.face_label)

        # Speech bubble
        self.bubble = Gtk.Label(label="Initialising…")
        self.bubble.add_css_class("navi-bubble")
        self.bubble.set_wrap(True)
        self.bubble.set_max_width_chars(22)
        self.bubble.set_xalign(0)
        body.append(self.bubble)

        # Trait mini-display
        self.trait_label = Gtk.Label(label="")
        self.trait_label.add_css_class("navi-trait-label")
        body.append(self.trait_label)

        # Feedback row
        fb_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        fb_row.set_halign(Gtk.Align.CENTER)

        btn_pos = Gtk.Button(label="👍")
        btn_pos.add_css_class("fb-pos")
        btn_pos.connect("clicked", self._on_positive)

        btn_neg = Gtk.Button(label="👎")
        btn_neg.add_css_class("fb-neg")
        btn_neg.connect("clicked", self._on_negative)

        fb_row.append(btn_pos)
        fb_row.append(btn_neg)
        body.append(fb_row)

        # Drag support
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin",  self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        self.add_controller(drag)

    # ── Drag to move ──────────────────────────────────────────────────────────

    def _drag_begin(self, gesture, x, y):
        self._drag_start = (x, y)

    def _drag_update(self, gesture, dx, dy):
        if self._drag_start:
            sx, sy = self._drag_start
            nx = int(self.get_allocated_width()  - sx + dx)
            ny = int(self.get_allocated_height() - sy + dy)
            # GTK4: surface-level move
            surf = self.get_surface()
            if surf:
                try:
                    surf.move(nx, ny)
                except Exception:
                    pass

    # ── Feedback handlers ─────────────────────────────────────────────────────

    def _on_positive(self, btn):
        if self.current_log_id:
            record_user_feedback(self.current_log_id, "positive")
        self.bubble.set_text("Thanks! I'll remember that 💙")

    def _on_negative(self, btn):
        if self.current_log_id:
            record_user_feedback(self.current_log_id, "negative")
        self.bubble.set_text("Got it, I'll adjust…")

    # ── GTK close → record as "dismissed" ────────────────────────────────────

    def do_close_request(self):
        if self.current_log_id:
            record_user_feedback(self.current_log_id, "neutral", outcome_override="dismissed")
        return False  # allow close

    # ── UI update from brain thread ───────────────────────────────────────────

    def update_ui(self, decision: dict, outcome: str, log_id: int):
        """Called on the GTK main thread via GLib.idle_add."""
        self.current_log_id  = log_id
        anim  = decision.get("animation_state", "idle")
        action = decision.get("action", "")
        self.animation_state = anim

        # Face
        self.face_label.set_text(ANIM_FACES.get(anim, ANIM_FACES["idle"]))

        # Bubble text
        if action.startswith("speak:"):
            self.bubble.set_text(action[6:])
        elif action.startswith("notify:"):
            self.bubble.set_text(f"📢 {action[7:]}")
        elif action.startswith("wait:"):
            self.bubble.set_text(f"Taking a moment… ({action[5:]}s)")
        elif outcome == "failure":
            self.bubble.set_text("Hmm, that didn't work.")
        else:
            self.bubble.set_text(decision.get("rational_justification", "…")[:80])

        # Mini trait bar
        traits = get_traits()
        top3 = sorted(traits.items(), key=lambda x: -x[1])[:3]
        self.trait_label.set_text("  ".join(f"{k[:3]}:{int(v)}" for k, v in top3))

    # ── Brain thread ──────────────────────────────────────────────────────────

    def _start_brain(self):
        self._brain_thread = threading.Thread(target=self._brain_loop, daemon=True)
        self._brain_thread.start()

    def _brain_loop(self):
        while True:
            try:
                from navi_core import log_action, get_env_state, get_traits as gt
                from navi_core import get_user_facts, get_recent_memories, get_conn
                from navi_core import decide, execute_action, update_action_outcome
                from navi_core import apply_personality_learning, get_speak_delay

                env_state    = get_env_state()
                personality  = gt()
                user_profile = get_user_facts()
                memories     = get_recent_memories()

                with get_conn() as conn:
                    fp_rows = conn.execute(
                        "SELECT pattern, fail_count FROM failure_patterns ORDER BY fail_count DESC LIMIT 20"
                    ).fetchall()
                failure_patterns = [dict(r) for r in fp_rows]

                # Hesitance gate
                delay = get_speak_delay(personality)
                if delay > 1.0:
                    time.sleep(delay)

                decision = decide(env_state, personality, memories,
                                  user_profile, failure_patterns)
                action   = decision["action"]
                anim     = decision["animation_state"]
                just     = decision["rational_justification"]

                log_id   = log_action(env_state, action, anim, just)
                outcome, fail_reason = execute_action(action, env_state)
                update_action_outcome(log_id, outcome, fail_reason)
                apply_personality_learning(outcome, None, action)

                GLib.idle_add(self.update_ui, decision, outcome, log_id)

            except Exception as e:
                GLib.idle_add(
                    self.bubble.set_text, f"[error] {str(e)[:60]}"
                )

            time.sleep(45)


# ── App entry point ────────────────────────────────────────────────────────────

class NaviApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.navi.desktop")

    def do_activate(self):
        init_db()
        win = NaviWindow(self)
        win.present()


if __name__ == "__main__":
    app = NaviApp()
    app.run()
