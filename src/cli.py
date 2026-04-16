"""
CLI Entry Point for Flow8-LLM.

Usage:
    flow8-llm                           # Interactive TUI dashboard
    flow8-llm "set channel 1 gain 25dB" # Single command
    flow8-llm --automix                 # Auto-mix mode
    flow8-llm --scene "live_band"       # Load and play scene
    flow8-llm --status                  # Show system status
    flow8-llm --dry-run                 # No MIDI hardware
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.midi import MidiDriver, MidiBus, CC, Convert, MidiCmd
from src.core.audio import AudioEngine
from src.core.llm import LLMEngine, LLMConfig, LLMProvider
from src.core.brain import Brain, BrainMode
from src.core.state import StateManager
from src.tui.dashboard import Dashboard, C


def print_banner():
    print(f"""
{C.BOLD}{C.CYAN}╔═══════════════════════════════════════════════╗
║          Flow8-LLM: AI Mixing Brain          ║
║     Behringer Flow 8 × LLM Controller        ║
╚═══════════════════════════════════════════════╝{C.RESET}
""")


def print_status(brain: Brain):
    """Print system status."""
    status = brain.status()
    print(f"\n{C.BOLD}System Status:{C.RESET}")
    print(f"  Mode:    {status['mode']}")
    print(f"  MIDI:    {'Connected' if status['midi']['connected'] else 'Disconnected'} ({status['midi']['port']})")
    print(f"  Audio:   {'Running' if status['audio']['running'] else 'Stopped'}")
    print(f"  LLM:     {'Connected' if status['llm']['connected'] else 'Disconnected'} ({status['llm']['model']})")
    print(f"  Presets: {status['state']['presets']}")
    print(f"  Snapshots: {status['state']['snapshots']}")
    print()


def print_help():
    """Print available commands."""
    print(f"""
{C.BOLD}Available Commands:{C.RESET}

  {C.CYAN}Mixing:{C.RESET}
    set channel <N> gain <dB>       Set preamp gain (-20 to +60)
    set channel <N> fader <dB>      Set fader level (-70 to +10)
    set channel <N> eq <band> <dB>  Set EQ (low/lowmid/highmid/high)
    set channel <N> pan <value>     Set pan (-1.0 L to +1.0 R)
    set channel <N> lowcut <Hz>     Set high-pass filter
    mute channel <N>                Mute channel
    unmute channel <N>              Unmute channel
    solo channel <N>                Solo channel

  {C.CYAN}Presets:{C.RESET}
    apply preset <name> to <N>      Apply preset to channel
    list presets                    Show available presets

  {C.CYAN}Snapshots:{C.RESET}
    save snapshot <name>            Save current state
    recall snapshot <name>          Restore saved state
    list snapshots                  Show saved snapshots

  {C.CYAN}Analysis:{C.RESET}
    analyze                         Show audio analysis
    explain                         Get LLM explanation of mix
    automix                         Start auto-mix mode
    stop automix                    Stop auto-mix

  {C.CYAN}Quick Actions:{C.RESET}
    gain staging                    Auto-set all gains
    fix feedback                    Detect and fix feedback
    reset channel <N>               Reset channel to defaults

  {C.CYAN}System:{C.RESET}
    status                          Show system status
    mode <manual|assisted|auto>     Change operating mode
    undo                            Undo last action
    help                            Show this help
    quit                            Exit
