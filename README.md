# Flow8-LLM: AI Mixing Brain untuk Behringer Flow 8

Sistem mixing otomatis berbasis LLM + analisis audio real-time untuk mengontrol Behringer Flow 8 digital mixer via MIDI USB.

Ketik perintah bahasa natural → LLM menganalisis → MIDI commands dikirim ke mixer. Bisa juga auto-mix: LLM menganalisis spektrum audio dan menyesuaikan mix secara otomatis.

---

## Arsitektur

```
┌─────────────────────────────────────────────────────────────────┐
│                        Flow8-LLM Core                           │
├──────────┬───────────┬────────────┬─────────────────────────────┤
│  Audio   │   MIDI    │    LLM     │      Brain                  │
│  Engine  │  Driver   │   Engine   │   (Decision Engine)         │
│          │           │            │                              │
│  FFT     │  Flow 8   │  Ollama/   │  Auto-mix                  │
│  RMS     │  USB MIDI │  OpenAI    │  Gain staging              │
│  LUFS    │  mapping  │  chat API  │  Feedback suppression      │
│  bands   │  CC cmds  │  prompt    │  Scene automation          │
├──────────┴───────────┴────────────┴─────────────────────────────┤
│                       State Manager                             │
│    Snapshot save/recall • Preset library • Undo/redo history    │
├─────────────────────────────────────────────────────────────────┤
│                      TUI Dashboard                              │
│    ASCII spectrum • VU meters • Channel strips • Command console│
└─────────────────────────────────────────────────────────────────┘

User (text) → LLM parses → Brain decides → MIDI sends → Flow 8 adjusts
                    ↑                              │
                    └──── Audio analysis ←─────────┘
```

---

## Fitur

### 1. MIDI Driver (`src/core/midi.py` — 343 baris)

Full MIDI implementation untuk Behringer Flow 8 berdasarkan official Quick Start Guide.

- **Complete CC mapping**: Gain, Fader, Pan, Mute, Solo, EQ (4-band), Low Cut, FX Sends, Bus Limiters, Phantom 48V
- **Semua MIDI channels**: Ch 1-8 inputs, Main, Mon1, Mon2, FX1, FX2, USB/BT (skip channel 13)
- **Bidirectional converters**: dB ↔ CC, Hz ↔ CC, Pan ↔ CC, Q ↔ CC
- **Command batching**: Kirim multiple MIDI commands dengan timing control
- **State tracking**: Track semua parameter yang dikirim

```
Flow 8 MIDI Value Formulas:
  Fader:    CC = ((dB + 70) * 126 / 80) + 1      [-70 to +10 dB]
  Gain:     CC = (dB + 20) * 127 / 80             [-20 to +60 dB]
  EQ:       CC = (dB + 15) * 127 / 30             [-15 to +15 dB]
  Low Cut:  CC = (Hz - 20) * 127 / 580            [20 to 600 Hz]
  Pan:      CC = (setting + 1) * 127 / 2           [-1.0 to +1.0]
  Limiter:  CC = (dB + 30) * 127 / 30             [-30 to 0 dB]
```

### 2. Audio Engine (`src/core/audio.py` — 488 baris)

Real-time audio analysis dari Flow 8 USB interface.

- **FFT Spectrum Analyzer**: Hann window, configurable FFT size (default 2048)
- **Level Metering**: RMS + Peak dengan ballistics (300ms integration, peak hold + decay)
- **LUFS Loudness**: K-weighted loudness measurement (ITU-R BS.1770 approximation)
- **Frequency Bands**: Sub (20-60Hz), Low (60-250), Low-Mid (250-500), Mid (500-2k), High-Mid (2k-4k), High (4k-8k), Air (8k-20k)
- **Feedback Detector**: Sustained narrow-band peak detection (configurable threshold, sustain count)
- **Crest Factor**: Peak - RMS = dynamic range indicator
- **Clipping Detection**: Peak > 0.99 = clipping flag

