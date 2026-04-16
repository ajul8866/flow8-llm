"""
Audio Analysis Engine for Flow8-LLM.

Captures audio from Flow 8 USB interface, performs real-time analysis:
- FFT spectrum analysis
- RMS/Peak level metering per channel
- LUFS loudness measurement
- Frequency band energy detection
- Noise floor analysis
- Phase correlation
- Feedback detection
"""

from __future__ import annotations

import struct
import threading
import time
import math
from dataclasses import dataclass, field
from collections import deque
from typing import Optional

import numpy as np


# ─── Frequency Bands ────────────────────────────────────────────────────

@dataclass(frozen=True)
class FreqBand:
    """Frequency band definition."""
    name: str
    lo: float
    hi: float
    color: str = ""  # For TUI display

BANDS = [
    FreqBand("sub", 20, 60, "█"),        # Sub bass
    FreqBand("low", 60, 250, "▓"),       # Bass
    FreqBand("lowmid", 250, 500, "▒"),   # Low midrange
    FreqBand("mid", 500, 2000, "░"),     # Midrange
    FreqBand("highmid", 2000, 4000, "·"),# Upper midrange
    FreqBand("high", 4000, 8000, "."),   # Presence
    FreqBand("air", 8000, 20000, "·"),   # Brilliance
]


# ─── Analysis Results ──────────────────────────────────────────────────

@dataclass
class ChannelAnalysis:
    """Analysis result for a single channel."""
    channel: int
    rms_db: float = -100.0
    peak_db: float = -100.0
    lufs: float = -100.0
    crest_factor: float = 0.0
    noise_floor_db: float = -100.0
    phase_correlation: float = 1.0
    band_energies: dict[str, float] = field(default_factory=dict)
    spectrum: np.ndarray = field(default_factory=lambda: np.zeros(512))
    freq_bins: np.ndarray = field(default_factory=lambda: np.zeros(512))
    timestamp: float = 0.0
    clipping: bool = False
    has_signal: bool = False

    @property
    def dominant_band(self) -> str:
        """Get the frequency band with most energy."""
        if not self.band_energies:
            return "none"
        return max(self.band_energies, key=self.band_energies.get)

    @property
    def headroom_db(self) -> float:
        """Available headroom before clipping (0 dBFS)."""
        return 0.0 - self.peak_db

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "rms_db": round(self.rms_db, 1),
            "peak_db": round(self.peak_db, 1),
            "lufs": round(self.lufs, 1),
            "headroom_db": round(self.headroom_db, 1),
            "dominant_band": self.dominant_band,
            "clipping": self.clipping,
            "has_signal": self.has_signal,
            "bands": {k: round(v, 1) for k, v in self.band_energies.items()},
        }


@dataclass
class MixAnalysis:
    """Full mix analysis across all channels."""
    channels: dict[int, ChannelAnalysis] = field(default_factory=dict)
    mix_rms_db: float = -100.0
    mix_peak_db: float = -100.0
    mix_lufs: float = -100.0
    stereo_width: float = 0.0
    feedback_detected: bool = False
    feedback_freq: Optional[float] = None
    timestamp: float = 0.0

    def get(self, channel: int) -> Optional[ChannelAnalysis]:
        return self.channels.get(channel)


# ─── FFT Analyzer ──────────────────────────────────────────────────────

