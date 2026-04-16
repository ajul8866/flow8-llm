"""Tests for Flow8-LLM core modules."""

import sys
import json
import math
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.midi import (
    MidiBus, CC, Convert, MidiCmd, MidiBatch, MidiDriver,
)
from src.core.audio import (
    SpectrumAnalyzer, LevelMeter, AudioEngine, BANDS,
)
from src.core.state import (
    MixerState, ChannelState, StateManager, Snapshot,
)
from src.core.llm import (
    LLMConfig, LLMProvider, parse_llm_response, Conversation,
)
from src.core.brain import Brain, BrainMode


# ─── MIDI Tests ────────────────────────────────────────────────────────

def test_convert_fader():
    assert Convert.fader_to_cc(-70) == 1
    assert Convert.fader_to_cc(0) == 111
    assert Convert.fader_to_cc(10) == 127
    assert abs(Convert.cc_to_fader(111) - 0.0) < 0.5
    print("  ✓ Fader conversion")

def test_convert_gain():
    assert Convert.gain_to_cc(-20) == 0
    assert Convert.gain_to_cc(0) == 31
    assert Convert.gain_to_cc(30) == 79
    assert Convert.gain_to_cc(60) == 127
    print("  ✓ Gain conversion")

def test_convert_eq():
    assert Convert.eq_to_cc(0) == 63
    assert Convert.eq_to_cc(15) == 127
    assert Convert.eq_to_cc(-15) == 0
    assert abs(Convert.cc_to_eq(63)) < 0.5
    print("  ✓ EQ conversion")

def test_convert_freq():
    assert Convert.freq_to_cc(20) == 0
    assert Convert.freq_to_cc(600) == 127
    assert Convert.freq_to_cc(100, 20, 600) == 17
    print("  ✓ Frequency conversion")

def test_convert_pan():
    assert Convert.pan_to_cc(-1.0) == 0
    assert Convert.pan_to_cc(0.0) == 63
    assert Convert.pan_to_cc(1.0) == 127
    assert abs(Convert.cc_to_pan(63)) < 0.02
    print("  ✓ Pan conversion")

def test_convert_q():
    assert abs(Convert.cc_to_q(Convert.q_to_cc(1.0)) - 1.0) < 0.1
    assert abs(Convert.cc_to_q(Convert.q_to_cc(5.0)) - 5.0) < 0.5
    print("  ✓ Q factor conversion")

def test_midi_cmd():
    cmd = MidiCmd(MidiBus.CH1, CC.GAIN.cc, 79, "Ch1 gain +30dB")
    msg = cmd.to_msg()
    assert msg.type == "control_change"
    assert msg.channel == 0
    assert msg.control == 8
    assert msg.value == 79
    print("  ✓ MIDI command creation")

def test_midi_batch():
    batch = MidiBatch(name="test")
    batch.add(MidiCmd(MidiBus.CH1, CC.FADER.cc, 100))
    batch.add(MidiCmd(MidiBus.CH2, CC.FADER.cc, 90))
    assert len(batch) == 2
    print("  ✓ MIDI batch")


# ─── Audio Tests ───────────────────────────────────────────────────────

def test_spectrum_analyzer():
    import numpy as np
    sa = SpectrumAnalyzer(sample_rate=48000, fft_size=2048)

    # Generate 1kHz test tone
    t = np.linspace(0, 0.1, 4800, False)
    signal = 0.5 * np.sin(2 * np.pi * 1000 * t)

    magnitudes, freqs = sa.analyze(signal)
    assert len(magnitudes) > 0
    assert len(freqs) == len(magnitudes)

    # 1kHz should be in the "mid" band
    mid_energy = sa.band_energy(magnitudes, BANDS[3])  # mid band
    sub_energy = sa.band_energy(magnitudes, BANDS[0])  # sub band
    assert mid_energy > sub_energy
    print("  ✓ Spectrum analyzer")

def test_level_meter():
    import numpy as np
    lm = LevelMeter(sample_rate=48000)

    # Feed enough samples to fill the integration window
    signal = 0.5 * np.sin(np.linspace(0, 0.5, 24000))
    rms, peak = lm.update(signal)
    assert rms > -100  # Has signal (not silence)
    assert peak > -100  # Peak detected
    print("  ✓ Level meter")

