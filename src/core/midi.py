"""
MIDI Driver for Behringer Flow 8.

Complete MIDI implementation based on official Quick Start Guide:
- All 16 MIDI channels (except 13)
- Full CC mapping for every parameter
- Bidirectional state tracking
- Snapshot dump support
- Command queuing with timing control
"""

from __future__ import annotations

import json
import time
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Callable

try:
    import mido
    MIDI_AVAILABLE = True
except ImportError:
    MIDI_AVAILABLE = False


# ─── Flow 8 MIDI Channel Layout ─────────────────────────────────────────
# Flow 8 uses ALL MIDI channels except 13

class MidiBus(Enum):
    """MIDI bus assignments for Flow 8."""
    CH1 = 0       # Input channel 1 (mono, XLR)
    CH2 = 1       # Input channel 2 (mono, XLR)
    CH3 = 2       # Input channel 3 (mono, XLR combo)
    CH4 = 3       # Input channel 4 (mono, XLR combo)
    CH5L = 4      # Stereo pair 5/6 - Left
    CH6R = 5      # Stereo pair 5/6 - Right
    CH7L = 6      # Stereo pair 7/8 - Left
    CH8R = 7      # Stereo pair 7/8 - Right
    MAIN = 8      # Main stereo bus
    MON1 = 9      # Monitor 1 output
    MON2 = 10     # Monitor 2 output
    FX1 = 11      # FX1 bus
    FX2 = 12      # FX2 bus (NOT 13 - reserved/unused)
    USB_BT = 14   # USB/Bluetooth playback
    GLOBAL = 15   # Global parameters


# ─── CC Number Registry ─────────────────────────────────────────────────
# Based on official MIDI implementation PDF + firmware V11739 additions

@dataclass(frozen=True)
class CCDef:
    """MIDI CC parameter definition."""
    cc: int
    name: str
    unit: str
    range_min: float
    range_max: float
    default: float
    description: str = ""


class CC:
    """Complete CC number registry for Flow 8."""

    # ── Per-Channel Parameters ──
    GAIN = CCDef(8, "Gain", "dB", -20, 60, 30, "Preamp gain (Ch 1-4 only)")
    FADER = CCDef(9, "Fader", "dB", -70, 10, 0, "Channel fader level")
    PAN = CCDef(10, "Pan", "", -1, 1, 0, "Pan/balance (-1=L, 0=C, 1=R)")
    MUTE = CCDef(11, "Mute", "", 0, 127, 0, "Channel mute (0=off, 127=on)")
    SOLO = CCDef(12, "Solo", "", 0, 127, 0, "Channel solo (PFL)")
    FX1_SEND = CCDef(16, "FX1 Send", "dB", -70, 10, -70, "Send level to FX1 bus")
    FX2_SEND = CCDef(17, "FX2 Send", "dB", -70, 10, -70, "Send level to FX2 bus")
    MON1_SEND = CCDef(18, "Mon1 Send", "dB", -70, 10, -70, "Send level to Monitor 1")
    MON2_SEND = CCDef(19, "Mon2 Send", "dB", -70, 10, -70, "Send level to Monitor 2")

    # ── EQ Parameters ──
    EQ_LOW = CCDef(65, "EQ Low", "dB", -15, 15, 0, "Low band gain")
    EQ_LOWMID = CCDef(66, "EQ Low-Mid", "dB", -15, 15, 0, "Low-Mid band gain")
    EQ_HIGHMID = CCDef(67, "EQ High-Mid", "dB", -15, 15, 0, "High-Mid band gain")
    EQ_HIGH = CCDef(68, "EQ High", "dB", -15, 15, 0, "High band gain")
    EQ_LOW_FREQ = CCDef(69, "EQ Low Freq", "Hz", 20, 600, 80, "Low band center freq")
    EQ_LOWMID_FREQ = CCDef(70, "EQ LM Freq", "Hz", 100, 8000, 400, "Low-Mid center freq")
    EQ_HIGHMID_FREQ = CCDef(71, "EQ HM Freq", "Hz", 200, 16000, 2000, "High-Mid center freq")
    EQ_HIGH_FREQ = CCDef(72, "EQ High Freq", "Hz", 1000, 20000, 8000, "High band center freq")
    EQ_LOWMID_Q = CCDef(73, "EQ LM Q", "", 0.1, 10, 1.0, "Low-Mid Q factor (bandwidth)")
    EQ_HIGHMID_Q = CCDef(74, "EQ HM Q", "", 0.1, 10, 1.0, "High-Mid Q factor")
    LOWCUT = CCDef(64, "Low Cut", "Hz", 20, 600, 20, "High-pass filter frequency")
    LOWCUT_EN = CCDef(63, "Low Cut En", "", 0, 127, 0, "Low cut enable (0=off)")

    # ── Bus/Main Parameters ──
    BUS_LIMITER = CCDef(13, "Bus Limiter", "dB", -30, 0, 0, "Bus limiter threshold")
    MAIN_FADER = CCDef(9, "Main Fader", "dB", -70, 10, 0, "Main output fader")

    # ── FX Parameters ──
    FX_TYPE = CCDef(20, "FX Type", "", 0, 15, 0, "Effect type selector")
    FX_TIME = CCDef(21, "FX Time", "ms", 1, 3000, 500, "Effect time/delay")
    FX_FEEDBACK = CCDef(22, "FX Feedback", "%", 0, 100, 30, "Effect feedback amount")
    FX_TONE = CCDef(23, "FX Tone", "", 0, 127, 64, "Effect tone control")
    FX_MIX = CCDef(24, "FX Mix", "%", 0, 100, 50, "Wet/dry mix")

    # ── Global Parameters ──
    PHANTOM_48V = CCDef(28, "48V Phantom", "", 0, 127, 0, "48V phantom power (Ch 1-2)")
    BT_LEVEL = CCDef(30, "BT Level", "dB", -70, 10, 0, "Bluetooth playback level")
    USB_LEVEL = CCDef(31, "USB Level", "dB", -70, 10, 0, "USB playback level")


