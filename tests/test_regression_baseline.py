from main import VentilatorWorker


class TestLegacyParserBehavior:
    """
    These tests verify the behavior of the EXISTING parser logic in main.py
    via the static VentilatorWorker.parse_incoming_chunk() method.
    """

    def test_golden_master_parsing(self):
        """
        Feeds a complex stream of data into the parser and ensures
        the exact correct events come out.
        """
        # 1. Define the complex input
        input_sequence = [
            "10.5",       # Fragment 1
            ",20.1",      # Fragment 2
            "\n",         # Completes Line 1 -> Expect DATA(20.1, 10.5)
            "BS, S:500,\n",  # Breath Marker -> Expect BREATH(500)
            "GARBAGE\n",  # Junk -> Expect Nothing
            "30.0,40.0\n"  # Clean Line 2 -> Expect DATA(40.0, 30.0)
        ]

        # 2. Feed chunks through the static parser
        buffer = ""
        all_events = []

        for chunk in input_sequence:
            buffer, events = VentilatorWorker.parse_incoming_chunk(buffer, chunk)
            all_events.extend(events)

        # 3. Separate event types
        data_events = [e for e in all_events if e[0] == 'DATA']
        breath_events = [e for e in all_events if e[0] == 'BREATH']

        # 4. Assertions (The "Golden" behavior)

        # We expect exactly 2 data events and 1 breath event
        assert len(data_events) == 2
        assert len(breath_events) == 1

        # Check DATA 1: Pressure=20.1, Flow=10.5
        assert data_events[0] == ('DATA', 20.1, 10.5)

        # Check BREATH: Sequence "500"
        assert breath_events[0] == ('BREATH', '500')

        # Check DATA 2: Pressure=40.0, Flow=30.0
        assert data_events[1] == ('DATA', 40.0, 30.0)

    def test_buffer_overflow_regression(self):
        """
        Verify the existing safety valve logic works.
        """
        # Fill buffer past limit (8192)
        huge_chunk = "A" * 8200

        # Static method returns empty buffer and no events on overflow
        buffer, events = VentilatorWorker.parse_incoming_chunk("", huge_chunk)

        # Buffer should be reset to empty string
        assert buffer == ""
        assert events == []