def test_audio_engine():
    import numpy as np
    ae = AudioEngine(channels=4)

    # Generate test signals for each channel
    channel_data = {}
    for ch in range(1, 5):
        freq = 100 * ch
        t = np.linspace(0, 0.1, 4800, False)
        channel_data[ch] = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    result = ae.analyze_mix(channel_data)
    assert len(result.channels) == 4
    for ch in result.channels.values():
        assert ch.has_signal
    print("  ✓ Audio engine")


# ─── State Tests ───────────────────────────────────────────────────────

def test_channel_state():
    ch = ChannelState(channel=1)
    assert ch.fader_db == 0.0
    assert ch.gain_db == 30.0
    assert ch.is_default

    ch.fader_db = -10
    assert not ch.is_default

    ch.reset()
    assert ch.is_default
    print("  ✓ Channel state")

def test_mixer_state():
    state = MixerState()
    assert len(state.channels) == 8

    ch1 = state.get_channel(1)
    assert ch1 is not None
    ch1.fader_db = -10

    # Serialize/deserialize
    data = state.to_dict()
    state2 = MixerState.from_dict(data)
    assert state2.get_channel(1).fader_db == -10
    print("  ✓ Mixer state")

def test_snapshot():
    state = MixerState()
    state.get_channel(1).fader_db = -20
    state.get_channel(2).muted = True

    snap = Snapshot(name="test", state=state, description="Test snapshot")

    data = snap.to_dict()
    snap2 = Snapshot.from_dict(data)
    assert snap2.name == "test"
    assert snap2.state.get_channel(1).fader_db == -20
    print("  ✓ Snapshot")

def test_state_manager():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(data_dir=Path(tmpdir))

        # Test preset
        batch = sm.apply_preset("vocal_male", 1)
        ch = sm.state.get_channel(1)
        assert ch.lowcut_hz == 80
        assert ch.eq_highmid_db == 3

        # Test snapshot
        sm.save_snapshot("test_snap")
        assert "test_snap" in sm.snapshots

        # Test undo
        sm.set_fader(1, -20)
        assert sm.state.get_channel(1).fader_db == -20
        print("  ✓ State manager")


# ─── LLM Tests ────────────────────────────────────────────────────────

def test_parse_llm_response():
    # Test structured JSON response
    text = '''
    {
        "reasoning": "Boosting vocal presence",
        "actions": [{"action": "set_eq", "channel": 1, "band": "highmid", "db": 4}],
        "confidence": 0.85
    }
    '''
    resp = parse_llm_response(text)
    assert not resp.parse_error
    assert len(resp.actions) == 1
    assert resp.confidence == 0.85
    print("  ✓ LLM response parsing")

def test_parse_array_response():
    text = '[{"action": "mute", "channel": 3}]'
    resp = parse_llm_response(text)
    assert not resp.parse_error
    assert len(resp.actions) == 1
    print("  ✓ Array response parsing")

def test_conversation():
    conv = Conversation(max_messages=5)
    conv.add("user", "Hello")
    conv.add("assistant", "Hi there!")
    assert len(conv.messages) == 2
    assert conv.last_user_message.content == "Hello"
    print("  ✓ Conversation manager")


# ─── Brain Tests ───────────────────────────────────────────────────────

def test_brain_dry_run():
    midi = MidiDriver(dry_run=True)
    state = StateManager()
    brain = Brain(midi=midi, state=state)

    # Test direct execution
    result = brain.execute({"action": "set_fader", "channel": 1, "db": -10})
    assert result.success
    assert brain.state.state.get_channel(1).fader_db == -10
    print("  ✓ Brain dry-run execution")

def test_brain_mode():
    brain = Brain(midi=MidiDriver(dry_run=True))
    brain.set_mode(BrainMode.AUTO)
    assert brain.mode == BrainMode.AUTO
    print("  ✓ Brain mode switching")


# ─── Run All ───────────────────────────────────────────────────────────

def main():
    print("=== Flow8-LLM Tests ===\n")

    print("[MIDI]")
    test_convert_fader()
    test_convert_gain()
    test_convert_eq()
    test_convert_freq()
    test_convert_pan()
    test_convert_q()
    test_midi_cmd()
    test_midi_batch()

    print("\n[Audio]")
    test_spectrum_analyzer()
    test_level_meter()
    test_audio_engine()

    print("\n[State]")
    test_channel_state()
    test_mixer_state()
    test_snapshot()
    test_state_manager()

    print("\n[LLM]")
    test_parse_llm_response()
    test_parse_array_response()
    test_conversation()

    print("\n[Brain]")
    test_brain_dry_run()
    test_brain_mode()

    print(f"\n✅ All tests passed!")


if __name__ == "__main__":
    main()
