import time
import serial
import serial.tools.list_ports
import sys
from pathlib import Path

# Configuration
PORT = "/dev/cu.usbserial-A50285BI"  # CHANGE THIS to your simulator PC's output port
BAUD_RATE = 38400
LOG_FILE = "logged_waveforms.txt"
FREQUENCY_HZ = 50
INTERVAL = 1.0 / FREQUENCY_HZ  # 0.02s (20ms)


def get_clean_lines(filepath):
    """Reads the file and returns a list of non-empty lines."""
    p = Path(filepath)
    if not p.exists():
        print(f"Error: {filepath} not found.")
        sys.exit(1)

    with open(p, 'r', encoding='utf-8') as f:
        # Keep the \n for writing, but strip extra whitespace
        lines = [line for line in f if line.strip()]
    return lines


def is_control_line(line):
    """Returns True if the line is a BS (Start) or BE (End) marker."""
    clean = line.strip()
    return clean.startswith("BS") or clean.startswith("BE")


def main():
    print(f"--- PB980 Simulator (Event-Driven) ---")
    print(f"Data Interval: {INTERVAL * 1000:.1f}ms | BS/BE: Instant")

    # 1. Load Data
    lines = get_clean_lines(LOG_FILE)
    print(f"Loaded {len(lines)} lines of waveform data.")

    # 2. Open Serial Port
    try:
        ser = serial.Serial(PORT, BAUD_RATE, timeout=0)
        print(f"Opened {PORT} successfully.")
    except Exception as e:
        print(f"Failed to open port {PORT}: {e}")
        return

    # 3. Metronome Loop
    index = 0
    total_lines = len(lines)

    # Initialize metronome reference time
    # We want the FIRST data point to fire immediately, then pace subsequent ones.
    next_wake_time = time.monotonic()

    try:
        while True:
            line_to_send = lines[index]

            # Logic:
            # - If Control (BS/BE): Send immediately (0 cost).
            # - If Data: Wait for metronome, Send, Increment metronome cost (20ms).

            if is_control_line(line_to_send):
                # Send immediately (Control line)
                ser.write(line_to_send.encode('latin-1'))
                ser.flush()
                # Do NOT increment next_wake_time
                # Do NOT sleep
            else:
                # Wait for the scheduled time (Data line)
                sleep_duration = next_wake_time - time.monotonic()
                if sleep_duration > 0:
                    time.sleep(sleep_duration)
                else:
                    # If we fell behind, catch up but don't spiral
                    # (Optional: Reset base if lag is huge, but strict pacing is better here)
                    if sleep_duration < -1.0:
                        next_wake_time = time.monotonic()

                # Send Data
                ser.write(line_to_send.encode('latin-1'))
                ser.flush()

                # Increment cost for NEXT data point
                next_wake_time += INTERVAL

            # Loop Index
            index = (index + 1) % total_lines

    except KeyboardInterrupt:
        print("\nSimulation stopped by user.")
    except Exception as e:
        print(f"\nRuntime Error: {e}")
    finally:
        ser.close()
        print("Port closed.")


if __name__ == "__main__":
    main()