### 3. LLM Engine (`src/core/llm.py` — 517 baris)

Natural language → mixing commands via LLM.

- **Multi-provider**: Ollama (local), OpenAI, Anthropic, custom endpoints
- **Structured output**: Parsing JSON responses (object + array formats, 3-strategy parser)
- **Chain-of-thought**: LLM memberikan reasoning untuk setiap keputusan mixing
- **Confidence scoring**: LLM rate kepercayaan (filter auto-adjust di bawah 60%)
- **Conversation management**: Context window control, history trimming
- **Audio-aware prompts**: Kirim spectral analysis data ke LLM untuk keputusan informed

**Supported actions (23 jenis):**
```
set_fader, set_gain, set_eq, set_eq_freq, set_eq_q,
set_lowcut, set_lowcut_en, set_pan, mute, unmute,
solo, unsolo, set_fx_send, set_fx_type, set_fx_time,
set_fx_feedback, set_fx_mix, set_limiter,
save_snapshot, recall_snapshot, apply_preset,
reset_channel, explain
```

### 4. Brain (`src/core/brain.py` — 599 baris)

Central decision engine yang orchestrate semua komponen.

- **5 Operating Modes**: Manual, Assisted, Auto, Scene, Learn
- **Natural language interface**: `brain.process("boost vocal presence")` → LLM → MIDI
- **Auto-mix step**: Analyze audio → LLM suggests → filter by confidence → execute
- **Quick gain staging**: Auto-set semua gains supaya peak di -18 dBFS
- **Feedback fix**: Detect feedback frequency → find source channel → apply narrow EQ cut
- **Scene playback**: Timed sequence of actions (seperti lighting cue list)
- **Event system**: Callbacks untuk action, analysis, mode_change, error
- **Full state**: `brain.status()` → MIDI, Audio, LLM, State dalam satu dict

### 5. State Manager (`src/core/state.py` — 658 baris)

Mixer state management dengan snapshots, presets, dan undo/redo.

- **MixerState**: Complete state untuk 8 channels + buses + FX processors
  - Per-channel: gain, fader, pan, mute, solo, lowcut, 4-band EQ (gain + freq + Q), FX sends
  - Buses: Main, Mon1, Mon2 dengan fader + limiter
  - FX: Type, time, feedback, tone, mix
- **Snapshots**: Save/recall/delete/export full mixer state (JSON)
- **Presets** (10 built-in):
  ```
  vocal_male    — warm, present (lowcut 80Hz, +3dB 3kHz, +2dB 10kHz, reverb send)
  vocal_female  — bright, airy (lowcut 100Hz, +4dB 4kHz, +3dB 12kHz)
  kick          — punchy, tight (gain +5, lowcut 30Hz, +4dB 60Hz, -3dB 300Hz)
  snare         — crack, body (lowcut 80Hz, +4dB 5kHz)
  guitar_acoustic — natural, shimmer (lowcut 100Hz, +2dB 5kHz, +3dB 12kHz)
  guitar_electric — full, present (lowcut 80Hz, +3dB 2.5kHz)
  bass          — warm, defined (lowcut 25Hz, +2dB 80Hz, +2dB 1kHz)
  keys          — full range, clear (lowcut 40Hz, +2dB 3kHz)
  podcast_voice — radio-ready (lowcut 100Hz, -4dB 250Hz, +3dB 3.5kHz)
  flat          — no processing (default)
  ```
- **Undo/Redo**: 200-entry history dengan position tracking
- **State serialization**: Export/import mixer state sebagai JSON

### 6. Auto-Mix Engine (`src/engines/automix.py` — 272 baris)

Intelligent auto-mixing berdasarkan real-time audio analysis.

- **Per-channel analysis**:
  - Clipping → emergency gain reduction
  - Peak too high → reduce gain for headroom
  - Signal too low → boost gain
  - Muddy low-mid buildup → cut lowmid EQ
  - Harsh high-mid → gentle highmid cut
