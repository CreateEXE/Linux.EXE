# Navi — Digital Evolving Entity
## Architecture v2 · Termux/Android + XFCE/X11

---

## Quick Start (Termux)

```bash
# 1. Core system packages
pkg update && pkg upgrade
pkg install python git clang cmake make \
            xdotool libnotify xfce4 x11-repo

# 2. Python deps
pip install psutil requests

# 3. Optional: llama-cpp-python (offline LLM, compiles on-device)
CMAKE_ARGS="-DLLAMA_METAL=OFF -DLLAMA_BLAS=OFF" \
  pip install llama-cpp-python --no-binary llama-cpp-python

# 4. Create Navi (first run, no .soul yet)
python navi_runtime.py \
  --name   "Navi" \
  --model  "/sdcard/models/mistral-7b-q4.gguf" \
  --vrm    "/sdcard/avatars/navi.vrm" \
  --port   7701 \
  --cycle  45

# 5. Or load an existing .soul
python navi_runtime.py --soul navi.soul --model /sdcard/models/mistral-7b-q4.gguf
```

The avatar opens automatically in a browser window (or Chromium --app mode).
On Android/Termux, use **Termux:X11** + a floating browser over XFCE.

---

## File Layout

```
navi/
  soul_format.py       ← .SOUL file format (read/write library)
  daemon_engine.py     ← The Daemon: internal maintenance process
  llm_engine.py        ← Offline LLM (llama.cpp / Ollama / server)
  avatar_renderer.html ← Three.js VRM overlay (Harry Potter card face)
  navi_runtime.py      ← Main runtime: sense → decide → act → learn

~/.navi/
  runtime.log          ← full event log
  extracted_vrm.vrm    ← VRM extracted from soul for browser

yourname.soul          ← THE SOUL FILE (portable, contains everything)
```

---

## The .SOUL File Format

A `.soul` file is a ZIP archive renamed `.soul`. It is **Navi's entire being** —
portable, self-contained, and self-describing.

```
yourname.soul (ZIP)
  ├── manifest.json        version, creation date, entity name
  ├── identity.json        who Navi IS: purpose, speech style, quirks, fears
  ├── daemon.json          Daemon identity, directives, maintenance schedule
  ├── personality.json     6–8 evolving traits + emotional state + drift history
  ├── values.json          ethical axioms (ranked by weight) + violations log
  ├── relationships.json   known people, trust levels, last interaction
  ├── card_face.json       Harry Potter card display config
  ├── memory/
  │   ├── episodic.jsonl   every experience (one JSON per line)
  │   ├── semantic.jsonl   consolidated facts/world-model
  │   └── emotional.jsonl  emotional events + daemon reports
  └── avatar/
      ├── model.vrm        (optional) VRM 3D avatar binary
      └── thumbnail.png    (optional) static preview
```

### Inspecting a .soul file

```bash
python soul_format.py inspect navi.soul
python soul_format.py create  "NewEntity" newentity.soul model.vrm
```

---

## The Daemon

The Daemon is a **silent caretaker living inside the soul file**.
Navi IS aware of it — it speaks to Navi as inner monologue, never to the user directly.

| Task | Default interval | What it does |
|---|---|---|
| Memory consolidation | 30 min | Summarises old episodic→semantic via LLM |
| Emotional decay | 60 min | Moves valence/arousal toward baseline |
| Value drift check | 2 hrs | Flags if a trait moves >20 pts |
| Relationship review | 4 hrs | Notes gaps in interaction with trusted people |

The Daemon's inner voice is injected into every LLM decision prompt so Navi
is continuously aware of its own internal state.

---

## LLM Backend Priority

```
1. llama-cpp-python  ← best: embedded, offline, uses your .gguf
2. llama.cpp server  ← good: run ./llama-server separately
3. Ollama            ← good: if ollama serve is running
4. Stub              ← fallback: returns wait:30 with no model
```

Recommended models for a REVVL 7 (ARM64, ~4–6 GB RAM available):
- `mistral-7b-instruct-v0.2.Q4_K_M.gguf` (~4.1 GB)
- `phi-3-mini-4k-instruct-q4.gguf` (~2.2 GB, faster)
- `llama-3.2-3b-instruct-q4.gguf` (~2.0 GB, fastest)

