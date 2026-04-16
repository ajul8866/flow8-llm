"""
LLM Engine for Flow8-LLM.

Integrates with Ollama (local) or cloud APIs for:
- Natural language → MIDI command translation
- Audio-aware mixing suggestions
- Multi-step mixing workflows
- Genre-specific presets
- Chain-of-thought mixing reasoning
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any
from pathlib import Path

import requests


# ─── Provider Types ─────────────────────────────────────────────────────

class LLMProvider(Enum):
    OLLAMA = auto()
    OPENAI = auto()
    ANTHROPIC = auto()
    CUSTOM = auto()


@dataclass
class LLMConfig:
    """LLM configuration."""
    provider: LLMProvider = LLMProvider.OLLAMA
    model: str = "gemma4:31b-cloud"
    base_url: str = "http://localhost:11434"
    api_key: str = ""
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout: int = 30


# ─── Prompt Engineering ────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert audio mixing engineer AI controlling a Behringer Flow 8 digital mixer.
You receive natural language commands and audio analysis data, and produce structured mixing actions.

AVAILABLE ACTIONS (JSON format):
1. {"action": "set_fader", "channel": <1-8|"main"|"mon1"|"mon2"|"fx1"|"fx2">, "db": <-70 to +10>}
2. {"action": "set_gain", "channel": <1-4>, "db": <-20 to +60>}
3. {"action": "set_eq", "channel": <1-8>, "band": <"low"|"lowmid"|"highmid"|"high">, "db": <-15 to +15>}
4. {"action": "set_eq_freq", "channel": <1-8>, "band": <"low"|"lowmid"|"highmid"|"high">, "hz": <20-20000>}
5. {"action": "set_eq_q", "channel": <1-8>, "band": <"lowmid"|"highmid">, "q": <0.1-10>}
6. {"action": "set_lowcut", "channel": <1-8>, "hz": <20-600>}
7. {"action": "set_lowcut_en", "channel": <1-8>, "enabled": <true|false>}
8. {"action": "set_pan", "channel": <1-8>, "pan": <-1.0 to +1.0>}
9. {"action": "mute", "channel": <1-8|"main"|"mon1"|"mon2">}
10. {"action": "unmute", "channel": <1-8|"main"|"mon1"|"mon2">}
11. {"action": "solo", "channel": <1-8>}
12. {"action": "unsolo", "channel": <1-8>}
13. {"action": "set_fx_send", "channel": <1-8>, "fx": <1|2>, "db": <-70 to +10>}
14. {"action": "set_fx_type", "fx": <1|2>, "type": <0-15>}
15. {"action": "set_fx_time", "fx": <1|2>, "ms": <1-3000>}
16. {"action": "set_fx_feedback", "fx": <1|2>, "percent": <0-100>}
17. {"action": "set_fx_mix", "fx": <1|2>, "percent": <0-100>}
18. {"action": "set_fx_mute", "fx": <1|2>, "mute": <true|false>}
19. {"action": "set_limiter", "bus": <"main"|"mon1"|"mon2">, "db": <-30 to 0>}
20. {"action": "set_mon_send", "channel": <1-8>, "mon": <1|2>, "db": <-70 to +10>}
21. {"action": "phantom_48v", "channel": <1|2>, "enabled": <true|false>}
22. {"action": "mute_all", "mute": <true|false>}
23. {"action": "set_bt_level", "db": <-70 to +10>}
24. {"action": "set_usb_level", "db": <-70 to +10>}
25. {"action": "save_snapshot", "name": "<snapshot_name>"}
26. {"action": "recall_snapshot", "name": "<snapshot_name>"}
27. {"action": "apply_preset", "preset": "<preset_name>", "channel": <1-8>}
28. {"action": "reset_channel", "channel": <1-8>}
29. {"action": "explain", "text": "<your reasoning>"}

RESPONSE FORMAT:
Always respond with a JSON object:
{
  "reasoning": "Your chain-of-thought mixing analysis",
  "actions": [<list of action objects>],
  "confidence": 0.0-1.0,
  "warnings": ["any concerns or caveats"]
}

AUDIO ANALYSIS CONTEXT:
When audio analysis data is provided, use it to inform your decisions:
- rms_db: Average level (target -18 to -12 dBFS for healthy signal)
- peak_db: Peak level (keep below -3 dBFS for headroom)
- band_energies: Frequency content per band (sub/low/lowmid/mid/highmid/high/air)
- clipping: true if channel is clipping
- dominant_band: The frequency band with most energy
- crest_factor: Dynamic range indicator (higher = more dynamic)

MIXING PRINCIPLES:
- Always maintain headroom (peaks below -3 dBFS)
- Use EQ to create space, not just boost
- Low-cut everything except bass/kick (typically 80-100 Hz)
- Pan elements to create width (vocals/center, guitars/keys spread)
- Use FX sends for reverb/delay, not channel inserts
- Gain staging: set preamp gain first, then fader
- If clipping: reduce gain first, not fader
- For feedback: identify and cut the resonant frequency
- Genre awareness: adjust approach based on content type
"""