- **Mix-level masking detection**: Compare band energies across channels, suggest EQ carve-outs
- **Auto-panning**: Stereo width suggestions berdasarkan instrument type
- **Genre awareness**: Different strategies untuk vocal, band, podcast, DJ, broadcast
- **Configurable targets**: target_rms, max_peak, min_headroom, EQ limits

### 7. Feedback Engine (`src/engines/feedback.py` — 147 baris)

Automatic feedback detection and suppression.

- **Detection**: Monitor audio untuk sustained narrow-band peaks (threshold configurable)
- **Source identification**: Find channel dengan energy di feedback frequency
- **Suppression**: Apply narrow notch filter (high Q, -6dB) pada source channel
- **Notch tracking**: Track semua notch filters yang di-apply
- **Severity rating**: Rate severity feedback (0-100%)
- **History log**: Log semua feedback events dengan timestamp, frequency, action taken

### 8. Scene Engine (`src/engines/scenes.py` — 276 baris)

Scene automation untuk live shows (seperti lighting console cue list).

- **Scene Lists**: Named sequences of cues
- **Cues**: Name, snapshot reference, delay, auto-advance flag, additional actions
- **Playback**: GO (advance), Back (previous), Jump to cue index
- **Templates**: Live band (9 cues), Podcast (4 cues)
- **Persistence**: Save/load scene lists dari disk (JSON)

### 9. Preset Library (`src/engines/presets.py` — 144 baris)

Extended preset management.

- **Import/Export**: Single preset atau full preset pack (JSON)
- **Genre Packs**: Rock, Jazz, EDM dengan instrument-specific presets
- **Custom presets**: Save user-created presets ke disk

### 10. TUI Dashboard (`src/tui/dashboard.py` — 354 baris)

Terminal User Interface untuk monitoring dan control.

- **Spectrum Visualizer**: ASCII art FFT display dengan color coding (Cyan bars)
- **VU Meters**: Horizontal meters dengan green/yellow/red zones + peak hold indicator
- **Channel Strips**: Side-by-side display (mute/solo indicator, level, fader, pan, EQ summary)
- **Mixer State**: Summary of non-default channels
- **Action History**: Recent 10 actions dengan timestamp dan success/fail
- **Command Console**: Input line untuk natural language commands
- **Status Bar**: Mode, MIDI connection, Audio status, LLM model

---

## Struktur Proyek (Aktual)

```
flow8-llm/
├── src/
│   ├── __init__.py              — Package init, version
│   ├── core/
│   │   ├── __init__.py
│   │   ├── midi.py              — 343  │ MIDI driver, CC mapping, converters
│   │   ├── audio.py             — 488  │ FFT, meters, LUFS, feedback detection
│   │   ├── llm.py               — 517  │ Ollama/OpenAI, prompt, parser
│   │   ├── brain.py             — 599  │ Decision engine, auto-mix, scenes
│   │   └── state.py             — 658  │ Snapshots, presets, undo/redo
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── automix.py           — 272  │ Auto-mix, masking, auto-pan
│   │   ├── feedback.py          — 147  │ Feedback detection + suppression
│   │   ├── scenes.py            — 276  │ Scene lists, cue playback
│   │   └── presets.py           — 144  │ Preset library, genre packs
│   ├── tui/
│   │   ├── __init__.py
│   │   └── dashboard.py         — 354  │ TUI: spectrum, VU, strips, console
│   └── cli.py                   — 405  │ CLI entry point, interactive mode
├── tests/
│   └── test_all.py              — 288  │ 18 unit tests (all passing)
├── flow8.py                     — 13   │ Entry point
├── requirements.txt             — 8    │ Dependencies
└── README.md                    — ini
                                  ─────
                           Total: ~4,100 baris
```

---

## Instalasi