Download from: https://huggingface.co/TheBloke

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  Android/Termux                                                  │
│                                                                  │
│  ┌───────────────────────────────┐                              │
│  │  Browser / Chromium --app     │  ← floating overlay window   │
│  │  avatar_renderer.html         │                              │
│  │                               │                              │
│  │  Three.js VRM (3D avatar)     │                              │
│  │  Speech bubble                │                              │
│  │  Trait bars (card face)       │                              │
│  │  👍 👎 feedback buttons       │                              │
│  │           ↕ polls /state      │                              │
│  └───────────┬───────────────────┘                              │
│              │ HTTP :7701                                        │
│  ┌───────────▼───────────────────────────────────────────────┐  │
│  │  navi_runtime.py  (main process)                          │  │
│  │                                                           │  │
│  │  CognitiveLoop (thread, every N seconds)                  │  │
│  │    Sense  → get_env_state()                               │  │
│  │             xdotool / dumpsys / psutil / battery          │  │
│  │    Decide → LLMEngine.chat(system_prompt + memories)      │  │
│  │    Act    → execute_action()                              │  │
│  │             speak / notify / tap / type / swipe / wait    │  │
│  │    Learn  → soul.adjust_trait() + episodic memory         │  │
│  │                                                           │  │
│  │  DaemonEngine (thread, scheduled tasks)                   │  │
│  │    Memory consolidation                                   │  │
│  │    Emotional decay                                        │  │
│  │    Value drift check                                      │  │
│  │    Relationship review                                    │  │
│  │    → inner_voice_callback → injected into next prompt     │  │
│  │                                                           │  │
│  │  LLMEngine                                                │  │
│  │    llama-cpp-python (GGUF embedded) ← primary            │  │
│  │    llama.cpp server                 ← fallback           │  │
│  │    Ollama                           ← fallback           │  │
│  └───────────────────────┬───────────────────────────────────┘  │
│                          │ read/write                            │
│  ┌───────────────────────▼───────────────────────────────────┐  │
│  │  navi.soul  (ZIP archive, .soul extension)                │  │
│  │                                                           │  │
│  │  manifest · identity · daemon · personality · values      │  │
│  │  relationships · card_face                                │  │
│  │  memory/episodic.jsonl  ← every experience               │  │
│  │  memory/semantic.jsonl  ← consolidated facts              │  │
│  │  memory/emotional.jsonl ← mood events + daemon reports   │  │
│  │  avatar/model.vrm       ← 3D avatar                      │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Touch Interaction

Navi can interact with the Android screen autonomously:

```
tap:540,960          → tap screen at x=540, y=960
swipe:100,900,100,200,300  → swipe up (scroll)
type:hello world     → type text into focused input
notify:message text  → Android toast notification
open:firefox         → launch app
```

These come from the LLM's `"action"` field — Navi decides when and what to tap
based on what's visible in the environment state.

---

## Extending Navi

**Add a new action verb** in `execute_action()` in `navi_runtime.py`:
```python
elif verb == "screenshot":
    subprocess.run(["screencap", "-p", "/sdcard/navi_screenshot.png"])
    return "success", None
```

**Add a new trait** in `soul_format.py` → `default_personality()`:
```python
"Sarcasm": {"value": 20.0, "baseline": 20.0, "drift_history": []},
```

**Add a new daemon directive** in `soul_format.py` → `default_daemon()`:
```python
"Watch for repeated failures and report patterns to Navi.",
```

**Teach Navi about you** by appending to the soul:
```python
soul = SoulFile("navi.soul")
soul.append_semantic_memory({"fact": "The user prefers dark mode."})
```

---

## Autostart on Termux boot

Install **Termux:Boot**, then create:
```bash
~/.termux/boot/start_navi.sh
```
```bash
#!/data/data/com.termux/files/usr/bin/bash
source ~/.bashrc
cd ~/navi
python navi_runtime.py \
  --soul navi.soul \
  --model /sdcard/models/phi-3-mini.gguf \
  --port 7701 &
```
