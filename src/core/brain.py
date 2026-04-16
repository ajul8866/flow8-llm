"""
Brain: Decision Engine for Flow8-LLM.

The central intelligence that:
- Takes user commands (text/voice) + audio analysis
- Uses LLM to reason about mixing decisions
- Executes actions through MIDI + state management
- Manages auto-mix, scenes, and advanced workflows
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Callable

from .midi import MidiDriver, MidiBus, CC, Convert, MidiCmd, MidiBatch
from .audio import AudioEngine, MixAnalysis, ChannelAnalysis
from .llm import LLMEngine, LLMConfig, LLMResponse, LLMProvider
from .state import StateManager, Snapshot, ChannelPreset


class BrainMode(Enum):
    """Operating modes for the Brain."""
    MANUAL = auto()      # User sends commands, Brain executes
    ASSISTED = auto()    # User commands + Brain suggestions
    AUTO = auto()        # Brain analyzes audio, auto-adjusts
    SCENE = auto()       # Running scene automation
    LEARN = auto()       # Learning mode - observe and suggest


@dataclass
class ActionResult:
    """Result of executing a brain command."""
    success: bool
    actions_sent: int = 0
    actions_failed: int = 0
    reasoning: str = ""
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    latency_ms: float = 0.0
    error: str = ""

    @property
    def total_actions(self) -> int:
        return self.actions_sent + self.actions_failed


class Brain:
    """
    Central decision engine for Flow8-LLM.

    Orchestrates MIDI, Audio, LLM, and State into a cohesive AI mixing system.
    """

    def __init__(
        self,
        midi: Optional[MidiDriver] = None,
        audio: Optional[AudioEngine] = None,
        llm: Optional[LLMEngine] = None,
        state: Optional[StateManager] = None,
    ):
        self.midi = midi or MidiDriver(dry_run=True)
        self.audio = audio or AudioEngine()
        self.llm = llm or LLMEngine()
        self.state = state or StateManager()

        self.mode = BrainMode.MANUAL
        self._event_log: list[dict] = []
        self._callbacks: dict[str, list[Callable]] = {
            "action": [],
            "analysis": [],
            "mode_change": [],
            "error": [],
        }

    # ── Event System ──

    def on(self, event: str, callback: Callable):
        """Register event callback."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, data: any = None):
        """Emit an event."""
        for cb in self._callbacks.get(event, []):
            try:
                cb(data)
            except Exception:
                pass

    def _log(self, action: str, detail: str = "", success: bool = True):
        """Log an action."""
        entry = {
            "time": time.time(),
            "action": action,
            "detail": detail,
            "success": success,
        }
        self._event_log.append(entry)
        if len(self._event_log) > 1000:
            self._event_log = self._event_log[-500:]

    # ── Mode Management ──

    def set_mode(self, mode: BrainMode):
        """Change operating mode."""
        old_mode = self.mode
        self.mode = mode
        self._emit("mode_change", {"from": old_mode, "to": mode})
        self._log("mode_change", f"{old_mode.name} → {mode.name}")

    # ── Command Execution ──

    def _execute_action(self, action: dict) -> tuple[bool, MidiBatch]:
        """Execute a single parsed action. Returns (success, batch)."""
        act = action.get("action", "").lower()
        batch = MidiBatch()

        try:
            if act == "set_fader":
                batch = self.state.set_fader(action["channel"], action["db"])

            elif act == "set_gain":
                batch = self.state.set_gain(action["channel"], action["db"])

            elif act == "set_eq":
                batch = self.state.set_eq(action["channel"], action["band"], action["db"])

            elif act == "set_eq_freq":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    band = action["band"].lower()
                    freq = action["hz"]
                    if band == "low":
                        ch.eq_low_freq = freq
                    elif band in ("lowmid", "low-mid"):
                        ch.eq_lowmid_freq = freq
                    elif band in ("highmid", "high-mid"):
                        ch.eq_highmid_freq = freq
                    elif band == "high":
                        ch.eq_high_freq = freq

            elif act == "set_eq_q":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    band = action["band"].lower()
                    q = action["q"]
                    if band in ("lowmid", "low-mid"):
                        ch.eq_lowmid_q = q
                    elif band in ("highmid", "high-mid"):
                        ch.eq_highmid_q = q

            elif act == "set_lowcut":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    ch.lowcut_hz = max(20, min(600, action["hz"]))
                    ch.lowcut_enabled = True
                    midi_bus = MidiBus(action["channel"] - 1)
                    batch = MidiBatch(name=f"lowcut_ch{action['channel']}")
                    batch.add(MidiCmd(midi_bus, CC.LOWCUT.cc,
                              Convert.freq_to_cc(ch.lowcut_hz)))
                    batch.add(MidiCmd(midi_bus, CC.LOWCUT_EN.cc, 127))

            elif act == "set_lowcut_en":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    ch.lowcut_enabled = action["enabled"]
                    midi_bus = MidiBus(action["channel"] - 1)
                    batch = MidiBatch()
                    batch.add(MidiCmd(midi_bus, CC.LOWCUT_EN.cc,
                              127 if action["enabled"] else 0))

            elif act == "set_pan":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    ch.pan = max(-1.0, min(1.0, action["pan"]))
                    midi_bus = MidiBus(action["channel"] - 1)
                    batch = MidiBatch(name=f"pan_ch{action['channel']}")
                    batch.add(MidiCmd(midi_bus, CC.PAN.cc, Convert.pan_to_cc(ch.pan)))

            elif act == "mute":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    ch.muted = True
                    midi_bus = MidiBus(action["channel"] - 1)
                    batch = MidiBatch(name=f"mute_ch{action['channel']}")
                    batch.add(MidiCmd(midi_bus, CC.MUTE.cc, 127))

            elif act == "unmute":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    ch.muted = False
                    midi_bus = MidiBus(action["channel"] - 1)
                    batch = MidiBatch(name=f"unmute_ch{action['channel']}")
                    batch.add(MidiCmd(midi_bus, CC.MUTE.cc, 0))

            elif act == "solo":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    ch.soloed = True
                    midi_bus = MidiBus(action["channel"] - 1)
                    batch = MidiBatch(name=f"solo_ch{action['channel']}")
                    batch.add(MidiCmd(midi_bus, CC.SOLO.cc, 127))

            elif act == "unsolo":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    ch.soloed = False
                    midi_bus = MidiBus(action["channel"] - 1)
                    batch = MidiBatch(name=f"unsolo_ch{action['channel']}")
                    batch.add(MidiCmd(midi_bus, CC.SOLO.cc, 0))

            elif act == "set_fx_send":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    db = action["db"]
                    fx = action.get("fx", 1)
                    if fx == 1:
                        ch.fx1_send_db = db
                        cc_def = CC.FX1_SEND
                    else:
                        ch.fx2_send_db = db
                        cc_def = CC.FX2_SEND
                    midi_bus = MidiBus(action["channel"] - 1)
                    batch = MidiBatch(name=f"fx{fx}_send_ch{action['channel']}")
                    batch.add(MidiCmd(midi_bus, cc_def.cc, Convert.fader_to_cc(db)))

            elif act == "set_fx_type":
                fx = self.state.state.fx.get(action["fx"])
                if fx:
                    fx.type = action["type"]
                    midi_bus = MidiBus.FX1 if action["fx"] == 1 else MidiBus.FX2
                    batch = MidiBatch(name=f"fx{action['fx']}_type")
                    batch.add(MidiCmd(midi_bus, CC.FX_TYPE.cc, action["type"]))

            elif act == "set_fx_mix":
                fx = self.state.state.fx.get(action["fx"])
                if fx:
                    fx.mix_pct = action["percent"]
                    midi_bus = MidiBus.FX1 if action["fx"] == 1 else MidiBus.FX2
                    batch = MidiBatch(name=f"fx{action['fx']}_mix")
                    batch.add(MidiCmd(midi_bus, CC.FX_MIX.cc,
                              Convert.percent_to_cc(action["percent"])))

            elif act == "set_fx_time":
                fx = self.state.state.fx.get(action["fx"])
                if fx:
                    fx.time_ms = action["ms"]
                    midi_bus = MidiBus.FX1 if action["fx"] == 1 else MidiBus.FX2
                    batch = MidiBatch(name=f"fx{action['fx']}_time")
                    batch.add(MidiCmd(midi_bus, CC.FX_TIME.cc,
                              int(action["ms"] * 127 / 3000)))

            elif act == "set_fx_feedback":
                fx = self.state.state.fx.get(action["fx"])
                if fx:
                    fx.feedback_pct = action["percent"]
                    midi_bus = MidiBus.FX1 if action["fx"] == 1 else MidiBus.FX2
                    batch = MidiBatch(name=f"fx{action['fx']}_feedback")
                    batch.add(MidiCmd(midi_bus, CC.FX_FEEDBACK.cc,
                              Convert.percent_to_cc(action["percent"])))

            elif act == "set_limiter":
                bus = self.state.state.get_bus(action["bus"])
                if bus:
                    bus.limiter_db = action["db"]
                    midi_bus = {"main": MidiBus.MAIN, "mon1": MidiBus.MON1,
                               "mon2": MidiBus.MON2}.get(action["bus"])
                    if midi_bus:
                        batch = MidiBatch(name=f"limiter_{action['bus']}")
                        batch.add(MidiCmd(midi_bus, CC.BUS_LIMITER.cc,
                                  Convert.limiter_to_cc(action["db"])))

            elif act == "save_snapshot":
                self.state.save_snapshot(action["name"])
                batch = MidiBatch(name="save_snapshot")

            elif act == "recall_snapshot":
                recalled = self.state.recall_snapshot(action["name"])
                if recalled:
                    batch = recalled

            elif act == "apply_preset":
                applied = self.state.apply_preset(
                    action["preset"], action["channel"])
                if applied:
                    batch = applied

            elif act == "reset_channel":
                ch = self.state.state.get_channel(action["channel"])
                if ch:
                    ch.reset()
                    batch = self.state.state.to_commands()

            elif act == "explain":
                return True, MidiBatch(name="explain")  # No MIDI needed

            else:
                self._log("unknown_action", act, False)
                return False, MidiBatch()

        except (KeyError, TypeError, ValueError) as e:
            self._log("action_error", f"{act}: {e}", False)
            return False, MidiBatch()

        return True, batch

    def execute(self, action: dict) -> ActionResult:
        """Execute a single action and send MIDI."""
        success, batch = self._execute_action(action)
        sent = 0
        if batch:
            sent = self.midi.send_batch(batch)

        result = ActionResult(
            success=success,
            actions_sent=sent,
            actions_failed=0 if success else 1,
        )
        self._emit("action", result)
        self._log("execute", json.dumps(action), success)
        return result

    def execute_many(self, actions: list[dict]) -> ActionResult:
        """Execute multiple actions."""
        total_sent = 0
        total_failed = 0

        for action in actions:
            result = self.execute(action)
            total_sent += result.actions_sent
            total_failed += result.actions_failed

        return ActionResult(
            success=total_failed == 0,
            actions_sent=total_sent,
            actions_failed=total_failed,
        )

    # ── Natural Language Interface ──

    def process(self, user_input: str,
                audio_context: Optional[dict] = None) -> ActionResult:
        """
        Process a natural language command.

        This is the main entry point for user interaction.
        """
        start = time.time()

        # Get audio context if not provided
        if audio_context is None and self.audio.is_running:
            analysis = self.audio.latest
            if analysis:
                audio_context = {
                    ch: chan.to_dict()
                    for ch, chan in analysis.channels.items()
                }

        # Parse with LLM
        resp = self.llm.parse_command(user_input, audio_context)

        if resp.parse_error:
            return ActionResult(
                success=False,
                error="Failed to parse command",
                reasoning=resp.raw,
                latency_ms=(time.time() - start) * 1000,
            )

        # Execute actions
        result = self.execute_many(resp.actions)
        result.reasoning = resp.reasoning
        result.confidence = resp.confidence
        result.warnings = resp.warnings
        result.latency_ms = (time.time() - start) * 1000

        return result

    # ── Auto-Mix Engine ──

    def auto_mix_step(self) -> Optional[ActionResult]:
        """
        Perform one auto-mix step.

        Analyzes current audio and makes adjustments.
        Called periodically when in AUTO mode.
        """
        if not self.audio.is_running:
            return None

        analysis = self.audio.latest
        if not analysis:
            return None

        # Build analysis dict for LLM
        analysis_dict = {
            "mix": {
                "rms_db": round(analysis.mix_rms_db, 1),
                "peak_db": round(analysis.mix_peak_db, 1),
                "feedback": analysis.feedback_detected,
            },
            "channels": {
                ch: chan.to_dict()
                for ch, chan in analysis.channels.items()
            }
        }

        # Get LLM suggestions
        resp = self.llm.auto_mix_suggestion(analysis_dict)

        if resp.parse_error or not resp.actions:
            return None

        # Filter: only apply if confidence is high enough
        if resp.confidence < 0.6:
            return ActionResult(
                success=False,
                reasoning=f"Low confidence ({resp.confidence:.0%}): {resp.reasoning}",
                confidence=resp.confidence,
            )

        result = self.execute_many(resp.actions)
        result.reasoning = resp.reasoning
        result.confidence = resp.confidence
        return result

    # ── Quick Actions ──

    def quick_gain_staging(self) -> ActionResult:
        """
        Automatic gain staging across all active channels.

        Sets gains so peaks hit -18 dBFS (analog sweet spot).
        """
        if not self.audio.is_running:
            return ActionResult(False, error="Audio not running")

        analysis = self.audio.latest
        if not analysis:
            return ActionResult(False, error="No audio data")

        actions = []
        for ch_num, ch_analysis in analysis.channels.items():
            if not ch_analysis.has_signal or ch_num > 4:
                continue

            # Target: -18 dBFS RMS for healthy analog-style level
            target_rms = -18.0
            current_gain = self.state.state.get_channel(ch_num)
            if not current_gain:
                continue

            gain_adjustment = target_rms - ch_analysis.rms_db
            new_gain = current_gain.gain_db + gain_adjustment
            new_gain = max(-20, min(60, new_gain))

            if abs(gain_adjustment) > 1:  # Only adjust if significant
                actions.append({
                    "action": "set_gain",
                    "channel": ch_num,
                    "db": round(new_gain, 1),
                })

        if actions:
            return self.execute_many(actions)
        return ActionResult(True, reasoning="All gains are in optimal range")

    def quick_feedback_fix(self) -> ActionResult:
        """Automatically detect and suppress feedback."""
        if not self.audio.is_running:
            return ActionResult(False, error="Audio not running")

        analysis = self.audio.latest
        if not analysis or not analysis.feedback_detected:
            return ActionResult(True, reasoning="No feedback detected")

        freq = analysis.feedback_freq or 1000
        actions = []

        # Find which channel has energy at the feedback frequency
        for ch_num, ch_analysis in analysis.channels.items():
            if not ch_analysis.has_signal:
                continue

            # Check if this channel has energy near the feedback freq
            band_name = self._freq_to_band(freq)
            energy = ch_analysis.band_energies.get(band_name, -100)

            if energy > -20:  # Significant energy
                # Apply a narrow cut at the feedback frequency
                actions.append({
                    "action": "set_eq",
                    "channel": ch_num,
                    "band": band_name,
                    "db": -6,
                })
                actions.append({
                    "action": "set_eq_q",
                    "channel": ch_num,
                    "band": band_name,
                    "q": 5.0,
                })

        if actions:
            return self.execute_many(actions)
        return ActionResult(True, reasoning="Feedback detected but couldn't identify source")

    @staticmethod
    def _freq_to_band(hz: float) -> str:
        """Map a frequency to the nearest EQ band."""
        if hz < 150:
            return "low"
        elif hz < 800:
            return "lowmid"
        elif hz < 5000:
            return "highmid"
        else:
            return "high"

    # ── Scene Playback ──

    def play_scene(self, scene_commands: list[dict],
                   interval_ms: float = 100) -> ActionResult:
        """
        Play a sequence of actions as a scene.

        Args:
            scene_commands: List of {action, timing_ms, ...} dicts
            interval_ms: Default interval between commands
        """
        total_sent = 0
        total_failed = 0

        for cmd_spec in scene_commands:
            timing = cmd_spec.pop("timing_ms", interval_ms)
            if timing > 0:
                time.sleep(timing / 1000)

            result = self.execute(cmd_spec)
            total_sent += result.actions_sent
            total_failed += result.actions_failed

        return ActionResult(
            success=total_failed == 0,
            actions_sent=total_sent,
            actions_failed=total_failed,
        )

    # ── Info Methods ──

    def status(self) -> dict:
        """Get full system status."""
        return {
            "mode": self.mode.name,
            "midi": {
                "connected": self.midi.is_connected,
                "port": self.midi.port_name,
            },
            "audio": {
                "running": self.audio.is_running,
                "devices": self.audio.list_devices() if hasattr(self.audio, 'list_devices') else [],
            },
            "llm": {
                "connected": self.llm.check(),
                "model": self.llm.config.model,
                "provider": self.llm.config.provider.name,
            },
            "state": {
                "channels": len(self.state.state.channels),
                "snapshots": len(self.state.snapshots),
                "presets": len(self.state.presets),
                "history": len(self.state.history.entries),
            },
        }

    def explain_status(self) -> str:
        """Get natural language explanation of current state."""
        analysis_dict = None
        if self.audio.is_running:
            analysis = self.audio.latest
            if analysis:
                analysis_dict = {
                    "mix": {
                        "rms_db": round(analysis.mix_rms_db, 1),
                        "peak_db": round(analysis.mix_peak_db, 1),
                    },
                    "channels": {
                        ch: chan.to_dict()
                        for ch, chan in analysis.channels.items()
                    }
                }

        state_dict = self.state.state.to_dict()
        combined = {"mixer_state": state_dict, "audio_analysis": analysis_dict}
        return self.llm.explain_mix(combined)
