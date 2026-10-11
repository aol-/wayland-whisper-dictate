#!/usr/bin/env python3
import os
import sys
import time
import signal
import subprocess
from pathlib import Path

# Paths
ENV_PYTHON = Path.home() / ".local/share/dictate-env/bin/python3"
PID_FILE = Path("/tmp/dictate.pid")
AUDIO_FILE = Path("/tmp/dictate_recording.wav")
SOUND_DIR = Path("/usr/share/sounds/freedesktop/stereo")

def play_sound(sound_name):
    sound_path = SOUND_DIR / f"{sound_name}.oga"
    if sound_path.exists():
        subprocess.Popen(
            ["pw-play", str(sound_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

def notify(summary, body="", icon="microphone", expire_ms=2000):
    subprocess.Popen(
        ["notify-send", "-a", "Dictation", "-i", icon, "-t", str(expire_ms), summary, body],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def is_recording_alive():
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
        return False

def start_recording():
    if AUDIO_FILE.exists():
        AUDIO_FILE.unlink(missing_ok=True)

    # Launch pw-record in 16kHz mono 16-bit WAV
    proc = subprocess.Popen(
        ["pw-record", "--rate", "16000", "--channels", "1", str(AUDIO_FILE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    PID_FILE.write_text(str(proc.pid))
    play_sound("message")
    notify("🎙️ Listening...", "Speak now. Press hotkey again to finish.", expire_ms=2500)
    print("Recording started. PID:", proc.pid)

def stop_and_transcribe():
    pid = None
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
        except ValueError:
            pass
        PID_FILE.unlink(missing_ok=True)

    if pid:
        try:
            os.kill(pid, signal.SIGINT)
            # Wait up to 1 second for pw-record to exit cleanly
            for _ in range(20):
                time.sleep(0.05)
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
        except OSError:
            pass

    if not AUDIO_FILE.exists() or AUDIO_FILE.stat().st_size < 3000:
        AUDIO_FILE.unlink(missing_ok=True)
        notify("Dictation Stopped", "No audio recorded.", icon="dialog-warning", expire_ms=1500)
        print("No audio recorded.")
        return

    notify("⏳ Transcribing...", "Processing speech on GPU...", icon="emblem-synchronizing", expire_ms=1500)
    t0 = time.time()

    # Import faster_whisper
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        notify("Dictation Error", "faster-whisper not found in environment.", icon="dialog-error")
        return

    try:
        model = WhisperModel("base.en", device="cuda", compute_type="float16")
        segments, info = model.transcribe(
            str(AUDIO_FILE),
            beam_size=5,
            language="en",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400)
        )
        text = " ".join([seg.text.strip() for seg in segments]).strip()
    except Exception as e:
        print("Transcription error:", e)
        notify("Transcription Error", str(e), icon="dialog-error")
        AUDIO_FILE.unlink(missing_ok=True)
        return

    duration = time.time() - t0
    AUDIO_FILE.unlink(missing_ok=True)

    if not text:
        notify("Dictation", "No speech detected.", icon="dialog-information", expire_ms=1500)
        print("No speech detected.")
        return

    print(f"Transcribed ({duration:.2f}s): {text}")

    # Copy to clipboard via wl-copy
    try:
        subprocess.run(["wl-copy", text], check=True)
    except Exception as e:
        print("Clipboard copy error:", e)
        notify("Clipboard Error", str(e), icon="dialog-error")
        return

    # Paste text via evdev /dev/uinput
    try:
        import evdev
        from evdev import ecodes

        cap = {ecodes.EV_KEY: [ecodes.KEY_LEFTCTRL, ecodes.KEY_V]}
        ui = evdev.UInput(cap, name="DictatePasteHelper")
        time.sleep(0.05)
        # Ctrl + V
        ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1)
        ui.write(ecodes.EV_KEY, ecodes.KEY_V, 1)
        ui.syn()
        time.sleep(0.05)
        ui.write(ecodes.EV_KEY, ecodes.KEY_V, 0)
        ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0)
        ui.syn()
        ui.close()
    except Exception as e:
        print("uinput paste error:", e)

    play_sound("complete")
    preview = text if len(text) <= 50 else text[:47] + "..."
    notify("✅ Dictation Inserted", preview, icon="dialog-ok", expire_ms=2000)

def cancel_recording():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGINT)
        except (ValueError, OSError):
            pass
        PID_FILE.unlink(missing_ok=True)
    if AUDIO_FILE.exists():
        AUDIO_FILE.unlink(missing_ok=True)
    notify("Dictation Cancelled", icon="process-stop", expire_ms=1500)
    print("Dictation cancelled.")

def toggle():
    if is_recording_alive():
        stop_and_transcribe()
    else:
        start_recording()

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    if action == "start":
        start_recording()
    elif action == "stop":
        stop_and_transcribe()
    elif action == "cancel":
        cancel_recording()
    else:
        toggle()