# ─── Conversation Manager ──────────────────────────────────────────────

@dataclass
class Message:
    role: str  # "system", "user", "assistant"
    content: str
    timestamp: float = 0.0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class Conversation:
    """Manages conversation history with context window control."""

    def __init__(self, max_messages: int = 50, max_tokens: int = 8000):
        self.messages: list[Message] = []
        self.max_messages = max_messages
        self.max_tokens = max_tokens

    def add(self, role: str, content: str, **metadata) -> Message:
        msg = Message(role=role, content=content, metadata=metadata)
        self.messages.append(msg)
        self._trim()
        return msg

    def _trim(self):
        """Trim conversation to fit within limits."""
        if len(self.messages) > self.max_messages:
            # Keep system messages and recent messages
            system_msgs = [m for m in self.messages if m.role == "system"]
            recent = self.messages[-(self.max_messages - len(system_msgs)):]
            self.messages = system_msgs + recent

    def to_api_messages(self) -> list[dict]:
        """Convert to API format."""
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def clear(self):
        self.messages.clear()

    @property
    def last_user_message(self) -> Optional[Message]:
        for m in reversed(self.messages):
            if m.role == "user":
                return m
        return None

    @property
    def last_assistant_message(self) -> Optional[Message]:
        for m in reversed(self.messages):
            if m.role == "assistant":
                return m
        return None


# ─── LLM Providers ─────────────────────────────────────────────────────

class OllamaProvider:
    """Ollama local LLM provider."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    def check(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except requests.ConnectionError:
            return False

    def list_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return [m["name"] for m in resp.json().get("models", [])]
        except Exception:
            return []

    def chat(self, messages: list[dict], system: str = "") -> str:
        """Send chat completion request to Ollama."""
        # Ollama /api/chat format
        ollama_messages = []
        if system:
            ollama_messages.append({"role": "system", "content": system})
        ollama_messages.extend(messages)

        payload = {
            "model": self.config.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
            }
        }

        resp = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("message", {}).get("content", "")

    def generate(self, prompt: str, system: str = "") -> str:
        """Simple generate (non-chat) endpoint."""
        payload = {
            "model": self.config.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
            }
        }

        resp = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")


class OpenAIProvider:
    """OpenAI-compatible API provider."""

    def __init__(self, config: LLMConfig):
        self.config = config

    def check(self) -> bool:
        return bool(self.config.api_key)

    def chat(self, messages: list[dict], system: str = "") -> str:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        payload = {
            "model": self.config.model,
            "messages": api_messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        resp = requests.post(
            f"{self.config.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ─── Response Parser ───────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Parsed LLM response."""
    reasoning: str = ""
    actions: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    raw: str = ""
    parse_error: bool = False
    latency_ms: float = 0.0

    @property
    def action_count(self) -> int:
        return len(self.actions)


def parse_llm_response(text: str) -> LLMResponse:
    """Parse LLM response into structured format."""
    resp = LLMResponse(raw=text)

    # Strategy 1: Try to find a top-level JSON array first (raw action list)
    # Use greedy match to get the outermost array
    array_match = re.search(r'\[.*\]', text, re.DOTALL)
    obj_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)

    # If we have both, pick whichever starts first (or array if they overlap)
    if array_match and obj_match:
        if array_match.start() <= obj_match.start():
            # Array comes first or at same position - try array
            try:
                actions = json.loads(array_match.group())
                if isinstance(actions, list) and len(actions) > 0:
                    resp.actions = actions
                    return resp
            except json.JSONDecodeError:
                pass
        # Fall through to try object
    elif array_match and not obj_match:
        # Only array found
        try:
            actions = json.loads(array_match.group())
            resp.actions = actions if isinstance(actions, list) else []
            return resp
        except json.JSONDecodeError:
            resp.parse_error = True
            return resp

    # Strategy 2: Try structured object {reasoning, actions, ...}
    if obj_match:
        try:
            data = json.loads(obj_match.group())
            if isinstance(data, dict):
                resp.reasoning = data.get("reasoning", "")
                resp.actions = data.get("actions", [])
                resp.confidence = data.get("confidence", 0.0)
                resp.warnings = data.get("warnings", [])
                return resp
        except json.JSONDecodeError:
            pass

    # Strategy 3: If array found but object parsing failed, try array again
    if array_match:
        try:
            actions = json.loads(array_match.group())
            resp.actions = actions if isinstance(actions, list) else []
            return resp
        except json.JSONDecodeError:
            resp.parse_error = True
            return resp

    resp.parse_error = True
    return resp


