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
    print_status(brain)
    print(f"{C.DIM}Type 'help' for commands, '/' to list all commands, 'quit' to exit.{C.RESET}\n")

    ALL_COMMANDS = [
        # (category, command, description)
        ("MIX", "set channel <N> gain <dB>", "Set preamp gain (-20 to +60)"),
        ("MIX", "set channel <N> fader <dB>", "Set fader level (-70 to +10)"),
        ("MIX", "set channel <N> eq <band> <dB>", "Set EQ (low/lowmid/highmid/high)"),
        ("MIX", "set channel <N> pan <val>", "Set pan (-1.0 L to +1.0 R)"),
        ("MIX", "set channel <N> lowcut <Hz>", "Set high-pass filter"),
        ("MIX", "mute channel <N>", "Mute channel"),
        ("MIX", "unmute channel <N>", "Unmute channel"),
        ("MIX", "solo channel <N>", "Solo (PFL)"),
        ("MIX", "reset channel <N>", "Reset channel to defaults"),
        ("PRESET", "apply preset <name> to <N>", "Apply preset to channel"),
        ("PRESET", "list presets", "Show available presets"),
        ("SNAP", "save snapshot <name>", "Save current state"),
        ("SNAP", "recall snapshot <name>", "Restore saved state"),
        ("SNAP", "list snapshots", "Show saved snapshots"),
        ("AUDIO", "start audio", "Auto-detect Flow 8 and start capture"),
        ("AUDIO", "start audio <id>", "Start capture on device ID"),
        ("AUDIO", "stop audio", "Stop audio capture"),
        ("AUDIO", "list devices", "Show audio input devices"),
        ("AUDIO", "analyze", "Show audio analysis per channel"),
        ("AI", "explain", "LLM explains current mix"),
        ("AI", "automix", "Start auto-mix mode (LLM controls)"),
        ("AI", "stop automix", "Stop auto-mix mode"),
        ("AI", "<natural language>", "Any mixing command in plain text"),
        ("QUICK", "gain staging", "Auto-set all gains for headroom"),
        ("QUICK", "fix feedback", "Detect and fix feedback"),
        ("SYS", "status", "Show system status"),
        ("SYS", "undo", "Undo last action"),
        ("SYS", "help", "Show help"),
        ("SYS", "quit", "Exit"),
    ]

    def show_command_menu():
        """Show numbered command menu."""
        print(f"\n{C.BOLD}── All Commands ──────────────────────────────────────────────{C.RESET}")
        current_cat = ""
        num = 1
        for cat, cmd, desc in ALL_COMMANDS:
            if cat != current_cat:
                current_cat = cat
                print(f"\n  {C.CYAN}{C.BOLD}[{cat}]{C.RESET}")
            print(f"  {C.GREEN}{num:>3}{C.RESET}. {cmd:<35} {C.DIM}{desc}{C.RESET}")
            num += 1
        print(f"\n  {C.DIM}Type number to run, or type command directly{C.RESET}")
        print(f"  {C.DIM}Example: 1 → set channel 1 gain 30dB{C.RESET}\n")

    while True:
        try:
            user_input = input(f"{C.CYAN}>{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        # Handle / prefix - show menu or execute /command
        if user_input == "/":
            show_command_menu()
            continue

        if user_input.startswith("/"):
            # /command → strip / and execute as normal
            user_input = user_input[1:].strip()

        # Handle numbered selection from menu
        if user_input.isdigit():
            idx = int(user_input) - 1
            if 0 <= idx < len(ALL_COMMANDS):
                _, cmd_template, desc = ALL_COMMANDS[idx]
                # If template has <>, prompt user for values
                if "<" in cmd_template:
                    print(f"  {C.YELLOW}Template: {cmd_template}{C.RESET}")
                    fill = input(f"  Fill in: ").strip()
                    if fill:
                        # Replace template placeholders with user input
                        parts_template = cmd_template.split()
                        parts_fill = fill.split()
                        result_parts = []
                        fill_idx = 0
                        for p in parts_template:
                            if p.startswith("<") and p.endswith(">"):
                                if fill_idx < len(parts_fill):
                                    result_parts.append(parts_fill[fill_idx])
                                    fill_idx += 1
                            else:
                                result_parts.append(p)
                        user_input = " ".join(result_parts)
                    else:
                        print("  Cancelled.")
                        continue
                else:
                    user_input = cmd_template
                print(f"  {C.DIM}→ {user_input}{C.RESET}")
            else:
                print(f"  {C.RED}Invalid number. '/' to see menu.{C.RESET}")
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
                print("  Audio capture not running. Use 'start audio' first.")
            continue

        elif cmd == "start audio":
            try:
                import sounddevice
                # List input devices
                devices = brain.audio.list_devices()
                print(f"  Available input devices:")
                for d in devices:
                    print(f"    [{d['id']}] {d['name']} ({d['inputs']}ch)")

                # Try to find Flow 8
                flow_device = None
                flow_channels = 2
                for d in devices:
                    if "flow" in d["name"].lower() or "behringer" in d["name"].lower():
                        flow_device = d["id"]
                        flow_channels = d["inputs"]
                        break

                if flow_device is not None:
                    print(f"\n  Auto-selected device: [{flow_device}] ({flow_channels}ch)")
                    brain.audio = AudioEngine(device=flow_device, channels=flow_channels)
                    brain.audio.start_capture()
                    print(f"  {C.GREEN}Audio capture started!{C.RESET}")
                else:
                    print(f"\n  {C.YELLOW}Flow 8 not found in audio devices.{C.RESET}")
                    print(f"  Use: start audio <device_id>")
            except ImportError:
                print(f"  {C.RED}sounddevice not installed.{C.RESET}")
                print(f"  Run: pip install sounddevice")
            except Exception as e:
                print(f"  {C.RED}Error: {e}{C.RESET}")
            continue

        elif cmd.startswith("start audio "):
            try:
                device_id = int(cmd.split()[-1])
                import sounddevice
                # Find channel count for this device
                devices = brain.audio.list_devices()
                channels = 2
                for d in devices:
                    if d["id"] == device_id:
                        channels = d["inputs"]
                        break
                brain.audio = AudioEngine(device=device_id, channels=channels)
                brain.audio.start_capture()
                print(f"  {C.GREEN}Audio capture started on device {device_id} ({channels}ch)!{C.RESET}")
            except Exception as e:
                print(f"  {C.RED}Error: {e}{C.RESET}")
            continue

        elif cmd == "stop audio":
            if brain.audio.is_running:
                brain.audio.stop_capture()
                print(f"  Audio capture stopped.")
            else:
                print("  Audio not running.")
            continue

        elif cmd == "list devices":
            try:
                import sounddevice
                devices = brain.audio.list_devices()
                print(f"  Input devices:")
                for d in devices:
                    print(f"    [{d['id']}] {d['name']} ({d['inputs']}ch)")
            except ImportError:
                print(f"  {C.RED}sounddevice not installed. Run: pip install sounddevice{C.RESET}")
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

    if not args.command and not args.status and not args.dashboard and not args.automix and not args.scene:
        interactive_mode(brain)


if __name__ == "__main__":
    main()
