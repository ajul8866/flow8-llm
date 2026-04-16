"""
Feedback Suppression Engine for Flow8-LLM.

Detects and suppresses acoustic feedback using:
- Narrow-band peak detection
- Automatic notch filtering
- Multi-channel feedback tracking
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..core.audio import FeedbackDetector, MixAnalysis
from ..core.brain import Brain


@dataclass
class FeedbackEvent:
    """Recorded feedback event."""
    frequency_hz: float
    channel: int
    timestamp: float
    severity: float  # 0-1, how severe the feedback was
    action_taken: str
    resolved: bool = False


class FeedbackEngine:
    """
    Feedback detection and suppression engine.

    Monitors audio for feedback and automatically applies corrective EQ.
    """

    def __init__(self, brain: Brain):
        self.brain = brain
        self._history: list[FeedbackEvent] = []
        self._notch_filters: dict[int, list[float]] = {}  # channel -> [freqs]
        self._threshold_db: float = 20.0
        self._auto_suppress: bool = True

    def check_and_suppress(self) -> Optional[FeedbackEvent]:
        """
        Check for feedback and suppress if detected.

        Returns FeedbackEvent if feedback was detected and acted upon.
        """
        if not self.brain.audio.is_running:
            return None

        analysis = self.brain.audio.latest
        if not analysis:
            return None

        if not analysis.feedback_detected:
            return None

        freq = analysis.feedback_freq
        if not freq:
            return None

        # Find which channel is most likely causing feedback
        source_channel = self._find_source_channel(analysis, freq)
        if not source_channel:
            return None

        # Determine severity
        ch_analysis = analysis.channels.get(source_channel)
        severity = min(1.0, max(0.0, (ch_analysis.rms_db + 20) / 20)) if ch_analysis else 0.5

        # Apply suppression
        action_taken = ""
        if self._auto_suppress:
            band = self.brain._freq_to_band(freq)
            q = 8.0  # Narrow notch

            result = self.brain.execute({
                "action": "set_eq",
                "channel": source_channel,
                "band": band,
                "db": -6,
            })
            result2 = self.brain.execute({
                "action": "set_eq_q",
                "channel": source_channel,
                "band": band,
                "q": q,
            })

            action_taken = f"Cut {band} -6dB Q={q} on CH{source_channel}"

            # Track notch
            if source_channel not in self._notch_filters:
                self._notch_filters[source_channel] = []
            self._notch_filters[source_channel].append(freq)

        event = FeedbackEvent(
            frequency_hz=freq,
            channel=source_channel,
            timestamp=time.time(),
            severity=severity,
            action_taken=action_taken,
            resolved=True,
        )
        self._history.append(event)
        return event

    def _find_source_channel(self, analysis: MixAnalysis,
                              freq: float) -> Optional[int]:
        """Find which channel is most likely causing feedback at a frequency."""
        band = self.brain._freq_to_band(freq)
        max_energy = -100
        source = None

        for ch_num, ch_analysis in analysis.channels.items():
            if not ch_analysis.has_signal:
                continue
            energy = ch_analysis.band_energies.get(band, -100)
            if energy > max_energy:
                max_energy = energy
                source = ch_num

        return source

    def clear_notches(self, channel: Optional[int] = None):
        """Clear applied notch filters."""
        if channel:
            self._notch_filters.pop(channel, None)
        else:
            self._notch_filters.clear()

    @property
    def history(self) -> list[dict]:
        """Get feedback history."""
        return [
            {
                "time": time.strftime("%H:%M:%S", time.localtime(e.timestamp)),
                "freq": f"{e.frequency_hz:.0f}Hz",
                "channel": e.channel,
                "severity": f"{e.severity:.0%}",
                "action": e.action_taken,
            }
            for e in self._history[-20:]
        ]