```bash
cd /root/flow8-llm

# Core dependencies
pip install mido numpy requests

# Optional: MIDI port access (needs hardware)
pip install python-rtmidi

# Optional: Real-time audio capture
pip install sounddevice
```

**System dependencies** (untuk python-rtmidi):
```bash
apt-get install libasound2-dev
```

---

## Penggunaan

### Interactive Mode
```bash
python3 flow8.py --dry-run     # Tanpa hardware (test mode)
python3 flow8.py               # Dengan Flow 8 terhubung
```

```
> set channel 1 gain to 25dB
  Processing...
  ✓ 1 command(s) sent
  Confidence: 92%

> mute channel 3 and 4
  Processing...
  ✓ 2 command(s) sent

> vocal preset for channel 1
  Processing...
  ✓ 5 command(s) sent

> boost high EQ on channel 2 by 6dB with narrow Q
  Processing...
  ✓ 2 command(s) sent

> explain
  Analyzing mix...
  [LLM memberikan analisis bahasa natural tentang kondisi mix saat ini]
```

### Single Command
```bash
python3 flow8.py "set channel 1 fader to -10dB"
python3 flow8.py "pan channel 3 hard left"
python3 flow8.py "apply preset kick to channel 2"
```

### Auto-Mix Mode
```bash
python3 flow8.py --automix
# LLM analyze audio → suggest → execute (loop setiap 3 detik)
# Ctrl+C untuk stop
```

### TUI Dashboard
```bash
python3 flow8.py --dashboard
# Full-screen: spectrum, VU meters, channel strips, command console
```

### Scene Mode
```bash
python3 flow8.py --scene "live_band"
# Load snapshot sequence
```

### Dry Run (tanpa MIDI hardware)
```bash
python3 flow8.py --dry-run
# Semua command di-print, tidak dikirim ke MIDI
```

---

## Built-in Commands

| Command | Description |
|---------|-------------|
| `set channel N gain dB` | Set preamp gain (-20 to +60) |
| `set channel N fader dB` | Set fader level (-70 to +10) |
| `set channel N eq band dB` | Set EQ (low/lowmid/highmid/high) |
| `set channel N pan value` | Set pan (-1.0 L to +1.0 R) |
| `set channel N lowcut Hz` | Set high-pass filter |
| `mute channel N` | Mute channel |
| `unmute channel N` | Unmute channel |
| `solo channel N` | Solo (PFL) |
| `apply preset NAME to N` | Apply preset to channel |
| `list presets` | Show available presets |
| `save snapshot NAME` | Save current state |
| `recall snapshot NAME` | Restore saved state |
| `list snapshots` | Show saved snapshots |
| `gain staging` | Auto-set all gains |
| `fix feedback` | Detect and fix feedback |
| `reset channel N` | Reset to defaults |
| `automix` | Start auto-mix loop |
| `explain` | LLM explains current mix |
| `analyze` | Show audio analysis |
| `undo` | Undo last action |
| `status` | Show system status |

---

## MIDI Implementation (Behringer Flow 8)

### Channel Mapping
```
MIDI Ch 0  = Input Ch 1 (XLR, phantom)
MIDI Ch 1  = Input Ch 2 (XLR, phantom)
MIDI Ch 2  = Input Ch 3 (XLR/TRS combo)
MIDI Ch 3  = Input Ch 4 (XLR/TRS combo)
MIDI Ch 4  = Stereo 5/6 Left
MIDI Ch 5  = Stereo 5/6 Right
MIDI Ch 6  = Stereo 7/8 Left
MIDI Ch 7  = Stereo 7/8 Right
MIDI Ch 8  = Main Bus
MIDI Ch 9  = Monitor 1
MIDI Ch 10 = Monitor 2
MIDI Ch 11 = FX1 Bus
MIDI Ch 12 = FX2 Bus
MIDI Ch 13 = (TIDAK DIGUNAKAN)
MIDI Ch 14 = USB/Bluetooth
MIDI Ch 15 = Global
```