""")


def interactive_mode(brain: Brain):
    """Run interactive command loop."""
    print_banner()
    print_status(brain)
    print(f"{C.DIM}Type 'help' for available commands. 'quit' to exit.{C.RESET}\n")

    while True:
        try:
            user_input = input(f"{C.CYAN}>{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        # System commands
        if cmd in ("quit", "exit", "q"):
            print("Bye!")
            break

        elif cmd == "help":
            print_help()
            continue

        elif cmd == "status":
            print_status(brain)
            continue

        elif cmd == "mode manual":
            brain.set_mode(BrainMode.MANUAL)
            print("Mode: MANUAL")
            continue

        elif cmd == "mode assisted":
            brain.set_mode(BrainMode.ASSISTED)
            print("Mode: ASSISTED")
            continue

        elif cmd == "mode auto":
            brain.set_mode(BrainMode.AUTO)
            print("Mode: AUTO (auto-mix active)")
            continue

        elif cmd == "undo":
            batch = brain.state.undo()
            if batch:
                sent = brain.midi.send_batch(batch)
                print(f"Undone ({sent} commands)")
            else:
                print("Nothing to undo")
            continue

        elif cmd.startswith("save snapshot"):
            name = user_input[len("save snapshot"):].strip()
            if name:
                brain.state.save_snapshot(name)
                print(f"Snapshot '{name}' saved")
            else:
                print("Usage: save snapshot <name>")
            continue

        elif cmd.startswith("recall snapshot"):
            name = user_input[len("recall snapshot"):].strip()
            if name:
                batch = brain.state.recall_snapshot(name)
                if batch:
                    sent = brain.midi.send_batch(batch)
                    print(f"Snapshot '{name}' restored ({sent} commands)")
                else:
                    print(f"Snapshot '{name}' not found")
            continue

        elif cmd == "list snapshots":
            snaps = brain.state.list_snapshots()
            if snaps:
                for s in snaps:
                    print(f"  - {s['name']}: {s['description'] or '(no description)'}")
            else:
                print("  No snapshots saved")
            continue

        elif cmd == "list presets":
            presets = brain.state.list_presets()
            for p in presets:
                print(f"  - {p['name']} ({p['type']}): {p['description']}")
            continue

        elif cmd.startswith("apply preset"):
            parts = user_input.split()
            try:
                preset_name = parts[2]
                channel = int(parts[-1])
                batch = brain.state.apply_preset(preset_name, channel)
                if batch:
                    sent = brain.midi.send_batch(batch)
                    print(f"Preset '{preset_name}' applied to CH{channel} ({sent} commands)")
                else:
                    print(f"Preset '{preset_name}' not found")
            except (IndexError, ValueError):
                print("Usage: apply preset <name> to <channel>")
            continue

        elif cmd == "gain staging":
            result = brain.quick_gain_staging()
            print(f"Gain staging: {result.reasoning or 'Done'} ({result.actions_sent} commands)")
            continue

        elif cmd == "fix feedback":
            result = brain.quick_feedback_fix()
            print(f"Feedback fix: {result.reasoning or 'Done'} ({result.actions_sent} commands)")
            continue

        elif cmd.startswith("reset channel"):
            try:
                ch = int(cmd.split()[-1])
                result = brain.execute({"action": "reset_channel", "channel": ch})
                print(f"CH{ch} reset to defaults ({result.actions_sent} commands)")
            except (IndexError, ValueError):
                print("Usage: reset channel <N>")
            continue

        elif cmd == "automix":
            brain.set_mode(BrainMode.AUTO)
            print("Auto-mix started. Press Ctrl+C to stop.")
            try:
                while True:
                    result = brain.auto_mix_step()
                    if result and result.actions_sent > 0:
                        print(f"  Auto-adjust: {result.reasoning[:80]}...")
                    time.sleep(2)
            except KeyboardInterrupt:
                brain.set_mode(BrainMode.MANUAL)
                print("\nAuto-mix stopped.")
            continue

        elif cmd == "stop automix":
            brain.set_mode(BrainMode.MANUAL)
            print("Auto-mix stopped.")
            continue

        elif cmd == "explain":
            print(f"\n{C.BOLD}Analyzing mix...{C.RESET}")
            explanation = brain.explain_status()
            print(f"\n{explanation}\n")
            continue

        elif cmd == "analyze":
            if brain.audio.is_running:
                analysis = brain.audio.latest
                if analysis:
                    for ch_num, ch in analysis.channels.items():
                        if ch.has_signal:
                            print(f"  CH{ch_num}: {ch.rms_db:+.1f}dB RMS | "
                                  f"{ch.peak_db:+.1f}dB Peak | "
                                  f"Dominant: {ch.dominant_band}")
                else:
                    print("  No analysis data yet")
            else:
                print("  Audio capture not running")
            continue

        # Natural language command → LLM
        print(f"  {C.DIM}Processing...{C.RESET}")
        result = brain.process(user_input)

        if result.success:
            if result.reasoning:
                print(f"  {C.DIM}Reasoning: {result.reasoning[:100]}...{C.RESET}")
            print(f"  {C.GREEN}✓ {result.actions_sent} command(s) sent{C.RESET}")
            if result.confidence > 0:
                print(f"  {C.DIM}Confidence: {result.confidence:.0%}{C.RESET}")
        else:
            if result.error:
                print(f"  {C.RED}✗ Error: {result.error}{C.RESET}")
            else:
                print(f"  {C.YELLOW}⚠ No actions generated{C.RESET}")
                if result.reasoning:
                    print(f"  {C.DIM}{result.reasoning[:200]}{C.RESET}")

        print()


def main():
    parser = argparse.ArgumentParser(
        description="Flow8-LLM: AI Mixing Brain for Behringer Flow 8",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", nargs="*", help="Command to execute")
    parser.add_argument("--midi-port", "-p", help="MIDI port name (auto-detect)")
    parser.add_argument("--model", "-m", default="gemma4:31b-cloud", help="LLM model")
    parser.add_argument("--ollama", "-o", default="http://localhost:11434", help="Ollama URL")
    parser.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    parser.add_argument("--api-key", help="API key for cloud providers")
    parser.add_argument("--dry-run", "-d", action="store_true", help="No MIDI output")
    parser.add_argument("--no-audio", action="store_true", help="Disable audio capture")
    parser.add_argument("--automix", action="store_true", help="Start in auto-mix mode")
    parser.add_argument("--dashboard", action="store_true", help="Launch TUI dashboard")
    parser.add_argument("--scene", help="Load and play a scene")
    parser.add_argument("--status", action="store_true", help="Show status and exit")
    parser.add_argument("--data-dir", help="Data directory for snapshots/presets")

    args = parser.parse_args()

    # Initialize components
    print_banner()

    # MIDI
    print(f"  MIDI: ", end="", flush=True)
    try:
        midi = MidiDriver(port_name=args.midi_port, dry_run=args.dry_run)
        print(f"{C.GREEN}{'Dry run' if args.dry_run else midi.port_name}{C.RESET}")
    except Exception as e:
        print(f"{C.RED}Error: {e}{C.RESET}")
        print(f"  Continuing in dry-run mode...")
        midi = MidiDriver(dry_run=True)

    # LLM
    print(f"  LLM:  ", end="", flush=True)
    provider = LLMProvider.OLLAMA if args.provider == "ollama" else LLMProvider.OPENAI
    llm_config = LLMConfig(
        provider=provider,
        model=args.model,
        base_url=args.ollama,
        api_key=args.api_key or "",
    )
    llm = LLMEngine(llm_config)
    if llm.check():
        models = llm.list_models()
        print(f"{C.GREEN}Connected ({args.model}){C.RESET}")
        if models:
            print(f"         Models: {', '.join(models[:5])}")
    else:
        print(f"{C.YELLOW}Not connected{C.RESET}")

    # Audio
    print(f"  Audio: ", end="", flush=True)
    if args.no_audio:
        print(f"{C.DIM}Disabled{C.RESET}")
        audio = AudioEngine()
    else:
        try:
            audio = AudioEngine()
            devices = audio.list_devices()
            if devices:
                print(f"{C.GREEN}{len(devices)} device(s) available{C.RESET}")
            else:
                print(f"{C.YELLOW}No input devices (install sounddevice){C.RESET}")
        except Exception as e:
            print(f"{C.YELLOW}Unavailable: {e}{C.RESET}")
            audio = AudioEngine()

    # State
    data_dir = Path(args.data_dir) if args.data_dir else None
    state = StateManager(data_dir)

    # Brain
    brain = Brain(midi=midi, audio=audio, llm=llm, state=state)

    if args.status:
        print_status(brain)
        return

    if args.dashboard:
        dashboard = Dashboard(brain)
        dashboard.run()
        return

    if args.automix:
        brain.set_mode(BrainMode.AUTO)
        print(f"\n{C.GREEN}Auto-mix mode active. Press Ctrl+C to stop.{C.RESET}")
        try:
            while True:
                result = brain.auto_mix_step()
                if result and result.actions_sent > 0:
                    print(f"  Adjusted: {result.reasoning[:80]}...")
                time.sleep(3)
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    if args.scene:
        batch = brain.state.recall_snapshot(args.scene)
        if batch:
            sent = midi.send_batch(batch)
            print(f"Scene '{args.scene}' loaded ({sent} commands)")
        else:
            print(f"Scene '{args.scene}' not found")
        return

    if args.command:
        # Single command mode
        user_input = " ".join(args.command)
        result = brain.process(user_input)
        if result.success:
            print(f"  {C.GREEN}✓ {result.actions_sent} command(s) sent{C.RESET}")
        else:
            print(f"  {C.RED}✗ {result.error or 'Failed'}{C.RESET}")
        return

    # Default: interactive mode
    interactive_mode(brain)


if __name__ == "__main__":
    main()
