from main import VentilatorWorker


class TestWaveformLogic:
    """
    Tests the logic for extracting pressure/flow data and breath markers.
    """

    def test_clean_packet(self):
        buffer, events = VentilatorWorker.parse_incoming_chunk("", "10.5,20.0\n")
        assert buffer == ""
        assert events == [('DATA', 20.0, 10.5)]

    def test_fragmentation(self):
        # Simulate data arriving in two pieces
        buffer, events = VentilatorWorker.parse_incoming_chunk("", "10.5,")
        assert buffer == "10.5,"
        assert len(events) == 0

        buffer, events = VentilatorWorker.parse_incoming_chunk(buffer, "20.0\n")
        assert buffer == ""
        assert events == [('DATA', 20.0, 10.5)]

    def test_breath_marker(self):
        _buffer, events = VentilatorWorker.parse_incoming_chunk("", "BS, S:123,\n")
        assert events == [('BREATH', '123')]

    def test_buffer_overflow(self):
        # Send garbage larger than 8192 bytes
        garbage = "A" * 9000
        buffer, events = VentilatorWorker.parse_incoming_chunk("", garbage)
        assert buffer == ""  # Should reset
        assert len(events) == 0


class TestSettingsLogic:
    """
    Tests the logic for parsing PB980 settings CSV strings.
    """
    # Synthetic PB980 MISCF payload for unit testing.
    # Format matches PB980 serial protocol (see PB980 Owner's Manual).
    SIMULATED_PB980_PAYLOAD = (
        b'MISCF,1225,169 ,\x0212:00 ,980 SIM000000001  ,JAN 01 2026 ,INVASIVE ,A/C   ,VC    ,'
        b'      ,V-Trig,10.0  ,0.400 ,44.0  ,21    ,      ,0.0   ,0.0   ,60    ,      ,10.0  ,'
        b'      ,100   ,15    ,0.74  ,      ,PC    ,1.00  ,7.11  ,      ,RAMP  ,OFF   ,100   ,'
        b'      ,48.500,0.100 ,1370  ,210   ,1370  ,210   ,OFF   ,      ,3.5   ,2.0   ,      ,'
        b'      ,      ,      ,      ,         ,      ,      ,HME               ,      ,Enabled  ,40    ,'
        b'      ,      ,      ,50.0  ,      ,      ,      ,      ,      ,ADULT    ,      ,      ,14.0  ,'
        b'10.0  ,0.320 ,3.200 ,14.0  ,2.4   ,5.00  ,1:5.00,22    ,      ,      ,      ,      ,      ,'
        b'      ,      ,      ,      ,0.3   ,      ,      ,0.0   ,0.0   ,0.0   ,0.0   ,0.0   ,      ,'
        b'26.0  ,9.9   ,      ,39.0  ,0.0   ,OFF   ,0.0   ,0.0   ,0.000 ,OFF   ,NORMAL,NORMAL,NORMAL,'
        b'NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,'
        b'NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,'
        b'NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,'
        b'NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,      ,      ,ON    ,'
        b'19    ,0.3   ,57    ,NORMAL,      ,      ,      ,      ,      ,      ,      ,      ,      ,'
        b'      ,      ,      ,      ,\x03\r'
    ).decode('latin-1')

    def test_real_payload_parsing(self):
        """
        Verify that the specific binary payload from the PB980 parses correctly.
        """
        buffer, results = VentilatorWorker.parse_settings_chunk("", self.SIMULATED_PB980_PAYLOAD)

        # Buffer should be clear (because payload ends in \r)
        assert buffer == ""

        # Should extract exactly one settings update
        assert len(results) == 1

        # Verify the formatting "Mode: {mandatory} {spont} {mode}"
        # Parts[7] = "A/C", Parts[8] = "VC", Parts[9] = ""
        # Expected: "Mode: VC A/C" (single space due to replace cleanup) or "Mode: VC  A/C" if cleanup omitted
        # The refactored code adds a .replace("  ", " ") cleanup for cleaner UI
        assert results[0] == "Mode: VC A/C"

    def test_settings_fragmentation(self):
        """
        Ensure large CSV lines can arrive in chunks.
        """
        # Split the real payload in half
        mid_point = len(self.SIMULATED_PB980_PAYLOAD) // 2
        chunk1 = self.SIMULATED_PB980_PAYLOAD[:mid_point]
        chunk2 = self.SIMULATED_PB980_PAYLOAD[mid_point:]

        # Feed chunk 1
        buffer, results = VentilatorWorker.parse_settings_chunk("", chunk1)
        assert buffer == chunk1  # Stored for later
        assert len(results) == 0  # Not complete yet

        # Feed chunk 2
        buffer, results = VentilatorWorker.parse_settings_chunk(buffer, chunk2)
        assert buffer == ""
        assert len(results) == 1
        assert results[0] == "Mode: VC A/C"

    def test_short_packet_ignored(self):
        """
        If a line is too short (<173 fields), it should be ignored safely.
        """
        short_line = "MISCF,1,2,3\r"
        buffer, results = VentilatorWorker.parse_settings_chunk("", short_line)
        assert buffer == ""
        assert len(results) == 0  # Ignored, no crash
