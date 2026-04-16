"""
Preset Library for Flow8-LLM.

Extended preset management with:
- Import/export presets as JSON
- Preset discovery from analysis
- Genre-specific preset packs
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..core.state import StateManager, ChannelPreset


class PresetLibrary:
    """
    Extended preset management.

    Provides additional presets and management features
    beyond the built-in presets in StateManager.
    """

    def __init__(self, state: StateManager, data_dir: Optional[Path] = None):
        self.state = state
        self.data_dir = data_dir or Path.home() / ".flow8-llm"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_extended_presets()

    def _load_extended_presets(self):
        """Load extended presets from disk."""
        preset_file = self.data_dir / "custom_presets.json"
        if preset_file.exists():
            try:
                data = json.loads(preset_file.read_text())
                for name, p_data in data.items():
                    preset = ChannelPreset.from_dict(p_data)
                    self.state.presets[name] = preset
            except Exception:
                pass

    def save_custom_presets(self):
        """Save custom presets to disk."""
        custom = {name: p.to_dict() for name, p in self.state.presets.items()
                  if name not in self._builtin_names()}
        preset_file = self.data_dir / "custom_presets.json"
        preset_file.write_text(json.dumps(custom, indent=2))

    @staticmethod
    def _builtin_names() -> set:
        return {
            "vocal_male", "vocal_female", "kick", "snare",
            "guitar_acoustic", "guitar_electric", "bass", "keys",
            "podcast_voice", "flat",
        }

    def export_preset(self, name: str) -> Optional[str]:
        """Export a preset as JSON string."""
        preset = self.state.presets.get(name)
        if not preset:
            return None
        return json.dumps(preset.to_dict(), indent=2)

    def import_preset(self, json_str: str) -> bool:
        """Import a preset from JSON string."""
        try:
            data = json.loads(json_str)
            preset = ChannelPreset.from_dict(data)
            self.state.presets[preset.name] = preset
            self.save_custom_presets()
            return True
        except Exception:
            return False

    def import_pack(self, pack_file: Path) -> int:
        """Import a preset pack (JSON file with multiple presets)."""
        try:
            data = json.loads(pack_file.read_text())
            count = 0
            for name, p_data in data.items():
                preset = ChannelPreset.from_dict(p_data)
                self.state.presets[name] = preset
                count += 1
            self.save_custom_presets()
            return count
        except Exception:
            return 0

    # ── Genre Packs ──

    def load_genre_pack(self, genre: str) -> int:
        """Load a genre-specific preset pack."""
        packs = {
            "rock": {
                "rock_vocal": ChannelPreset(
                    name="rock_vocal", source_type="vocal",
                    description="Rock vocal - aggressive, present",
                    lowcut_hz=100, lowcut_enabled=True,
                    eq_lowmid_db=-4, eq_lowmid_freq=300,
                    eq_highmid_db=5, eq_highmid_freq=3000, eq_highmid_q=0.8,
                    eq_high_db=3, eq_high_freq=8000,
                    fx1_send_db=-18,
                ),
                "rock_kick": ChannelPreset(
                    name="rock_kick", source_type="drums",
                    description="Rock kick - punchy attack",
                    gain_db=38, lowcut_hz=35, lowcut_enabled=True,
                    eq_low_db=5, eq_low_freq=60,
                    eq_highmid_db=4, eq_highmid_freq=4000,
                    fx1_send_db=-70,
                ),
            },
            "jazz": {
                "jazz_vocal": ChannelPreset(
                    name="jazz_vocal", source_type="vocal",
                    description="Jazz vocal - warm, natural",
                    lowcut_hz=80, lowcut_enabled=True,
                    eq_lowmid_db=-2, eq_lowmid_freq=250,
                    eq_highmid_db=2, eq_highmid_freq=3000, eq_highmid_q=1.2,
                    eq_high_db=1, eq_high_freq=10000,
                    fx1_send_db=-12,
                ),
            },
            "edm": {
                "edm_synth": ChannelPreset(
                    name="edm_synth", source_type="synth",
                    description="EDM synth - wide, saturated",
                    lowcut_hz=40, lowcut_enabled=True,
                    eq_low_db=3, eq_low_freq=100,
                    eq_highmid_db=3, eq_highmid_freq=5000,
                    eq_high_db=2, eq_high_freq=12000,
                    fx1_send_db=-10,
                ),
            },
        }

        pack = packs.get(genre.lower(), {})
        for name, preset in pack.items():
            self.state.presets[name] = preset
        self.save_custom_presets()
        return len(pack)