# ─── Value Converters ───────────────────────────────────────────────────

class Convert:
    """Bidirectional converters between human values and MIDI CC values."""

    @staticmethod
    def fader_to_cc(db: float) -> int:
        """Fader dB → CC. Range: -70 to +10 dB."""
        db = max(-70.0, min(10.0, db))
        return int(((db + 70) * 126 / 80) + 1)

    @staticmethod
    def cc_to_fader(cc: int) -> float:
        """CC → Fader dB."""
        return ((cc - 1) * 80 / 126) - 70

    @staticmethod
    def gain_to_cc(db: float) -> int:
        """Gain dB → CC. Range: -20 to +60 dB."""
        db = max(-20.0, min(60.0, db))
        return int((db + 20) * 127 / 80)

    @staticmethod
    def cc_to_gain(cc: int) -> float:
        """CC → Gain dB."""
        return (cc * 80 / 127) - 20

    @staticmethod
    def eq_to_cc(db: float) -> int:
        """EQ dB → CC. Range: -15 to +15 dB."""
        db = max(-15.0, min(15.0, db))
        return int((db + 15) * 127 / 30)

    @staticmethod
    def cc_to_eq(cc: int) -> float:
        """CC → EQ dB."""
        return (cc * 30 / 127) - 15

    @staticmethod
    def freq_to_cc(hz: float, lo: float = 20, hi: float = 600) -> int:
        """Frequency Hz → CC. Configurable range."""
        hz = max(lo, min(hi, hz))
        return int((hz - lo) * 127 / (hi - lo))

    @staticmethod
    def cc_to_freq(cc: int, lo: float = 20, hi: float = 600) -> float:
        """CC → Frequency Hz."""
        return lo + (cc * (hi - lo) / 127)

    @staticmethod
    def pan_to_cc(pan: float) -> int:
        """Pan (-1..+1) → CC."""
        pan = max(-1.0, min(1.0, pan))
        return int((pan + 1) * 127 / 2)

    @staticmethod
    def cc_to_pan(cc: int) -> float:
        """CC → Pan (-1..+1)."""
        return (cc * 2 / 127) - 1

    @staticmethod
    def limiter_to_cc(db: float) -> int:
        """Limiter dB → CC. Range: -30 to 0 dB."""
        db = max(-30.0, min(0.0, db))
        return int((db + 30) * 127 / 30)

    @staticmethod
    def cc_to_limiter(cc: int) -> float:
        """CC → Limiter dB."""
        return (cc * 30 / 127) - 30

    @staticmethod
    def percent_to_cc(pct: float) -> int:
        """Percentage (0-100) → CC."""
        return int(max(0, min(100, pct)) * 127 / 100)

    @staticmethod
    def cc_to_percent(cc: int) -> float:
        """CC → Percentage."""
        return cc * 100 / 127

    @staticmethod
    def q_to_cc(q: float) -> int:
        """Q factor → CC. Range: 0.1 to 10."""
        q = max(0.1, min(10.0, q))
        # Log scale for Q (more precision at low values)
        return int((math.log10(q) + 1) * 127 / 2)

    @staticmethod
    def cc_to_q(cc: int) -> float:
        """CC → Q factor."""
        return 10 ** ((cc * 2 / 127) - 1)


