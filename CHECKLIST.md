# Behringer Flow 8 — Complete Feature Verification Checklist

Status setiap fitur di Flow8-LLM. ✅ = sudah, ❌ = belum, ⚠️ = partial, N/A = tidak perlu.

---

## 1. INPUTS

### Channel 1-2 (XLR Mic)
| Feature | MIDI | Flow8-LLM | Notes |
|---------|------|-----------|-------|
| Gain (0/+10/+20/+30/+40 dB) | CC 8 | ✅ `set_gain` | Ch 1-4 only |
| +48V Phantom Power | CC 28 | ❌ | Belum ada command |
| Low Cut (20-600 Hz) | CC 64 + CC 63 | ✅ `set_lowcut` | |
| 4-Band EQ (Low/LM/HM/Hi) | CC 65-74 | ✅ `set_eq` | Gain + Freq + Q |
| Compressor | - | ❌ | Tidak ada di MIDI spec |
| Pan | CC 10 | ✅ `set_pan` | |
| Fader | CC 9 | ✅ `set_fader` | |
| Mute | CC 11 | ✅ `mute` | |
| Solo/PFL | CC 12 | ✅ `solo` | |
| FX1 Send (Reverb) | CC 16 | ✅ `set_fx_send` | |
| FX2 Send (Delay) | CC 17 | ✅ `set_fx_send` | |
| Mon1 Send | CC 18 | ⚠️ | Ada di CC def, belum di execute |
| Mon2 Send | CC 19 | ⚠️ | Ada di CC def, belum di execute |

### Channel 3-4 (XLR/TRS Combo)
| Feature | MIDI | Flow8-LLM | Notes |
|---------|------|-----------|-------|
| Gain (+20/+0 dB switch) | CC 8 | ✅ | 2-step analog gain |
| +48V Phantom (external) | - | N/A | Butuh PS400 eksternal |
| All other params | same as Ch 1-2 | ✅ | |

### Channel 5/6 (Stereo Pair)
| Feature | MIDI | Flow8-LLM | Notes |
|---------|------|-----------|-------|
| Hi-Z input (Right jack) | - | N/A | Hardware switch |
| Stereo link | - | N/A | Automatic |
| Pan/Balance | CC 10 | ✅ | Balance mode |
| All other params | same as Ch 1-4 | ✅ | |

### Channel 7/8 (Stereo Pair)
| Feature | MIDI | Flow8-LLM | Notes |
|---------|------|-----------|-------|
| Same as Ch 5/6 | - | ✅ | |

### BT/USB Channel
| Feature | MIDI | Flow8-LLM | Notes |
|---------|------|-----------|-------|
| BT Level | CC 30 | ⚠️ | Ada di CC def |
| USB Level | CC 31 | ⚠️ | Ada di CC def |
| EQ (4-band) | CC 65-74 | ⚠️ | Midi Ch 14 |
| FX Sends | CC 16-17 | ⚠️ | Midi Ch 14 |
| Mon Sends | CC 18-19 | ⚠️ | Midi Ch 14 |
| Balance | CC 10 | ⚠️ | Midi Ch 14 |

---

## 2. EQ (4-Band Parametric Per Channel)

| Band | Gain CC | Freq CC | Q CC | Range |
|------|---------|---------|------|-------|
| Low | 65 | 69 | - | ±15 dB, 20-600 Hz, fixed Q |
| Low-Mid | 66 | 70 | 73 | ±15 dB, 100-8000 Hz, Q 0.1-10 |
| High-Mid | 67 | 71 | 74 | ±15 dB, 200-16000 Hz, Q 0.1-10 |
| High | 68 | 72 | - | ±15 dB, 1000-20000 Hz, fixed Q |

| Feature | Flow8-LLM | Notes |
|---------|-----------|-------|
| EQ Gain | ✅ `set_eq` | |
| EQ Frequency | ✅ `set_eq_freq` | |
| EQ Q (Low-Mid, High-Mid) | ✅ `set_eq_q` | |
| Low Cut Frequency | ✅ `set_lowcut` | |
| Low Cut Enable/Disable | ✅ `set_lowcut_en` | |

---

## 3. EFFECTS

### FX1 — Hall Reverb (MIDI Ch 11)
| Preset | Type | Name |
|--------|------|------|
| 0 | Reverb | Hall 1 |
| 1 | Reverb | Hall 2 |
| 2 | Reverb | Room 1 |
| 3 | Reverb | Room 2 |
| 4 | Reverb | Room 3 |
| 5 | Reverb | Chamber |
| 6 | Reverb | Plate 1 |
| 7 | Reverb | Plate 2 |
| 8 | Reverb | Spring 1 |
| 9 | Reverb | Spring 2 |
| 10 | Reverb | Ambient 1 |
| 11 | Reverb | Ambient 2 |
| 12 | Flanger | Flanger |
| 13 | Chorus | Chorus 1 (continuous) |
| 14 | Chorus | Chorus 2 (switch) |
| 15 | Chorus | Chorus 3 |