# ─── Main LLM Engine ───────────────────────────────────────────────────

class LLMEngine:
    """
    Main LLM engine with multi-provider support.

    Provides high-level mixing AI capabilities:
    - Parse natural language commands
    - Generate mixing suggestions from audio analysis
    - Multi-step workflows
    - Context-aware responses
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()
        self.conversation = Conversation()
        self._provider = self._create_provider()

        # Add system prompt
        self.conversation.add("system", SYSTEM_PROMPT)

    def _create_provider(self):
        if self.config.provider == LLMProvider.OLLAMA:
            return OllamaProvider(self.config)
        elif self.config.provider == LLMProvider.OPENAI:
            return OpenAIProvider(self.config)
        else:
            raise ValueError(f"Unsupported provider: {self.config.provider}")

    def check(self) -> bool:
        """Check if LLM is accessible."""
        return self._provider.check()

    def list_models(self) -> list[str]:
        """List available models."""
        if hasattr(self._provider, 'list_models'):
            return self._provider.list_models()
        return []

    # ── High-Level Methods ──

    def parse_command(self, user_input: str,
                      audio_context: Optional[dict] = None) -> LLMResponse:
        """
        Parse a natural language mixing command.

        Args:
            user_input: User's natural language command
            audio_context: Optional audio analysis data for context
        """
        prompt = user_input
        if audio_context:
            prompt = f"""AUDIO ANALYSIS DATA:
{json.dumps(audio_context, indent=2)}

USER COMMAND: {user_input}

Analyze the audio data and user command. Provide mixing actions with reasoning."""

        self.conversation.add("user", prompt)
        messages = self.conversation.to_api_messages()

        start = time.time()
        try:
            raw = self._provider.chat(messages)
        except Exception as e:
            resp = LLMResponse(raw=str(e), parse_error=True)
            return resp

        latency = (time.time() - start) * 1000
        resp = parse_llm_response(raw)
        resp.latency_ms = latency

        self.conversation.add("assistant", raw)
        return resp

    def analyze_spectrum(self, spectrum_data: dict,
                         context: str = "") -> LLMResponse:
        """
        Ask LLM to analyze spectrum and suggest EQ adjustments.

        Args:
            spectrum_data: {channel: {band: energy_db, ...}, ...}
            context: Additional context (e.g., "recording vocals")
        """
        prompt = f"""SPECTRUM ANALYSIS:
{json.dumps(spectrum_data, indent=2)}

CONTEXT: {context or "General mixing"}

Analyze the frequency content. Identify problems and suggest EQ adjustments:
- Muddy low-mids? Cut where needed.
- Harsh highs? Smooth with gentle cuts.
- Thin sound? Add warmth in the right band.
- Boomy? Find and reduce the resonance.

Provide specific EQ actions with dB values and frequency bands."""

        return self.parse_command(prompt)

    def auto_mix_suggestion(self, analysis: dict) -> LLMResponse:
        """
        Generate full mix suggestions from channel analysis.

        Args:
            analysis: Full mix analysis dict from AudioEngine
        """
        prompt = f"""FULL MIX ANALYSIS:
{json.dumps(analysis, indent=2)}

Review ALL channels. Suggest a complete mix adjustment:
1. Level balance (faders)
2. EQ corrections per channel
3. Panning for stereo width
4. Effects sends for space/depth
5. Bus processing if needed

Consider: frequency masking, level balance, stereo image, dynamics.
Produce a comprehensive set of actions."""

        return self.parse_command(prompt)

    def workflow(self, description: str) -> LLMResponse:
        """
        Generate multi-step workflow from description.

        Examples:
            - "prepare for live band performance"
            - "set up for podcast recording"
            - "configure for DJ streaming"
        """
        prompt = f"""WORKFLOW REQUEST: {description}

Generate a complete multi-step mixing workflow. Include:
1. Channel setup (gains, EQ basics)
2. Level balance
3. Effects configuration
4. Bus processing
5. Scene save if appropriate

Output ALL actions needed to go from default state to fully configured."""

        return self.parse_command(prompt)

    def explain_mix(self, analysis: dict) -> str:
        """Get a natural language explanation of the current mix."""
        prompt = f"""MIX STATE:
{json.dumps(analysis, indent=2)}

Describe the current mix in plain language:
- What's working well?
- What needs attention?
- Overall balance assessment
- Specific recommendations

Be concise but thorough. Use audio engineering terminology."""

        self.conversation.add("user", prompt)
        messages = self.conversation.to_api_messages()

        try:
            return self._provider.chat(messages)
        except Exception as e:
            return f"Error: {e}"

    def reset_conversation(self):
        """Reset conversation history."""
        self.conversation.clear()
        self.conversation.add("system", SYSTEM_PROMPT)