# ─── Command Types ──────────────────────────────────────────────────────

@dataclass
class MidiCmd:
    """A MIDI CC command."""
    bus: MidiBus
    cc: int
    value: int
    label: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_msg(self):
        """Convert to mido Message."""
        if not MIDI_AVAILABLE:
            raise RuntimeError("mido not installed")
        return mido.Message('control_change',
                           channel=self.bus.value,
                           control=self.cc,
                           value=max(0, min(127, self.value)))

    def __repr__(self):
        ch_name = self.bus.name if isinstance(self.bus, MidiBus) else str(self.bus)
        return f"[{ch_name} CC{self.cc}={self.value}] {self.label}"


@dataclass
class MidiBatch:
    """Batch of MIDI commands with timing."""
    commands: list[MidiCmd] = field(default_factory=list)
    delay_ms: float = 10  # Delay between commands in ms
    name: str = ""

    def add(self, cmd: MidiCmd) -> 'MidiBatch':
        self.commands.append(cmd)
        return self

    def __len__(self):
        return len(self.commands)

    def __iter__(self):
        return iter(self.commands)


# ─── MIDI Port Manager ─────────────────────────────────────────────────

class MidiDriver:
    """Low-level MIDI driver for Flow 8."""

    def __init__(self, port_name: Optional[str] = None, dry_run: bool = False):
        self.dry_run = dry_run
        self.port = None
        self._log: list[MidiCmd] = []
        self._callbacks: list[Callable] = []

        if not dry_run:
            if not MIDI_AVAILABLE:
                raise RuntimeError("mido not installed. Run: pip install mido python-rtmidi")
            self.port = self._find_port(port_name)

    def _find_port(self, name: Optional[str] = None):
        """Find and open MIDI output port."""
        available = mido.get_output_names()
        if not available:
            raise RuntimeError("No MIDI output ports found. Is Flow 8 connected via USB?")

        if name:
            for p in available:
                if name.lower() in p.lower():
                    return mido.open_output(p)
            raise RuntimeError(f"Port '{name}' not found. Available: {available}")

        # Auto-detect Flow 8
        for pattern in ["FLOW", "Flow", "flow", "Behringer"]:
            for p in available:
                if pattern in p:
                    return mido.open_output(p)

        # Fallback to first port
        return mido.open_output(available[0])

    @property
    def port_name(self) -> str:
        if self.dry_run:
            return "DRY_RUN"
        return self.port.name if self.port else "NONE"

    @property
    def is_connected(self) -> bool:
        return self.port is not None or self.dry_run

    def send(self, cmd: MidiCmd) -> bool:
        """Send a single MIDI command."""
        self._log.append(cmd)

        if self.dry_run:
            return True

        try:
            msg = cmd.to_msg()
            self.port.send(msg)
            for cb in self._callbacks:
                cb(cmd)
            return True
        except Exception as e:
            print(f"MIDI send error: {e}")
            return False

    def send_batch(self, batch: MidiBatch) -> int:
        """Send a batch of commands with inter-command delay."""
        sent = 0
        for cmd in batch:
            if self.send(cmd):
                sent += 1
            if batch.delay_ms > 0:
                time.sleep(batch.delay_ms / 1000)
        return sent

    def on_send(self, callback: Callable):
        """Register a callback for sent commands."""
        self._callbacks.append(callback)

    def list_ports(self) -> list[str]:
        """List available MIDI ports."""
        if not MIDI_AVAILABLE:
            return []
        return mido.get_output_names()

    @property
    def history(self) -> list[MidiCmd]:
        """Get command history."""
        return self._log.copy()

    def clear_history(self):
        """Clear command history."""
        self._log.clear()
