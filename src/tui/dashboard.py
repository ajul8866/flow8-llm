"""
TUI Dashboard for Flow8-LLM.

Real-time terminal interface showing:
- Spectrum analyzer (ASCII)
- VU meters per channel
- Channel strip overview
- Command console
- Status bar
"""

from __future__ import annotations

import os
import sys
import time
import threading
from typing import Optional

from ..core.brain import Brain, BrainMode
from ..core.audio import MixAnalysis, BANDS


# ─── ANSI Colors ────────────────────────────────────────────────────────

class C:
    """ANSI color codes."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


# ─── Spectrum Visualizer ───────────────────────────────────────────────

class SpectrumViz:
    """ASCII spectrum analyzer visualization."""

    BARS = " ░▒▓█"
    HEIGHT = 8
    WIDTH = 60

    @staticmethod
    def render(magnitudes, freq_bins, width: int = 60, height: int = 8) -> str:
        """Render spectrum as ASCII art."""
        if magnitudes is None or len(magnitudes) == 0:
            return "No signal\n" * height

        # Normalize to 0-1 range
        db_min, db_max = -80, 0
        norm = (magnitudes - db_min) / (db_max - db_min)
        norm = norm.clip(0, 1)

        # Bin into display columns
        if len(norm) > width:
            chunk_size = len(norm) // width
            binned = [norm[i*chunk_size:(i+1)*chunk_size].mean()
                      for i in range(width)]
        else:
            binned = list(norm) + [0] * (width - len(norm))

        # Build bars
        lines = []
        for row in range(height - 1, -1, -1):
            threshold = row / height
            line = ""
            for val in binned:
                if val >= threshold + (1 / height):
                    line += f"{C.CYAN}█{C.RESET}"
                elif val >= threshold:
                    intensity = int((val - threshold) * height * 4)
                    intensity = min(3, max(0, intensity))
                    line += f"{C.CYAN}{SpectrumViz.BARS[intensity]}{C.RESET}"
                else:
                    line += " "
            lines.append(line)

        # Frequency labels
        labels = "20Hz" + " " * (width // 3 - 4) + "200Hz"
        labels += " " * (width // 3 - 5) + "2kHz"
        labels += " " * (width // 3 - 4) + "20kHz"
        lines.append(f"{C.DIM}{labels[:width]}{C.RESET}")

        return "\n".join(lines)


# ─── VU Meter ──────────────────────────────────────────────────────────

class VUMeter:
    """ASCII VU meter."""

    @staticmethod
    def render(db: float, peak: float = -100, width: int = 30,
               label: str = "", show_peak: bool = True) -> str:
        """Render a horizontal VU meter."""
        # Map dB to position (-60 to 0 dBFS)
        db = max(-60, min(0, db))
        peak = max(-60, min(0, peak))

        pos = int((db + 60) / 60 * width)
        peak_pos = int((peak + 60) / 60 * width)
        pos = min(width, max(0, pos))
        peak_pos = min(width, max(0, peak_pos))

        # Build meter
        meter = ""
        for i in range(width):
            if i == peak_pos and show_peak:
                meter += f"{C.RED}▲{C.RESET}"
            elif i < pos:
                if i < width * 0.6:  # Green zone
                    meter += f"{C.GREEN}█{C.RESET}"
                elif i < width * 0.8:  # Yellow zone
                    meter += f"{C.YELLOW}█{C.RESET}"
                else:  # Red zone
                    meter += f"{C.RED}█{C.RESET}"
            else:
                meter += f"{C.DIM}░{C.RESET}"

        db_str = f"{db:+5.1f}dB"
        return f"{label:>6} |{meter}| {db_str}"


# ─── Channel Strip ─────────────────────────────────────────────────────

class ChannelStrip:
    """ASCII channel strip display."""

    @staticmethod
    def render(state, analysis=None) -> str:
        """Render a channel strip."""
        ch = state.channel

        # Header
        lines = [f"{C.BOLD}CH{ch}{C.RESET}"]

        # Mute/Solo indicators
        indicators = ""
        if state.muted:
            indicators += f"{C.BG_RED}{C.WHITE}M{C.RESET} "
        if state.soloed:
            indicators += f"{C.BG_YELLOW}{C.WHITE}S{C.RESET} "
        lines.append(indicators if indicators else "  ")

        # Level meter
        if analysis and analysis.has_signal:
            meter = VUMeter.render(analysis.rms_db, analysis.peak_db, width=8)
            lines.append(meter)
            lines.append(f" {analysis.rms_db:+5.1f}")
        else:
            lines.append(f"{C.DIM}--------{C.RESET}")
            lines.append(f"{C.DIM}  ----{C.RESET}")

        # Fader
        lines.append(f"F:{state.fader_db:+4.0f}")

        # Pan
        if abs(state.pan) < 0.1:
            pan_str = " C "
        elif state.pan < 0:
            pan_str = f"L{int(abs(state.pan)*100):>2}"
        else:
            pan_str = f"R{int(state.pan*100):>2}"
        lines.append(f"P:{pan_str}")

        # EQ summary
        eq_parts = []
        if state.eq_low_db != 0:
            eq_parts.append(f"L{state.eq_low_db:+.0f}")
        if state.eq_lowmid_db != 0:
            eq_parts.append(f"LM{state.eq_lowmid_db:+.0f}")
        if state.eq_highmid_db != 0:
            eq_parts.append(f"HM{state.eq_highmid_db:+.0f}")
        if state.eq_high_db != 0:
            eq_parts.append(f"H{state.eq_high_db:+.0f}")
        lines.append(" ".join(eq_parts) if eq_parts else f"{C.DIM}flat{C.RESET}")

        return "\n".join(lines)


# ─── Dashboard ─────────────────────────────────────────────────────────

class Dashboard:
    """Main TUI dashboard."""

    def __init__(self, brain: Brain):
        self.brain = brain
        self._running = False
        self._update_interval = 0.1  # 100ms refresh
        self._command_buffer = ""

    def clear_screen(self):
        """Clear terminal screen."""
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

    def render_header(self) -> str:
        """Render dashboard header."""
        status = self.brain.status()
        mode_color = {
            "MANUAL": C.BLUE,
            "ASSISTED": C.CYAN,
            "AUTO": C.GREEN,
            "SCENE": C.MAGENTA,
            "LEARN": C.YELLOW,
        }.get(status["mode"], C.WHITE)

        midi_status = f"{C.GREEN}●{C.RESET}" if status["midi"]["connected"] else f"{C.RED}●{C.RESET}"
        audio_status = f"{C.GREEN}●{C.RESET}" if status["audio"]["running"] else f"{C.RED}●{C.RESET}"
        llm_status = f"{C.GREEN}●{C.RESET}" if status["llm"]["connected"] else f"{C.RED}●{C.RESET}"

        header = f"""
{C.BOLD}╔══════════════════════════════════════════════════════════════╗
║  Flow8-LLM: AI Mixing Brain                                ║
╠══════════════════════════════════════════════════════════════╣
║  Mode: {mode_color}{status['mode']:<10}{C.RESET}  MIDI: {midi_status}  Audio: {audio_status}  LLM: {llm_status} ({status['llm']['model']})  ║
╚══════════════════════════════════════════════════════════════╝{C.RESET}"""
        return header

    def render_channels(self) -> str:
        """Render all channel strips side by side."""
        state = self.brain.state.state
        analysis = self.brain.audio.latest

        # Split channels into groups
        mono_chs = [1, 2, 3, 4]
        stereo_chs = [5, 6, 7, 8]

        lines = []
        lines.append(f"\n{C.BOLD}── Channels ──────────────────────────────────────────────────{C.RESET}")

        # Mono channels
        strips = []
        for ch in mono_chs:
            ch_state = state.get_channel(ch)
            ch_analysis = analysis.get(ch) if analysis else None
            if ch_state:
                strips.append(ChannelStrip.render(ch_state, ch_analysis).split("\n"))

        # Interleave strip lines
        if strips:
            max_lines = max(len(s) for s in strips)
            for row in range(max_lines):
                line = "  ".join(s[row] if row < len(s) else "      " for s in strips)
                lines.append(line)

        return "\n".join(lines)

    def render_spectrum(self) -> str:
        """Render spectrum analyzer."""
        analysis = self.brain.audio.latest
        if not analysis:
            return f"\n{C.DIM}No audio analysis available{C.RESET}"

        lines = [f"\n{C.BOLD}── Spectrum ─────────────────────────────────────────────────{C.RESET}"]

        for ch_num in sorted(analysis.channels.keys()):
            ch = analysis.channels[ch_num]
            if ch.has_signal:
                viz = SpectrumViz.render(ch.spectrum, ch.freq_bins, width=50, height=4)
                lines.append(f"\n{C.BOLD}CH{ch_num}{C.RESET} ({ch.dominant_band}, {ch.rms_db:+.1f}dB):")
                lines.append(viz)

        return "\n".join(lines)

    def render_state(self) -> str:
        """Render current mixer state summary."""
        state = self.brain.state.state

        lines = [f"\n{C.BOLD}── Mixer State ──────────────────────────────────────────────{C.RESET}"]

        for ch_num in sorted(state.channels.keys()):
            ch = state.channels[ch_num]
            if not ch.is_default:
                parts = []
                if ch.fader_db != 0:
                    parts.append(f"fader={ch.fader_db:+.0f}dB")
                if ch.gain_db != 30 and ch_num <= 4:
                    parts.append(f"gain={ch.gain_db:+.0f}dB")
                if ch.muted:
                    parts.append(f"{C.RED}MUTED{C.RESET}")
                if ch.lowcut_enabled and ch.lowcut_hz > 20:
                    parts.append(f"HPF={ch.lowcut_hz:.0f}Hz")
                eq_changed = any([
                    ch.eq_low_db != 0, ch.eq_lowmid_db != 0,
                    ch.eq_highmid_db != 0, ch.eq_high_db != 0,
                ])
                if eq_changed:
                    parts.append("EQ≠flat")
                if abs(ch.pan) > 0.1:
                    parts.append(f"pan={ch.pan:+.1f}")

                if parts:
                    lines.append(f"  CH{ch_num}: {' | '.join(parts)}")

        if all(ch.is_default for ch in state.channels.values()):
            lines.append(f"  {C.DIM}All channels at default{C.RESET}")

        return "\n".join(lines)

    def render_history(self) -> str:
        """Render recent command history."""
        log = self.brain._event_log[-10:]

        lines = [f"\n{C.BOLD}── Recent Actions ───────────────────────────────────────────{C.RESET}"]

        if not log:
            lines.append(f"  {C.DIM}No actions yet{C.RESET}")

        for entry in log:
            ts = time.strftime("%H:%M:%S", time.localtime(entry["time"]))
            status = f"{C.GREEN}✓{C.RESET}" if entry["success"] else f"{C.RED}✗{C.RESET}"
            action = entry["action"]
            detail = entry.get("detail", "")[:50]
            lines.append(f"  {ts} {status} {action}: {detail}")

        return "\n".join(lines)

    def render_full(self) -> str:
        """Render full dashboard."""
        parts = [
            self.render_header(),
            self.render_channels(),
            self.render_spectrum(),
            self.render_state(),
            self.render_history(),
            f"\n{C.BOLD}── Command ──────────────────────────────────────────────────{C.RESET}",
            f"  {C.CYAN}>{C.RESET} {self._command_buffer}█",
            f"\n{C.DIM}Type command, 'help' for options, Ctrl+C to exit{C.RESET}",
        ]
        return "\n".join(parts)

    def run(self):
        """Run the dashboard in full-screen mode."""
        self._running = True

        try:
            while self._running:
                self.clear_screen()
                print(self.render_full())
                time.sleep(self._update_interval)
        except KeyboardInterrupt:
            self._running = False
            print("\nDashboard closed.")
