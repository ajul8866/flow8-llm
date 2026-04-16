"""
Auto-Mix Engine for Flow8-LLM.

Intelligent auto-mixing that:
- Analyzes real-time audio per channel
- Detects frequency conflicts (masking)
- Suggests and applies EQ, levels, panning
- Genre-aware mixing decisions
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..core.audio import MixAnalysis, ChannelAnalysis, BANDS
from ..core.brain import Brain
from ..core.state import MixerState


@dataclass
class AutoMixConfig:
    """Auto-mix configuration."""
    target_rms_db: float = -18.0     # Target RMS level
    max_peak_db: float = -3.0        # Maximum peak before adjustment
    min_headroom_db: float = 6.0     # Minimum headroom
    eq_max_cut_db: float = -6.0      # Maximum auto EQ cut
    eq_max_boost_db: float = 4.0     # Maximum auto EQ boost
    update_interval_sec: float = 2.0
    confidence_threshold: float = 0.6
    genre: str = "general"           # vocal, band, podcast, dj, broadcast


class AutoMixEngine:
    """
    Automatic mixing engine.

    Continuously analyzes audio and makes mixing decisions.
    """

    def __init__(self, brain: Brain, config: Optional[AutoMixConfig] = None):
        self.brain = brain
        self.config = config or AutoMixConfig()
        self._running = False
        self._history: list[dict] = []

    def analyze_channel(self, ch: ChannelAnalysis) -> list[dict]:
        """
        Analyze a single channel and return suggested actions.
        """
        actions = []

        if not ch.has_signal:
            return actions

        # 1. Level management
        if ch.clipping:
            # Emergency: reduce gain immediately
            actions.append({
                "action": "set_gain",
                "channel": ch.channel,
                "db": -10,  # Hard cut
                "reason": "CLIPPING - emergency gain reduction",
                "priority": 1,
            })
        elif ch.peak_db > self.config.max_peak_db:
            # Reduce gain to create headroom
            reduction = ch.peak_db - self.config.max_peak_db
            ch_state = self.brain.state.state.get_channel(ch.channel)
            if ch_state:
                actions.append({
                    "action": "set_gain",
                    "channel": ch.channel,
                    "db": max(-20, ch_state.gain_db - reduction - 2),
                    "reason": f"Peak too high ({ch.peak_db:+.1f}dB), reducing gain",
                    "priority": 2,
                })
        elif ch.rms_db < self.config.target_rms_db - 12:
            # Signal too low
            ch_state = self.brain.state.state.get_channel(ch.channel)
            if ch_state:
                boost = self.config.target_rms_db - ch.rms_db
                actions.append({
                    "action": "set_gain",
                    "channel": ch.channel,
                    "db": min(60, ch_state.gain_db + boost),
                    "reason": f"Signal too low ({ch.rms_db:+.1f}dB), boosting gain",
                    "priority": 3,
                })

        # 2. Frequency management
        band_energies = ch.band_energies

        # Check for muddy low-mid buildup
        lowmid_energy = band_energies.get("lowmid", -100)
        mid_energy = band_energies.get("mid", -100)
        if lowmid_energy > mid_energy + 8 and lowmid_energy > -20:
            actions.append({
                "action": "set_eq",
                "channel": ch.channel,
                "band": "lowmid",
                "db": self.config.eq_max_cut_db,
                "reason": "Muddy low-mid buildup detected",
                "priority": 4,
            })

        # Check for harsh high-mid
        highmid_energy = band_energies.get("highmid", -100)
        if highmid_energy > -10 and ch.crest_factor < 6:
            actions.append({
                "action": "set_eq",
                "channel": ch.channel,
                "band": "highmid",
                "db": -3,
                "reason": "Harsh high-mid, reducing",
                "priority": 4,
            })

        return actions

    def analyze_mix_masking(self, analysis: MixAnalysis) -> list[dict]:
        """
        Detect frequency masking between channels and suggest fixes.
        """
        actions = []
        channels_with_signal = {
            ch: a for ch, a in analysis.channels.items() if a.has_signal
        }

        if len(channels_with_signal) < 2:
            return actions

        # Compare band energies across channels
        for band in BANDS:
            energies = {}
            for ch, a in channels_with_signal.items():
                energies[ch] = a.band_energies.get(band.name, -100)

            # Find channels with high energy in same band
            active = {ch: e for ch, e in energies.items() if e > -25}
            if len(active) >= 3:
                # Masking likely - suggest EQ carve-outs
                sorted_chs = sorted(active.items(), key=lambda x: x[1], reverse=True)
                # Cut the less important channels in this band
                for ch, e in sorted_chs[1:]:
                    ch_type = self._guess_channel_type(ch, analysis.channels.get(ch))
                    if ch_type in ("guitar", "keys", "pad"):
                        actions.append({
                            "action": "set_eq",
                            "channel": ch,
                            "band": band.name,
                            "db": -3,
                            "reason": f"Masking: cut {band.name} on CH{ch} (dominant: CH{sorted_chs[0][0]})",
                            "priority": 5,
                        })

        return actions

    def suggest_panning(self, analysis: MixAnalysis) -> list[dict]:
        """Suggest panning for stereo width."""
        actions = []
        channels_with_signal = {
            ch: a for ch, a in analysis.channels.items() if a.has_signal
        }

        # Standard panning suggestions based on channel number
        # Ch 1-2: center (vocals, kick, snare)
        # Ch 3-4: moderate L/R
        # Ch 5-8: wide L/R
        pan_suggestions = {
            3: -0.5,   # Guitar L
            4: 0.5,    # Guitar R
            5: -0.7,   # Keys L
            6: 0.7,    # Keys R
            7: -0.3,
            8: 0.3,
        }

        for ch in channels_with_signal:
            if ch in pan_suggestions:
                ch_state = self.brain.state.state.get_channel(ch)
                if ch_state and abs(ch_state.pan) < 0.1:
                    actions.append({
                        "action": "set_pan",
                        "channel": ch,
                        "pan": pan_suggestions[ch],
                        "reason": f"Stereo width: pan CH{ch} {'left' if pan_suggestions[ch] < 0 else 'right'}",
                        "priority": 6,
                    })

        return actions

    def process(self, analysis: MixAnalysis) -> list[dict]:
        """
        Full auto-mix processing.

        Returns list of actions sorted by priority.
        """
        all_actions = []

        # Per-channel analysis
        for ch, ch_analysis in analysis.channels.items():
            all_actions.extend(self.analyze_channel(ch_analysis))

        # Mix-level analysis
        all_actions.extend(self.analyze_mix_masking(analysis))

        # Panning suggestions
        if self.config.genre in ("band", "general"):
            all_actions.extend(self.suggest_panning(analysis))

        # Sort by priority
        all_actions.sort(key=lambda a: a.get("priority", 99))

        return all_actions

    def step(self) -> Optional[dict]:
        """
        Perform one auto-mix step.

        Returns action summary or None.
        """
        if not self.brain.audio.is_running:
            return None

        analysis = self.brain.audio.latest
        if not analysis:
            return None

        actions = self.process(analysis)

        if not actions:
            return None

        # Execute highest priority action
        top_action = actions[0]
        # Remove metadata before execution
        clean_action = {k: v for k, v in top_action.items()
                       if k in ("action", "channel", "db", "band", "pan", "hz", "q")}
        result = self.brain.execute(clean_action)

        summary = {
            "time": time.time(),
            "action": clean_action,
            "reason": top_action.get("reason", ""),
            "priority": top_action.get("priority", 99),
            "success": result.success,
            "total_suggestions": len(actions),
        }

        self._history.append(summary)
        return summary

    @staticmethod
    def _guess_channel_type(ch: int, analysis: Optional[ChannelAnalysis]) -> str:
        """Guess instrument type from channel number and audio characteristics."""
        if analysis is None:
            return "unknown"

        dominant = analysis.dominant_band
        if ch == 1:
            return "vocal"
        elif dominant in ("sub", "low") and analysis.crest_factor > 10:
            return "kick"
        elif dominant == "low" and ch <= 4:
            return "bass"
        elif dominant in ("highmid", "high"):
            return "guitar"
        elif dominant == "mid":
            return "keys"
        return "unknown"
