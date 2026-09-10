import unittest

from m3.midi import ActiveNotes, BleMidiParser, MidiEvent


def event(status, *data):
    return MidiEvent(status, data)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = BleMidiParser()

    def decode(self, hex_bytes):
        return self.parser.parse(bytes.fromhex(hex_bytes))

    def test_hardware_captures(self):
        self.assertEqual(self.decode('a3 c9 90 2d 29'), [event(0x90, 45, 41)])
        self.assertEqual(self.decode('a3 d9 80 2d 29 d9 90 2f 29'),
                         [event(0x80, 45, 41), event(0x90, 47, 41)])
        self.assertEqual(self.decode('a4 bd 80 39 29 bd 90 3c 29'),
                         [event(0x80, 57, 41), event(0x90, 60, 41)])

    def test_running_with_and_without_timestamp(self):
        self.assertEqual(self.decode('80 80 90 3c 50 3e 40 81 40 00'),
                         [event(0x90, 60, 80), event(0x90, 62, 64), event(0x90, 64, 0)])

    def test_running_cleared_at_packet_boundary(self):
        self.decode('80 80 90 3c 50')
        with self.assertRaises(ValueError):
            self.decode('80 81 3c 00')

    def test_all_channel_message_lengths(self):
        self.assertEqual(self.decode('80 80 b1 01 40 80 c1 05 06 80 d1 20 '
                                     '80 a1 3c 22 80 e1 00 40 80 91 3c 50'),
                         [event(0xB1, 1, 64), event(0xC1, 5), event(0xC1, 6),
                          event(0xD1, 32), event(0xA1, 60, 34), event(0xE1, 0, 64),
                          event(0x91, 60, 80)])

    def test_system_messages_preserve_ble_running_status(self):
        for system in ('f8', 'fa', 'fb', 'fc', 'fe', 'f1 01', 'f2 01 02', 'f3 01', 'f6'):
            with self.subTest(system=system):
                decoded = self.decode('80 80 90 3c 50 81 ' + system + ' 82 3c 00')
                self.assertEqual(decoded[-1], event(0x90, 60, 0))

    def test_realtime_byte_values_as_timestamps(self):
        self.assertEqual(self.decode('bf ff 90 3c 50 80 80 3c 00'),
                         [event(0x90, 60, 80), event(0x80, 60, 0)])

    def test_sysex_single_packet(self):
        self.assertEqual(self.decode('80 80 f0 7e 00 09 01 81 f7 82 90 3c 50'),
                         [event(0x90, 60, 80)])

    def test_sysex_multiple_packets_and_realtime(self):
        self.assertEqual(self.decode('80 80 f0 7e 00'), [])
        self.assertEqual(self.decode('80 01 02 81 f8 03 04'), [event(0xF8)])
        self.assertEqual(self.decode('80 05 82 f7 83 90 3c 50'), [event(0x90, 60, 80)])
        self.assertFalse(self.parser.in_sysex)

    def test_sysex_end_only_packet(self):
        self.decode('80 80 f0')
        self.assertEqual(self.decode('80 81 f7'), [])

    def test_malformed_and_recovery(self):
        for data in ('', '80', '40 80 90 3c 40', 'c0 80 90 3c 40',
                     '80 80', '80 01', '80 80 90 3c', '80 80 90 3c 81',
                     '80 80 f7', '80 80 f4', '80 80 f0 01 81 90 3c 50',
                     '80 80 90 3c 50 81 f8 3d 50'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.decode(data)
            self.assertEqual(self.decode('80 80 90 3c 50'), [event(0x90, 60, 80)])


class ActiveNotesTests(unittest.TestCase):
    def setUp(self):
        self.notes = ActiveNotes()

    def test_overlap_and_velocity_zero(self):
        self.notes.apply(event(0x90, 60, 80))
        self.notes.apply(event(0x90, 64, 80))
        self.notes.apply(event(0x80, 60, 10))
        self.assertEqual(self.notes.notes, {(0, 64)})
        log = self.notes.apply(event(0x90, 64, 0))
        self.assertIn('NOTE OFF ch=1 note=64 vel=0', log)
        self.assertFalse(self.notes.notes)

    def test_duplicate_on_and_unmatched_off(self):
        self.notes.apply(event(0x90, 60, 80))
        self.notes.apply(event(0x90, 60, 80))
        self.notes.apply(event(0x80, 61, 0))
        self.assertEqual(len(self.notes.notes), 1)
        self.notes.apply(event(0x80, 60, 0))
        self.assertFalse(self.notes.notes)

    def test_channels_independent_and_all_off(self):
        for cc in (120, 123, 124, 125, 126, 127):
            self.notes.apply(event(0x90, 60, 80))
            self.notes.apply(event(0x91, 60, 80))
            self.notes.apply(event(0xB0, cc, 0))
            self.assertEqual(self.notes.notes, {(1, 60)})
            self.notes.apply(event(0x81, 60, 0))
            self.assertFalse(self.notes.notes)

    def test_cc_program_sysex_do_not_create_notes(self):
        self.notes.apply(event(0x90, 60, 80))
        self.notes.apply(event(0xB0, 121, 0))
        self.notes.apply(event(0xB0, 64, 127))
        self.notes.apply(event(0xC0, 20))
        self.assertEqual(self.notes.notes, {(0, 60)})
        self.notes.apply(event(0xFF))
        self.assertFalse(self.notes.notes)


if __name__ == '__main__':
    unittest.main()