### CC Numbers
```
CC  8 = Gain (-20 to +60 dB)
CC  9 = Fader (-70 to +10 dB)
CC 10 = Pan (-1.0 to +1.0)
CC 11 = Mute (0=off, 127=on)
CC 12 = Solo (0=off, 127=on)
CC 13 = Bus Limiter (-30 to 0 dB)
CC 16 = FX1 Send
CC 17 = FX2 Send
CC 18 = Mon1 Send
CC 19 = Mon2 Send
CC 20 = FX Type
CC 21 = FX Time
CC 22 = FX Feedback
CC 23 = FX Tone
CC 24 = FX Mix
CC 28 = 48V Phantom
CC 30 = BT Level
CC 31 = USB Level
CC 63 = Low Cut Enable
CC 64 = Low Cut Frequency
CC 65 = EQ Low Gain
CC 66 = EQ Low-Mid Gain
CC 67 = EQ High-Mid Gain
CC 68 = EQ High Gain
CC 69 = EQ Low Frequency
CC 70 = EQ Low-Mid Frequency
CC 71 = EQ High-Mid Frequency
CC 72 = EQ High Frequency
CC 73 = EQ Low-Mid Q
CC 74 = EQ High-Mid Q
```

### Catatan Penting
- Flow 8 **menerima** MIDI via USB tapi **tidak mengirim** (one-way)
- Semua MIDI channel digunakan **kecuali channel 13**
- Firmware V11739+ menambahkan parameter EQ tambahan

---

## LLM Providers

### Ollama (Recommended — Local, Gratis)
```bash
ollama serve
ollama pull gemma3:4b

python3 flow8.py --model gemma3:4b
```

### OpenAI
```bash
python3 flow8.py --provider openai --model gpt-4o --api-key sk-xxx
```

### Custom Endpoint
```bash
python3 flow8.py --ollama http://192.168.1.100:11434 --model llama3:8b
```

---

## Testing

```bash
cd /root/flow8-llm
python3 tests/test_all.py
```

```
=== Flow8-LLM Tests ===

[MIDI]
  ✓ Fader conversion
  ✓ Gain conversion
  ✓ EQ conversion
  ✓ Frequency conversion
  ✓ Pan conversion
  ✓ Q factor conversion
  ✓ MIDI command creation
  ✓ MIDI batch

[Audio]
  ✓ Spectrum analyzer
  ✓ Level meter
  ✓ Audio engine

[State]
  ✓ Channel state
  ✓ Mixer state
  ✓ Snapshot
  ✓ State manager

[LLM]
  ✓ LLM response parsing
  ✓ Array response parsing
  ✓ Conversation manager

[Brain]
  ✓ Brain dry-run execution
  ✓ Brain mode switching

✅ All tests passed!
```

---

## Data Flow

```
1. User types: "vocal preset for channel 1"
                │
                ▼
2. LLM Engine sends prompt to Ollama/OpenAI
   + includes audio analysis context (if available)
                │
                ▼
3. LLM returns JSON:
   {"reasoning": "Applying vocal preset...",
    "actions": [
      {"action": "set_lowcut", "channel": 1, "hz": 80},
      {"action": "set_eq", "channel": 1, "band": "lowmid", "db": -3},
      ...
    ],
    "confidence": 0.92}
                │
                ▼
4. Brain executes each action:
   - Updates StateManager (ChannelState)
   - Generates MidiBatch from state changes
   - Sends MIDI commands to Flow 8 via USB
                │
                ▼
5. Flow 8 mixer adjusts parameters
                │
                ▼
6. Audio Engine captures output (if running):
   - FFT analysis → spectrum
   - Level metering → RMS/Peak
   - Feedback detection
                │
                ▼
7. TUI Dashboard updates display:
   - Spectrum bars
   - VU meters
   - Channel strip values
   - Action log
```

---

## License

MIT