class SpectrumAnalyzer:
    """FFT-based spectrum analyzer with configurable resolution."""

    def __init__(self, sample_rate: int = 48000, fft_size: int = 2048):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.window = np.hanning(fft_size)
        self.freq_bins = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)

    def analyze(self, samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Perform FFT analysis on audio samples.

        Returns:
            (magnitudes_db, freq_bins) - spectrum in dB and corresponding frequencies
        """
        if len(samples) < self.fft_size:
            # Zero-pad if needed
            padded = np.zeros(self.fft_size)
            padded[:len(samples)] = samples
            samples = padded

        # Take last fft_size samples
        segment = samples[-self.fft_size:]

        # Apply window
        windowed = segment * self.window

        # FFT
        spectrum = np.fft.rfft(windowed)
        magnitudes = np.abs(spectrum)

        # Convert to dB (with floor to avoid log(0))
        magnitudes_db = 20 * np.log10(np.maximum(magnitudes, 1e-10))

        return magnitudes_db, self.freq_bins

    def band_energy(self, magnitudes: np.ndarray, band: FreqBand) -> float:
        """Get average energy in a frequency band (in dB)."""
        mask = (self.freq_bins >= band.lo) & (self.freq_bins <= band.hi)
        if not np.any(mask):
            return -100.0
        return float(np.mean(magnitudes[mask]))


# ─── Level Meter ───────────────────────────────────────────────────────

class LevelMeter:
    """RMS/Peak level metering with ballistics."""

    def __init__(self, sample_rate: int = 48000, integration_ms: float = 300.0):
        self.sample_rate = sample_rate
        self.integration_samples = int(sample_rate * integration_ms / 1000)
        self._peak_hold = -100.0
        self._peak_decay = 0.9995  # Peak hold decay factor
        self._rms_buffer = deque(maxlen=self.integration_samples)

    def update(self, samples: np.ndarray) -> tuple[float, float]:
        """
        Update meter with new samples.

        Returns:
            (rms_db, peak_db)
        """
        if len(samples) == 0:
            return -100.0, -100.0

        # RMS calculation
        self._rms_buffer.extend(samples ** 2)
        if len(self._rms_buffer) > 0:
            rms = math.sqrt(np.mean(list(self._rms_buffer)))
            rms_db = 20 * math.log10(max(rms, 1e-10))
        else:
            rms_db = -100.0

        # Peak calculation
        peak = np.max(np.abs(samples))
        peak_db = 20 * math.log10(max(peak, 1e-10))

        # Peak hold
        if peak_db > self._peak_hold:
            self._peak_hold = peak_db
        else:
            self._peak_hold = max(peak_db, self._peak_hold * self._peak_decay)

        return rms_db, self._peak_hold


# ─── LUFS Meter ────────────────────────────────────────────────────────

class LUFSMeter:
    """Simplified LUFS (Loudness) meter based on ITU-R BS.1770."""

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self._gate_block_size = int(sample_rate * 0.4)  # 400ms blocks
        self._short_block_size = int(sample_rate * 0.003)  # 3ms for momentary
        self._loudness_history = deque(maxlen=100)
        self._momentary_buf = deque(maxlen=30)  # ~300ms momentary

    def _k_weighted_rms(self, samples: np.ndarray) -> float:
        """Apply K-weighting approximation and compute RMS."""
        # Simplified K-weighting: boost high frequencies ~4dB above 1kHz
        # Full implementation would use pre-filter + RLB filter
        fft = np.fft.rfft(samples)
        freqs = np.fft.rfftfreq(len(samples), 1.0 / self.sample_rate)

        # Approximate K-weighting curve
        weight = np.ones_like(freqs)
        weight[freqs > 1000] *= 2.5  # ~4dB boost
        weight[freqs > 8000] *= 0.7  # Slight rolloff at very high

        weighted = np.abs(fft) * weight
        power = np.mean(weighted ** 2)
        return power

    def update(self, samples: np.ndarray) -> float:
        """
        Update LUFS measurement.

        Returns:
            momentary_lufs (float) - Momentary loudness in LUFS
        """
        if len(samples) < 1024:
            return -100.0

        power = self._k_weighted_rms(samples)
        if power > 0:
            loudness = -0.691 + 10 * math.log10(power)
        else:
            loudness = -100.0

        self._momentary_buf.append(loudness)

        if len(self._momentary_buf) > 0:
            return float(np.mean(list(self._momentary_buf)))
        return -100.0

    @property
    def integrated_lufs(self) -> float:
        """Integrated loudness over measurement period."""
        if len(self._loudness_history) == 0:
            return -100.0
        return float(np.mean(list(self._loudness_history)))


# ─── Feedback Detector ─────────────────────────────────────────────────

class FeedbackDetector:
    """Detects acoustic feedback by looking for sustained narrow peaks."""

    def __init__(self, sample_rate: int = 48000, fft_size: int = 4096):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.window = np.hanning(fft_size)
        self.freq_bins = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)
        self._peak_history: deque = deque(maxlen=50)
        self._threshold_db = 20.0  # Peak must be this much above average
        self._sustain_count = 10    # Must sustain for this many frames

    def check(self, samples: np.ndarray) -> tuple[bool, Optional[float]]:
        """
        Check for feedback in audio samples.

        Returns:
            (is_feedback, frequency_hz)
        """
        if len(samples) < self.fft_size:
            return False, None

        segment = samples[-self.fft_size:]
        windowed = segment * self.window
        spectrum = np.fft.rfft(windowed)
        magnitudes_db = 20 * np.log10(np.maximum(np.abs(spectrum), 1e-10))

        # Find peaks significantly above average
        avg = np.mean(magnitudes_db)
        peaks = magnitudes_db - avg
        peak_idx = np.argmax(peaks)

        if peaks[peak_idx] > self._threshold_db:
            freq = self.freq_bins[peak_idx]
            self._peak_history.append(freq)

            # Check if same frequency sustained
            if len(self._peak_history) >= self._sustain_count:
                recent = list(self._peak_history)[-self._sustain_count:]
                if len(set(recent)) <= 2:  # Very few unique frequencies = feedback
                    return True, float(np.mean(recent))
        else:
            self._peak_history.clear()

        return False, None


# ─── Audio Capture Engine ──────────────────────────────────────────────

class AudioEngine:
    """
    Main audio capture and analysis engine.

    Captures audio from Flow 8 USB interface (or any audio device)
    and provides real-time analysis.
    """

    def __init__(
        self,
        device_name: Optional[str] = None,
        sample_rate: int = 48000,
        channels: int = 8,
        buffer_size: int = 1024,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.buffer_size = buffer_size
        self.device_name = device_name

        # Analysis components
        self.spectrum = SpectrumAnalyzer(sample_rate)
        self.meters = {ch: LevelMeter(sample_rate) for ch in range(1, channels + 1)}
        self.lufs = {ch: LUFSMeter(sample_rate) for ch in range(1, channels + 1)}
        self.feedback = FeedbackDetector(sample_rate)

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._audio_buffer: dict[int, np.ndarray] = {}
        self._latest_analysis: Optional[MixAnalysis] = None
        self._analysis_callbacks: list = []

        # Initialize audio buffers
        for ch in range(1, channels + 1):
            self._audio_buffer[ch] = np.zeros(buffer_size * 4)  # Ring buffer

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def latest(self) -> Optional[MixAnalysis]:
        """Get latest analysis result."""
        with self._lock:
            return self._latest_analysis

    def analyze_buffer(self, audio_data: np.ndarray, channel: int) -> ChannelAnalysis:
        """
        Analyze a buffer of audio data for a specific channel.

        Args:
            audio_data: numpy array of audio samples (float32, -1.0 to 1.0)
            channel: channel number (1-based)
        """
        result = ChannelAnalysis(channel=channel, timestamp=time.time())

        if len(audio_data) < 64:
            return result

        # Check for signal
        peak = np.max(np.abs(audio_data))
        result.has_signal = peak > 0.001  # -60 dBFS threshold
        result.clipping = peak > 0.99

        if not result.has_signal:
            return result

        # Level metering
        rms_db, peak_db = self.meters.get(channel, LevelMeter(self.sample_rate)).update(audio_data)
        result.rms_db = rms_db
        result.peak_db = peak_db

        # Crest factor (peak - rms, indicates dynamic range)
        result.crest_factor = peak_db - rms_db if rms_db > -100 else 0

        # LUFS
        result.lufs = self.lufs.get(channel, LUFSMeter(self.sample_rate)).update(audio_data)

        # Spectrum analysis
        magnitudes, freqs = self.spectrum.analyze(audio_data)
        result.spectrum = magnitudes
        result.freq_bins = freqs

        # Band energies
        for band in BANDS:
            result.band_energies[band.name] = self.spectrum.band_energy(magnitudes, band)

        return result

    def analyze_mix(self, channel_data: dict[int, np.ndarray]) -> MixAnalysis:
        """
        Analyze all channels and produce mix analysis.

        Args:
            channel_data: {channel_number: audio_samples}
        """
        mix = MixAnalysis(timestamp=time.time())

        all_samples = []
        for ch, samples in channel_data.items():
            ch_analysis = self.analyze_buffer(samples, ch)
            mix.channels[ch] = ch_analysis
            if ch_analysis.has_signal:
                all_samples.append(samples)

        # Mix-level analysis
        if all_samples:
            mix_sum = np.sum(all_samples, axis=0) / len(all_samples)
            mix.mix_rms_db = 20 * np.log10(max(np.sqrt(np.mean(mix_sum ** 2)), 1e-10))
            mix.mix_peak_db = 20 * np.log10(max(np.max(np.abs(mix_sum)), 1e-10))

            # Feedback detection on mix
            is_fb, fb_freq = self.feedback.check(mix_sum)
            mix.feedback_detected = is_fb
            mix.feedback_freq = fb_freq

        with self._lock:
            self._latest_analysis = mix

        return mix

    def on_analysis(self, callback):
        """Register callback for analysis results."""
        self._analysis_callbacks.append(callback)

    def start_capture(self):
        """Start real-time audio capture (requires sounddevice)."""
        try:
            import sounddevice as sd
        except ImportError:
            raise RuntimeError("sounddevice not installed. Run: pip install sounddevice")

        self._running = True

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"Audio status: {status}")

            # Split channels
            channel_data = {}
            for ch in range(min(indata.shape[1], self.channels)):
                channel_data[ch + 1] = indata[:, ch]

            result = self.analyze_mix(channel_data)
            for cb in self._analysis_callbacks:
                cb(result)

        self._stream = sd.InputStream(
            device=self.device_name,
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self.buffer_size,
            callback=audio_callback,
        )
        self._stream.start()

    def stop_capture(self):
        """Stop audio capture."""
        self._running = False
        if hasattr(self, '_stream'):
            self._stream.stop()
            self._stream.close()

    @staticmethod
    def list_devices() -> list[dict]:
        """List available audio devices."""
        try:
            import sounddevice as sd
            return [{"id": d["index"], "name": d["name"], "inputs": d["max_input_channels"]}
                    for d in sd.query_devices() if d["max_input_channels"] > 0]
        except ImportError:
            return []

    def generate_test_signal(self, channel: int, freq: float = 1000,
                              duration: float = 0.5) -> np.ndarray:
        """Generate a test tone for testing without hardware."""
        t = np.linspace(0, duration, int(self.sample_rate * duration), False)
        signal = 0.5 * np.sin(2 * np.pi * freq * t)
        return signal.astype(np.float32)