| Param | CC | Range | Flow8-LLM |
|-------|-----|-------|-----------|
| Type Select | 20 | 0-15 | ✅ `set_fx_type` |
| Time | 21 | varies | ⚠️ Defined |
| Feedback | 22 | 0-127 | ⚠️ Defined |
| Tone | 23 | 0-127 | ⚠️ Defined |
| Mix | 24 | 0-100% | ✅ `set_fx_mix` |
| Mute FX | - | - | ❌ |
| Level to Mon1 | - | - | ❌ |
| Level to Mon2 | - | - | ❌ |
| Level to Main | - | - | ❌ |

### FX2 — Delay/Modulation (MIDI Ch 12)
| Preset | Type | Name |
|--------|------|------|
| 0 | Delay | Delay 1 |
| 1 | Delay | Delay 2 |
| 2 | Delay | Echo 1 |
| 3 | Delay | Echo 2 |
| 4 | Delay | Ping Pong |
| 5 | Delay | Tape Echo |
| 6 | Delay | Slapback |
| 7 | Delay | Multi-Tap |
| 8 | Delay | Analog |
| 9 | Delay | Digital |
| 10 | Delay | Stereo |
| 11 | Delay | Filtered |
| 12 | Flanger | Flanger |
| 13 | Chorus | Chorus 1 (continuous) |
| 14 | Chorus | Chorus 2 (switch) |
| 15 | Chorus | Chorus 3 |

| Param | CC | Flow8-LLM |
|-------|-----|-----------|
| Type Select | 20 | ✅ `set_fx_type` |
| Tap Tempo | Note On (Note 0) | ❌ |
| Time | 21 | ⚠️ Defined |
| Feedback | 22 | ⚠️ Defined |
| Tone | 23 | ⚠️ Defined |
| Mix | 24 | ✅ `set_fx_mix` |

---

## 4. MAIN OUTPUT

| Feature | MIDI | Flow8-LLM | Notes |
|---------|------|-----------|-------|
| Main Fader | CC 9 (Ch 8) | ✅ `set_fader("main")` | |
| Main Mute | CC 11 (Ch 8) | ✅ `mute("main")` | |
| 9-Band Parametric EQ | ❌ | ❌ | Tidak ada di MIDI spec |
| Limiter | CC 13 | ⚠️ | Ada di CC def, belum di execute |
| Main L/R Output | - | N/A | Analog hardware |

---

## 5. MONITOR OUTPUTS

### Monitor 1 (MIDI Ch 9)
| Feature | Flow8-LLM | Notes |
|---------|-----------|-------|
| Mon1 Fader | ⚠️ | Midi Ch 9, CC 9 |
| Mon1 Mute | ⚠️ | Midi Ch 9, CC 11 |
| 9-Band EQ | ❌ | Tidak ada di MIDI spec |
| Limiter | ⚠️ | Midi Ch 9, CC 13 |

### Monitor 2 (MIDI Ch 10)
| Feature | Flow8-LLM | Notes |
|---------|-----------|-------|
| Mon2 Fader | ⚠️ | Midi Ch 10, CC 9 |
| Mon2 Mute | ⚠️ | Midi Ch 10, CC 11 |
| 9-Band EQ | ❌ | Tidak ada di MIDI spec |
| Limiter | ⚠️ | Midi Ch 10, CC 13 |

---

## 6. HEADPHONE OUTPUT

| Feature | Flow8-LLM | Notes |
|---------|-----------|-------|
| Source: Main Bus | N/A | Hardware/firmware switch |
| Source: Mon1/2 Bus | N/A | |
| Pre/Post Fader | N/A | |
| Volume | N/A | Physical knob |

---

## 7. USB AUDIO INTERFACE

| Feature | Flow8-LLM | Notes |
|---------|-----------|-------|
| Recording: Ch 1-4 post-gain | N/A | Hardware/firmware |
| Recording: Ch 5/6 source select | N/A | |
| Streaming: Main L/R post-fader | N/A | |
| Playback: In 1/2 = Main | N/A | |
| Playback: In 3...10 = Streaming | N/A | |
| USB audio capture | ✅ `start audio` | Via sounddevice |

---

## 8. BLUETOOTH

| Feature | Flow8-LLM | Notes |
|---------|-----------|-------|
| BT Audio Input | N/A | Hardware |
| BT Level Control | CC 30 | ⚠️ Defined |
| BT App Control | N/A | Behringer FLOW app |
| BT pairing | N/A | Hardware button |

---

## 9. SNAPSHOTS

| Feature | MIDI | Flow8-LLM | Notes |
|---------|------|-----------|-------|
| Save Snapshot (up to 15) | MIDI Dump | ✅ `save snapshot` | Software-side |
| Recall Snapshot | MIDI Dump | ✅ `recall snapshot` | |
| MIDI Dump Request | SysEx | ❌ | Tidak diimplement |
| Footswitch: Snapshot Recall | - | ❌ | |
| Crossfade between snapshots | - | ❌ | Feature request di Behringer |

