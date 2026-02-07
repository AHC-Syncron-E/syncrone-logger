import serial
import time
import re

# --- CONFIGURATION ---
PORT = "COM3"  # The receiving Windows port
BAUD_RATE = 38400


def parse_sequence_number(header_line):
    """Extracts 123 from 'BS, S:123,'"""
    match = re.search(r"S:(\d+)", header_line)
    if match:
        return int(match.group(1))
    return None


def main():
    print(f"--- PB980 Receiver/Validator listening on {PORT} ---")

    try:
        # timeout=1 allows the loop to check for Ctrl+C periodically
        ser = serial.Serial(PORT, BAUD_RATE, timeout=1)
        print(f"Connected to {PORT}.")
    except Exception as e:
        print(f"Could not open {PORT}: {e}")
        return

    buffer = ""
    last_seq = None
    last_arrival_time = time.monotonic()

    try:
        while True:
            # Read incoming data.
            # In a real app, you might use a thread, but for validation
            # we just need to read fast enough to keep the buffer clear.
            if ser.in_waiting > 0:
                # Decode and append to buffer
                try:
                    chunk = ser.read(ser.in_waiting).decode('latin-1')
                    buffer += chunk
                except UnicodeDecodeError:
                    print("Decode Error: non-latin-1 byte received")
                    continue

                # Process buffer: Extract complete BS...BE blocks
                while "BE" in buffer:
                    # Find the end of the first complete breath
                    be_index = buffer.find("BE")

                    # Extract the full breath text (including BE and newline)
                    # We look for the newline after BE to be clean
                    end_of_breath = buffer.find('\n', be_index)
                    if end_of_breath == -1:
                        # Wait for the newline to arrive
                        break

                    raw_breath = buffer[:end_of_breath + 1]
                    buffer = buffer[end_of_breath + 1:]  # Remove from buffer

                    # --- ANALYZE BREATH ---
                    process_breath(raw_breath, last_seq, last_arrival_time)

                    # Update state for next loop
                    bs_line = raw_breath.strip().split('\n')[0]
                    current_seq = parse_sequence_number(bs_line)
                    if current_seq is not None:
                        last_seq = current_seq

                    last_arrival_time = time.monotonic()

    except KeyboardInterrupt:
        print("\nReceiver stopped.")
    finally:
        if ser.is_open: ser.close()


def process_breath(raw_text, last_seq, last_arrival_time):
    lines = raw_text.strip().split('\n')
    header = lines[0].strip()

    # Validation 1: Structure
    if not header.startswith("BS") or not lines[-1].strip().startswith("BE"):
        print(f"[ERROR] Malformed Breath! Start/End markers missing.\nRaw: {raw_text[:50]}...")
        return

    # Validation 2: Sequence Logic
    curr_seq = parse_sequence_number(header)
    seq_status = "OK"
    if last_seq is not None and curr_seq is not None:
        diff = curr_seq - last_seq
        # Note: If simulator loops file, curr_seq will drop. Handle that.
        if diff != 1 and diff > -1000:
            seq_status = f"MISSING {diff - 1} BREATHS"
        elif diff < 0:
            seq_status = "FILE LOOP DETECTED"

    # Validation 3: Timing
    # Time since we finished processing the LAST breath
    dt = time.monotonic() - last_arrival_time

    # Count data lines (exclude BS/BE)
    data_lines = len(lines) - 2

    print(f"Rx: S:{curr_seq:<6} | Lines: {data_lines:<4} | Inter-Breath Δt: {dt:.3f}s | Seq: {seq_status}")


if __name__ == "__main__":
    main()