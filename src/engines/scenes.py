"""
Scene Automation Engine for Flow8-LLM.

Manages timed scene changes for live shows:
- Scene sequences with timing
- Cue lists
- Crossfade between scenes
- Footswitch integration
"""

from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from ..core.brain import Brain


@dataclass
class SceneCue:
    """A single cue in a scene sequence."""
    name: str
    snapshot_name: str
    delay_sec: float = 0.0        # Delay after previous cue
    description: str = ""
    auto_advance: bool = True      # Auto-advance to next cue
    actions: list[dict] = field(default_factory=list)  # Additional actions

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "snapshot_name": self.snapshot_name,
            "delay_sec": self.delay_sec,
            "description": self.description,
            "auto_advance": self.auto_advance,
            "actions": self.actions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SceneCue':
        return cls(**data)


@dataclass
class SceneList:
    """A list of cues for a show/scene sequence."""
    name: str
    cues: list[SceneCue] = field(default_factory=list)
    description: str = ""
    loop: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "loop": self.loop,
            "cues": [c.to_dict() for c in self.cues],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SceneList':
        sl = cls(
            name=data["name"],
            description=data.get("description", ""),
            loop=data.get("loop", False),
        )
        sl.cues = [SceneCue.from_dict(c) for c in data.get("cues", [])]
        return sl


class SceneEngine:
    """
    Scene automation engine.

    Manages scene lists, playback, and automation.
    """

    def __init__(self, brain: Brain, data_dir: Optional[Path] = None):
        self.brain = brain
        self.data_dir = data_dir or Path.home() / ".flow8-llm"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.scene_lists: dict[str, SceneList] = {}
        self._current_list: Optional[SceneList] = None
        self._current_cue_idx: int = -1
        self._playing: bool = False
        self._play_thread: Optional[threading.Thread] = None

        self._load_scene_lists()

        # Register built-in scene templates
        self._register_templates()

    def _register_templates(self):
        """Register built-in scene list templates."""
        # Live band template
        self.scene_lists["live_band"] = SceneList(
            name="live_band",
            description="Standard live band show",
            cues=[
                SceneCue("Soundcheck", "soundcheck", 0,
                         "Initial soundcheck levels"),
                SceneCue("Walk-in", "walk_in_music", 5,
                         "Background music before show"),
                SceneCue("MC Intro", "mc_intro", 0,
                         "MC introduction - vocals up"),
                SceneCue("Song 1", "song_1", 0,
                         "First song"),
                SceneCue("Song 2", "song_2", 0,
                         "Second song"),
                SceneCue("Break", "break_music", 0,
                         "Intermission"),
                SceneCue("Song 3", "song_3", 0,
                         "After break"),
                SceneCue("Encore", "encore", 0,
                         "Encore"),
                SceneCue("Walk-out", "walk_out", 0,
                         "End of show"),
            ],
        )

        # Podcast template
        self.scene_lists["podcast"] = SceneList(
            name="podcast",
            description="Multi-host podcast recording",
            cues=[
                SceneCue("Intro", "podcast_intro", 0,
                         "Podcast intro music"),
                SceneCue("Main", "podcast_main", 0,
                         "Main discussion"),
                SceneCue("Break", "podcast_break", 0,
                         "Ad break / intermission"),
                SceneCue("Outro", "podcast_outro", 0,
                         "Outro and sign-off"),
            ],
        )

    def _load_scene_lists(self):
        """Load scene lists from disk."""
        scene_file = self.data_dir / "scene_lists.json"
        if scene_file.exists():
            try:
                data = json.loads(scene_file.read_text())
                for name, sl_data in data.items():
                    self.scene_lists[name] = SceneList.from_dict(sl_data)
            except Exception:
                pass

    def save_scene_lists(self):
        """Save scene lists to disk."""
        scene_file = self.data_dir / "scene_lists.json"
        data = {name: sl.to_dict() for name, sl in self.scene_lists.items()}
        scene_file.write_text(json.dumps(data, indent=2))

    # ── Scene List Management ──

    def create_scene_list(self, name: str, description: str = "") -> SceneList:
        """Create a new scene list."""
        sl = SceneList(name=name, description=description)
        self.scene_lists[name] = sl
        self.save_scene_lists()
        return sl

    def add_cue(self, scene_list_name: str, cue: SceneCue) -> bool:
        """Add a cue to a scene list."""
        sl = self.scene_lists.get(scene_list_name)
        if not sl:
            return False
        sl.cues.append(cue)
        self.save_scene_lists()
        return True

    def list_scene_lists(self) -> list[dict]:
        """List all scene lists."""
        return [
            {"name": sl.name, "description": sl.description,
             "cues": len(sl.cues)}
            for sl in self.scene_lists.values()
        ]

    def get_cues(self, scene_list_name: str) -> list[dict]:
        """Get all cues in a scene list."""
        sl = self.scene_lists.get(scene_list_name)
        if not sl:
            return []
        return [
            {"index": i, "name": c.name, "snapshot": c.snapshot_name,
             "delay": c.delay_sec, "description": c.description}
            for i, c in enumerate(sl.cues)
        ]

    # ── Playback ──

    def load(self, scene_list_name: str) -> bool:
        """Load a scene list for playback."""
        sl = self.scene_lists.get(scene_list_name)
        if not sl:
            return False
        self._current_list = sl
        self._current_cue_idx = -1
        return True

    def go(self) -> bool:
        """Advance to next cue (like pressing 'GO' on a lighting console)."""
        if not self._current_list:
            return False

        next_idx = self._current_cue_idx + 1
        if next_idx >= len(self._current_list.cues):
            if self._current_list.loop:
                next_idx = 0
            else:
                return False

        self._current_cue_idx = next_idx
        cue = self._current_list.cues[next_idx]

        # Recall snapshot
        batch = self.brain.state.recall_snapshot(cue.snapshot_name)
        if batch:
            self.brain.midi.send_batch(batch)

        # Execute additional actions
        if cue.actions:
            self.brain.execute_many(cue.actions)

        return True

    def back(self) -> bool:
        """Go back to previous cue."""
        if not self._current_list or self._current_cue_idx <= 0:
            return False

        self._current_cue_idx -= 1
        cue = self._current_list.cues[self._current_cue_idx]

        batch = self.brain.state.recall_snapshot(cue.snapshot_name)
        if batch:
            self.brain.midi.send_batch(batch)
        return True

    def jump_to(self, cue_index: int) -> bool:
        """Jump to a specific cue."""
        if not self._current_list:
            return False
        if cue_index < 0 or cue_index >= len(self._current_list.cues):
            return False

        self._current_cue_idx = cue_index
        cue = self._current_list.cues[cue_index]

        batch = self.brain.state.recall_snapshot(cue.snapshot_name)
        if batch:
            self.brain.midi.send_batch(batch)
        return True

    @property
    def current_cue(self) -> Optional[SceneCue]:
        """Get current cue."""
        if self._current_list and 0 <= self._current_cue_idx < len(self._current_list.cues):
            return self._current_list.cues[self._current_cue_idx]
        return None

    @property
    def status(self) -> dict:
        """Get playback status."""
        return {
            "loaded": self._current_list.name if self._current_list else None,
            "cue_index": self._current_cue_idx,
            "cue_name": self.current_cue.name if self.current_cue else None,
            "total_cues": len(self._current_list.cues) if self._current_list else 0,
        }
