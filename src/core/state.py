"""
State Manager for Flow8-LLM.

Manages mixer state, snapshots, presets, and undo/redo history.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any
from copy import deepcopy

from .midi import MidiBus, CC, Convert, MidiCmd, MidiBatch


# ─── Mixer State ────────────────────────────────────────────────────────

@dataclass
class ChannelState:
    """Complete state for one mixer channel."""
    channel: int
    gain_db: float = 30.0
    fader_db: float = 0.0
    pan: float = 0.0
    muted: bool = False
    soloed: bool = False
    lowcut_hz: float = 20.0
    lowcut_enabled: bool = False
    eq_low_db: float = 0.0
    eq_low_freq: float = 80.0
    eq_lowmid_db: float = 0.0
    eq_lowmid_freq: float = 400.0
    eq_lowmid_q: float = 1.0
    eq_highmid_db: float = 0.0
    eq_highmid_freq: float = 2000.0
    eq_highmid_q: float = 1.0
    eq_high_db: float = 0.0
    eq_high_freq: float = 8000.0
    fx1_send_db: float = -70.0
    fx2_send_db: float = -70.0
    mon1_send_db: float = -70.0
    mon2_send_db: float = -70.0

    @property
    def is_default(self) -> bool:
        """Check if channel is at default state."""
        defaults = ChannelState(channel=self.channel)
        return self.__dict__ == defaults.__dict__

    def reset(self):
        """Reset to default state."""
        defaults = ChannelState(channel=self.channel)
        self.__dict__.update(defaults.__dict__)


@dataclass
class BusState:
    """State for a bus (main, monitors, FX)."""
    fader_db: float = 0.0
    limiter_db: float = 0.0
    muted: bool = False


@dataclass
class FXState:
    """State for an FX processor."""
    type: int = 0
    time_ms: float = 500.0
    feedback_pct: float = 30.0
    tone: int = 64
    mix_pct: float = 50.0


@dataclass
class MixerState:
    """Complete mixer state."""
    channels: dict[int, ChannelState] = field(default_factory=dict)
    buses: dict[str, BusState] = field(default_factory=dict)
    fx: dict[int, FXState] = field(default_factory=dict)
    bt_level_db: float = 0.0
    usb_level_db: float = 0.0
    phantom_48v: dict[int, bool] = field(default_factory=dict)
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        # Initialize default channels (1-8)
        for ch in range(1, 9):
            if ch not in self.channels:
                self.channels[ch] = ChannelState(channel=ch)
        # Initialize buses
        for bus in ["main", "mon1", "mon2"]:
            if bus not in self.buses:
                self.buses[bus] = BusState()
        # Initialize FX
        for fx_num in [1, 2]:
            if fx_num not in self.fx:
                self.fx[fx_num] = FXState()

    def get_channel(self, ch: int) -> Optional[ChannelState]:
        return self.channels.get(ch)

    def get_bus(self, bus: str) -> Optional[BusState]:
        return self.buses.get(bus)

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'MixerState':
        """Deserialize from dict."""
        state = cls()
        if "channels" in data:
            for ch_str, ch_data in data["channels"].items():
                ch = int(ch_str) if isinstance(ch_str, str) else ch_str
                state.channels[ch] = ChannelState(**ch_data)
        if "buses" in data:
            for bus_name, bus_data in data["buses"].items():
                state.buses[bus_name] = BusState(**bus_data)
        if "fx" in data:
            for fx_str, fx_data in data["fx"].items():
                fx = int(fx_str) if isinstance(fx_str, str) else fx_str
                state.fx[fx] = FXState(**fx_data)
        return state

    def to_commands(self) -> MidiBatch:
        """Generate all MIDI commands to apply this state."""
        batch = MidiBatch(name="full_state")

        for ch_num, ch in self.channels.items():
            midi_bus = MidiBus(ch_num - 1) if ch_num <= 8 else MidiBus.MAIN

            # Gain (only for Ch 1-4)
            if ch_num <= 4:
                batch.add(MidiCmd(midi_bus, CC.GAIN.cc, Convert.gain_to_cc(ch.gain_db),
                                  f"Ch{ch_num} gain {ch.gain_db:+.0f}dB"))

            # Fader
            batch.add(MidiCmd(midi_bus, CC.FADER.cc, Convert.fader_to_cc(ch.fader_db),
                              f"Ch{ch_num} fader {ch.fader_db:+.0f}dB"))

            # Pan
            batch.add(MidiCmd(midi_bus, CC.PAN.cc, Convert.pan_to_cc(ch.pan),
                              f"Ch{ch_num} pan {ch.pan:+.2f}"))

            # Mute
            batch.add(MidiCmd(midi_bus, CC.MUTE.cc, 127 if ch.muted else 0,
                              f"Ch{ch_num} mute={'ON' if ch.muted else 'OFF'}"))

            # Low cut
            batch.add(MidiCmd(midi_bus, CC.LOWCUT_EN.cc, 127 if ch.lowcut_enabled else 0))
            batch.add(MidiCmd(midi_bus, CC.LOWCUT.cc, Convert.freq_to_cc(ch.lowcut_hz)))

            # EQ
            batch.add(MidiCmd(midi_bus, CC.EQ_LOW.cc, Convert.eq_to_cc(ch.eq_low_db)))
            batch.add(MidiCmd(midi_bus, CC.EQ_LOWMID.cc, Convert.eq_to_cc(ch.eq_lowmid_db)))
            batch.add(MidiCmd(midi_bus, CC.EQ_HIGHMID.cc, Convert.eq_to_cc(ch.eq_highmid_db)))
            batch.add(MidiCmd(midi_bus, CC.EQ_HIGH.cc, Convert.eq_to_cc(ch.eq_high_db)))

            # FX sends
            fx1_bus = MidiBus.FX1
            batch.add(MidiCmd(midi_bus, CC.FX1_SEND.cc, Convert.fader_to_cc(ch.fx1_send_db)))
            batch.add(MidiCmd(midi_bus, CC.FX2_SEND.cc, Convert.fader_to_cc(ch.fx2_send_db)))

        # FX processors
        for fx_num, fx in self.fx.items():
            fx_bus = MidiBus.FX1 if fx_num == 1 else MidiBus.FX2
            batch.add(MidiCmd(fx_bus, CC.FX_TYPE.cc, fx.type))
            batch.add(MidiCmd(fx_bus, CC.FX_MIX.cc, Convert.percent_to_cc(fx.mix_pct)))

        return batch


# ─── Snapshot ───────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    """A named mixer snapshot (scene)."""
    name: str
    state: MixerState
    description: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "created_at": self.created_at,
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Snapshot':
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            tags=data.get("tags", []),
            created_at=data.get("created_at", 0),
            state=MixerState.from_dict(data.get("state", {})),
        )


# ─── Preset ─────────────────────────────────────────────────────────────

@dataclass
class ChannelPreset:
    """A channel preset (EQ, gain, effects for a specific source type)."""
    name: str
    source_type: str  # vocal, guitar, kick, snare, bass, keys, etc.
    description: str = ""
    gain_db: float = 30.0
    lowcut_hz: float = 20.0
    lowcut_enabled: bool = False
    eq_low_db: float = 0.0
    eq_low_freq: float = 80.0
    eq_lowmid_db: float = 0.0
    eq_lowmid_freq: float = 400.0
    eq_lowmid_q: float = 1.0
    eq_highmid_db: float = 0.0
    eq_highmid_freq: float = 2000.0
    eq_highmid_q: float = 1.0
    eq_high_db: float = 0.0
    eq_high_freq: float = 8000.0
    fx1_send_db: float = -70.0
    fx2_send_db: float = -70.0
    tags: list[str] = field(default_factory=list)

    def apply_to_channel(self, state: ChannelState):
        """Apply this preset to a channel state."""
        state.gain_db = self.gain_db
        state.lowcut_hz = self.lowcut_hz
        state.lowcut_enabled = self.lowcut_enabled
        state.eq_low_db = self.eq_low_db
        state.eq_low_freq = self.eq_low_freq
        state.eq_lowmid_db = self.eq_lowmid_db
        state.eq_lowmid_freq = self.eq_lowmid_freq
        state.eq_lowmid_q = self.eq_lowmid_q
        state.eq_highmid_db = self.eq_highmid_db
        state.eq_highmid_freq = self.eq_highmid_freq
        state.eq_highmid_q = self.eq_highmid_q
        state.eq_high_db = self.eq_high_db
        state.eq_high_freq = self.eq_high_freq
        state.fx1_send_db = self.fx1_send_db
        state.fx2_send_db = self.fx2_send_db

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ChannelPreset':
        return cls(**{k: v for k, v in data.items()
                     if k in cls.__dataclass_fields__})


# ─── History (Undo/Redo) ───────────────────────────────────────────────

@dataclass
class HistoryEntry:
    """One entry in the undo history."""
    action: str
    channel: Optional[int]
    before: Any  # Previous value
    after: Any   # New value
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class History:
    """Undo/redo history manager."""

    def __init__(self, max_entries: int = 200):
        self.max_entries = max_entries
        self._entries: list[HistoryEntry] = []
        self._position: int = -1

    def push(self, entry: HistoryEntry):
        """Push a new history entry."""
        # Truncate any redo entries
        self._entries = self._entries[:self._position + 1]
        self._entries.append(entry)
        self._position = len(self._entries) - 1

        # Trim if too long
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]
            self._position = len(self._entries) - 1

    def undo(self) -> Optional[HistoryEntry]:
        """Undo last action."""
        if self._position < 0:
            return None
        entry = self._entries[self._position]
        self._position -= 1
        return entry

    def redo(self) -> Optional[HistoryEntry]:
        """Redo last undone action."""
        if self._position >= len(self._entries) - 1:
            return None
        self._position += 1
        return self._entries[self._position]

    @property
    def can_undo(self) -> bool:
        return self._position >= 0

    @property
    def can_redo(self) -> bool:
        return self._position < len(self._entries) - 1

    def clear(self):
        self._entries.clear()
        self._position = -1

    @property
    def entries(self) -> list[HistoryEntry]:
        return self._entries.copy()


# ─── State Manager (Main) ──────────────────────────────────────────────

class StateManager:
    """
    Main state manager orchestrating snapshots, presets, and history.
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or Path.home() / ".flow8-llm"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.state = MixerState()
        self.snapshots: dict[str, Snapshot] = {}
        self.presets: dict[str, ChannelPreset] = {}
        self.history = History()

        # Load built-in presets
        self._load_builtin_presets()

        # Load saved data
        self._load_snapshots()

    def _load_builtin_presets(self):
        """Load built-in channel presets."""
        self.presets = {
            "vocal_male": ChannelPreset(
                name="vocal_male", source_type="vocal",
                description="Male vocal - warm, present",
                lowcut_hz=80, lowcut_enabled=True,
                eq_lowmid_db=-3, eq_lowmid_freq=300, eq_lowmid_q=1.2,
                eq_highmid_db=3, eq_highmid_freq=3000, eq_highmid_q=1.0,
                eq_high_db=2, eq_high_freq=10000,
                fx1_send_db=-15,
            ),
            "vocal_female": ChannelPreset(
                name="vocal_female", source_type="vocal",
                description="Female vocal - bright, airy",
                lowcut_hz=100, lowcut_enabled=True,
                eq_lowmid_db=-2, eq_lowmid_freq=250,
                eq_highmid_db=4, eq_highmid_freq=4000, eq_highmid_q=0.8,
                eq_high_db=3, eq_high_freq=12000,
                fx1_send_db=-12,
            ),
            "kick": ChannelPreset(
                name="kick", source_type="drums",
                description="Kick drum - punchy, tight",
                gain_db=35, lowcut_hz=30, lowcut_enabled=True,
                eq_low_db=4, eq_low_freq=60,
                eq_lowmid_db=-3, eq_lowmid_freq=300, eq_lowmid_q=1.5,
                eq_highmid_db=3, eq_highmid_freq=3500,
                fx1_send_db=-70,
            ),
            "snare": ChannelPreset(
                name="snare", source_type="drums",
                description="Snare - crack, body",
                gain_db=30, lowcut_hz=80, lowcut_enabled=True,
                eq_lowmid_db=-2, eq_lowmid_freq=400,
                eq_highmid_db=4, eq_highmid_freq=5000,
                eq_high_db=2, eq_high_freq=10000,
                fx1_send_db=-20,
            ),
            "guitar_acoustic": ChannelPreset(
                name="guitar_acoustic", source_type="guitar",
                description="Acoustic guitar - natural, shimmer",
                gain_db=25, lowcut_hz=100, lowcut_enabled=True,
                eq_lowmid_db=-2, eq_lowmid_freq=300,
                eq_highmid_db=2, eq_highmid_freq=5000,
                eq_high_db=3, eq_high_freq=12000,
                fx1_send_db=-18,
            ),
            "guitar_electric": ChannelPreset(
                name="guitar_electric", source_type="guitar",
                description="Electric guitar - full, present",
                gain_db=28, lowcut_hz=80, lowcut_enabled=True,
                eq_lowmid_db=-1, eq_lowmid_freq=250,
                eq_highmid_db=3, eq_highmid_freq=2500,
                fx1_send_db=-20,
            ),
            "bass": ChannelPreset(
                name="bass", source_type="bass",
                description="Bass guitar - warm, defined",
                gain_db=32, lowcut_hz=25, lowcut_enabled=True,
                eq_low_db=2, eq_low_freq=80,
                eq_lowmid_db=-2, eq_lowmid_freq=200, eq_lowmid_q=1.5,
                eq_highmid_db=2, eq_highmid_freq=1000,
                eq_high_db=1, eq_high_freq=5000,
                fx1_send_db=-70,
            ),
            "keys": ChannelPreset(
                name="keys", source_type="keys",
                description="Keys/piano - full range, clear",
                lowcut_hz=40, lowcut_enabled=True,
                eq_lowmid_db=-1, eq_lowmid_freq=200,
                eq_highmid_db=2, eq_highmid_freq=3000,
                eq_high_db=1, eq_high_freq=8000,
                fx1_send_db=-15,
            ),
            "podcast_voice": ChannelPreset(
                name="podcast_voice", source_type="voice",
                description="Podcast/broadcast voice - radio-ready",
                gain_db=30, lowcut_hz=100, lowcut_enabled=True,
                eq_lowmid_db=-4, eq_lowmid_freq=250, eq_lowmid_q=1.0,
                eq_highmid_db=3, eq_highmid_freq=3500, eq_highmid_q=0.8,
                eq_high_db=2, eq_high_freq=10000,
                fx1_send_db=-70,
            ),
            "flat": ChannelPreset(
                name="flat", source_type="generic",
                description="Flat/default - no processing",
                gain_db=30, lowcut_hz=20, lowcut_enabled=False,
                eq_low_db=0, eq_lowmid_db=0, eq_highmid_db=0, eq_high_db=0,
                fx1_send_db=-70, fx2_send_db=-70,
            ),
        }

    def _load_snapshots(self):
        """Load saved snapshots from disk."""
        snapshot_file = self.data_dir / "snapshots.json"
        if snapshot_file.exists():
            try:
                data = json.loads(snapshot_file.read_text())
                for name, snap_data in data.items():
                    self.snapshots[name] = Snapshot.from_dict(snap_data)
            except Exception:
                pass

    def save_snapshots(self):
        """Save snapshots to disk."""
        snapshot_file = self.data_dir / "snapshots.json"
        data = {name: snap.to_dict() for name, snap in self.snapshots.items()}
        snapshot_file.write_text(json.dumps(data, indent=2))

    # ── Snapshot Operations ──

    def save_snapshot(self, name: str, description: str = "",
                      tags: list[str] = None) -> Snapshot:
        """Save current state as a snapshot."""
        snap = Snapshot(
            name=name,
            state=deepcopy(self.state),
            description=description,
            tags=tags or [],
        )
        self.snapshots[name] = snap
        self.save_snapshots()
        return snap

    def recall_snapshot(self, name: str) -> Optional[MidiBatch]:
        """Recall a snapshot and return MIDI commands to apply it."""
        snap = self.snapshots.get(name)
        if not snap:
            return None

        # Save current state for undo
        self.history.push(HistoryEntry(
            action="recall_snapshot",
            channel=None,
            before=deepcopy(self.state),
            after=deepcopy(snap.state),
        ))

        self.state = deepcopy(snap.state)
        return self.state.to_commands()

    def delete_snapshot(self, name: str) -> bool:
        """Delete a snapshot."""
        if name in self.snapshots:
            del self.snapshots[name]
            self.save_snapshots()
            return True
        return False

    def list_snapshots(self) -> list[dict]:
        """List all snapshots."""
        return [
            {"name": s.name, "description": s.description,
             "tags": s.tags, "created": s.created_at}
            for s in self.snapshots.values()
        ]

    # ── Preset Operations ──

    def apply_preset(self, preset_name: str, channel: int) -> Optional[MidiBatch]:
        """Apply a preset to a channel."""
        preset = self.presets.get(preset_name)
        if not preset:
            return None

        ch_state = self.state.get_channel(channel)
        if not ch_state:
            return None

        # Save for undo
        self.history.push(HistoryEntry(
            action="apply_preset",
            channel=channel,
            before=deepcopy(ch_state),
            after=None,
        ))

        preset.apply_to_channel(ch_state)
        return self.state.to_commands()

    def list_presets(self, source_type: Optional[str] = None) -> list[dict]:
        """List available presets, optionally filtered by source type."""
        results = []
        for p in self.presets.values():
            if source_type and p.source_type != source_type:
                continue
            results.append({
                "name": p.name,
                "type": p.source_type,
                "description": p.description,
            })
        return results

    # ── Channel Operations ──

    def set_fader(self, channel: int, db: float) -> MidiBatch:
        """Set fader level."""
        ch = self.state.get_channel(channel)
        if not ch:
            return MidiBatch()

        old = ch.fader_db
        ch.fader_db = max(-70, min(10, db))

        self.history.push(HistoryEntry("set_fader", channel, old, ch.fader_db))

        midi_bus = MidiBus(channel - 1) if 1 <= channel <= 8 else MidiBus.MAIN
        batch = MidiBatch(name=f"fader_ch{channel}")
        batch.add(MidiCmd(midi_bus, CC.FADER.cc, Convert.fader_to_cc(ch.fader_db),
                          f"Ch{channel} fader {ch.fader_db:+.0f}dB"))
        return batch

    def set_gain(self, channel: int, db: float) -> MidiBatch:
        """Set preamp gain."""
        ch = self.state.get_channel(channel)
        if not ch:
            return MidiBatch()

        old = ch.gain_db
        ch.gain_db = max(-20, min(60, db))

        self.history.push(HistoryEntry("set_gain", channel, old, ch.gain_db))

        midi_bus = MidiBus(channel - 1)
        batch = MidiBatch(name=f"gain_ch{channel}")
        batch.add(MidiCmd(midi_bus, CC.GAIN.cc, Convert.gain_to_cc(ch.gain_db),
                          f"Ch{channel} gain {ch.gain_db:+.0f}dB"))
        return batch

    def set_eq(self, channel: int, band: str, db: float) -> MidiBatch:
        """Set EQ band."""
        ch = self.state.get_channel(channel)
        if not ch:
            return MidiBatch()

        band_map = {
            "low": ("eq_low_db", CC.EQ_LOW),
            "lowmid": ("eq_lowmid_db", CC.EQ_LOWMID),
            "low-mid": ("eq_lowmid_db", CC.EQ_LOWMID),
            "highmid": ("eq_highmid_db", CC.EQ_HIGHMID),
            "high-mid": ("eq_highmid_db", CC.EQ_HIGHMID),
            "high": ("eq_high_db", CC.EQ_HIGH),
        }

        entry = band_map.get(band.lower())
        if not entry:
            return MidiBatch()

        attr_name, cc_def = entry
        old = getattr(ch, attr_name)
        db = max(-15, min(15, db))
        setattr(ch, attr_name, db)

        self.history.push(HistoryEntry(f"set_eq_{band}", channel, old, db))

        midi_bus = MidiBus(channel - 1)
        batch = MidiBatch(name=f"eq_{band}_ch{channel}")
        batch.add(MidiCmd(midi_bus, cc_def.cc, Convert.eq_to_cc(db),
                          f"Ch{channel} EQ {band} {db:+.1f}dB"))
        return batch

    # ── Undo/Redo ──

    def undo(self) -> Optional[MidiBatch]:
        """Undo last action."""
        entry = self.history.undo()
        if not entry:
            return None
        # Restore previous state for this specific change
        if entry.action.startswith("set_") and entry.channel:
            ch = self.state.get_channel(entry.channel)
            if ch:
                attr = entry.action.replace("set_", "")
                attr_map = {
                    "fader": "fader_db",
                    "gain": "gain_db",
                    "eq_low": "eq_low_db",
                    "eq_lowmid": "eq_lowmid_db",
                    "eq_highmid": "eq_highmid_db",
                    "eq_high": "eq_high_db",
                }
                mapped = attr_map.get(attr)
                if mapped:
                    setattr(ch, mapped, entry.before)
                    return self.state.to_commands()
        return None

    # ── I/O ──

    def export_state(self) -> str:
        """Export full state as JSON string."""
        return json.dumps(self.state.to_dict(), indent=2)

    def import_state(self, json_str: str) -> bool:
        """Import state from JSON string."""
        try:
            data = json.loads(json_str)
            self.state = MixerState.from_dict(data)
            return True
        except Exception:
            return False