---

## 10. FOOTSWITCH

| Function | Flow8-LLM | Notes |
|----------|-----------|-------|
| Snapshot Recall | ❌ | Hardware |
| Tap Tempo | ❌ | Hardware |
| FX Mute Toggle | ❌ | Hardware |
| Mute All | ❌ | Hardware |

---

## 11. GLOBAL

| Feature | MIDI CC | Flow8-LLM |
|---------|---------|-----------|
| 48V Phantom (Ch 1-2) | CC 28 | ❌ |
| BT Level | CC 30 | ⚠️ |
| USB Level | CC 31 | ⚠️ |
| Tap Tempo (FX2) | Note 0 | ❌ |

---

## 12. MIDI PROTOCOL

| Feature | Status |
|---------|--------|
| One-way (receive only) | ✅ Implemented |
| All channels except 13 | ✅ Implemented |
| CC Value formulas | ✅ Implemented |
| Snapshot MIDI Dump | ❌ Not implemented |
| Tap Tempo MIDI Note | ❌ Not implemented |
| Program Change | ❌ Not implemented |

---

## 13. LLM INTEGRATION

| Feature | Status |
|---------|--------|
| Natural language parsing | ✅ |
| Ollama provider | ✅ |
| OpenAI provider | ✅ |
| Audio context injection | ✅ |
| Chain-of-thought reasoning | ✅ |
| Confidence scoring | ✅ |
| Multi-action responses | ✅ |
| Indonesian language support | ✅ (via LLM) |

---

## 14. AUDIO ANALYSIS

| Feature | Status |
|---------|--------|
| FFT Spectrum | ✅ |
| RMS Level | ✅ |
| Peak Level | ✅ |
| LUFS Loudness | ✅ |
| Frequency bands (7) | ✅ |
| Feedback detection | ✅ |
| Clipping detection | ✅ |
| Crest factor | ✅ |

---

## 15. AUTO-MIX

| Feature | Status |
|---------|--------|
| Auto gain staging | ✅ |
| Frequency masking detection | ✅ |
| Auto-panning | ✅ |
| Auto-EQ suggestion | ✅ |
| Feedback auto-fix | ✅ |
| Confidence filter | ✅ |

---

## 16. SCENES

| Feature | Status |
|---------|--------|
| Scene lists | ✅ |
| Cue playback (GO) | ✅ |
| Go back | ✅ |
| Jump to cue | ✅ |
| Templates (live band, podcast) | ✅ |
| Persistence (JSON) | ✅ |

---

## 17. TUI

| Feature | Status |
|---------|--------|
| ASCII spectrum | ✅ |
| VU meters | ✅ |
| Channel strips | ✅ |
| Command console | ✅ |
| Command menu (/) | ✅ |
| Numbered selection | ✅ |

---

## SUMMARY

| Category | Total | ✅ | ❌ | ⚠️ |
|----------|-------|-----|-----|-----|
| Inputs (Ch 1-4) | 12 | 10 | 1 | 2 |
| Inputs (Ch 5-8) | 8 | 6 | 0 | 2 |
| BT/USB | 6 | 0 | 0 | 6 |
| EQ | 5 | 5 | 0 | 0 |
| Effects | 14 | 3 | 2 | 9 |
| Main Output | 4 | 2 | 1 | 1 |
| Monitor Outputs | 8 | 0 | 2 | 6 |
| Snapshots | 5 | 2 | 2 | 1 |
| Footswitch | 4 | 0 | 4 | 0 |
| Global | 4 | 0 | 1 | 3 |
| MIDI Protocol | 5 | 3 | 2 | 0 |
| LLM | 8 | 8 | 0 | 0 |
| Audio Analysis | 8 | 8 | 0 | 0 |
| Auto-Mix | 6 | 6 | 0 | 0 |
| Scenes | 6 | 6 | 0 | 0 |
| TUI | 6 | 6 | 0 | 0 |
| **TOTAL** | **103** | **65** | **13** | **25** |

---

## PRIORITY TODO (❌ items yang perlu ditambah)

### High Priority
1. [ ] Phantom 48V control (CC 28)
2. [ ] FX Mute (toggle both FX on/off)
3. [ ] Mute All command
4. [ ] Mon1/Mon2 send levels (full implementation)
5. [ ] Tap Tempo via MIDI Note

### Medium Priority
6. [ ] Limiter control (Main, Mon1, Mon2)
7. [ ] BT/USB level control
8. [ ] MIDI Dump for snapshot exchange with hardware
9. [ ] FX routing to Mon1/Mon2/Main
10. [ ] Main/Mon 9-band EQ (jika ada MIDI mapping)

### Low Priority
11. [ ] Program Change support
12. [ ] Footswitch emulation
13. [ ] Per-channel compressor (jika MIDI mapping ditemukan)
14. [ ] Snapshot crossfade
