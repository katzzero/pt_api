"""Exact PTX rate selection and independent rational timecode oracles."""
from fractions import Fraction
import os
import tempfile
import unittest

from pt_api import PTBlock, ProToolsSession, TimecodeEngine
from tests.test_loading import frame_rate_payload, generic_block, minimal_ptx
from tests.test_move import make_session as move_session, event_info
from tests.test_split import make_session as split_session, timeline_info


# Independent expected table, not imported from the implementation under test.
RATES = (
    (0x01, Fraction(24), 24, 0), (0x02, Fraction(25), 25, 0),
    (0x03, Fraction(30000, 1001), 30, 0),
    (0x05, Fraction(30000, 1001), 30, 2),
    (0x07, Fraction(30), 30, 0), (0x08, Fraction(30), 30, 2),
    (0x09, Fraction(24000, 1001), 24, 0),
    (0x0A, Fraction(50), 50, 0),
    (0x0B, Fraction(60000, 1001), 60, 0),
    (0x0C, Fraction(60000, 1001), 60, 4),
    (0x0D, Fraction(60), 60, 0), (0x0E, Fraction(60), 60, 4),
    (0x0F, Fraction(48000, 1001), 48, 0),
    (0x10, Fraction(48), 48, 0), (0x11, Fraction(100), 100, 0),
    (0x12, Fraction(120000, 1001), 120, 0),
    (0x13, Fraction(120000, 1001), 120, 8),
    (0x14, Fraction(120), 120, 0), (0x15, Fraction(120), 120, 8),
)


def nearest(value):
    value += Fraction(1, 2)
    return value.numerator // value.denominator


def expected_samples(rate, nominal, drop, sample_rate, position):
    hh, mm, ss, ff = position
    minutes = hh * 60 + mm
    frames = (minutes * 60 + ss) * nominal + ff
    frames -= drop * (minutes - minutes // 10)
    return nearest(Fraction(frames * sample_rate) / rate)


class ExactRateTests(unittest.TestCase):
    def test_all_nineteen_mappings_and_unknown_enums(self):
        for enum, rate, nominal, drop in RATES:
            with self.subTest(enum=hex(enum)):
                engine = TimecodeEngine(48000, enum)
                self.assertEqual(engine.get_frame_rate(), (float(rate), bool(drop)))
                self.assertEqual(engine._nominal_fps(), nominal)
                self.assertEqual(engine._drop_frame_count(), drop)
        for enum in (0, 4, 6, 0xFF, 0x109, 0xFFFFFFFF):
            with self.assertRaisesRegex(ValueError, "Unsupported frame rate enum"):
                TimecodeEngine(48000, enum).samples_to_timecode(0)

    def test_independent_position_oracles_at_multiple_sample_rates(self):
        for enum, rate, nominal, drop in RATES:
            for sample_rate in (44100, 48000, 96000):
                engine = TimecodeEngine(sample_rate, enum)
                positions = ((0, 0, 0, 0), (0, 0, 0, nominal - 1),
                             (0, 0, 59, nominal - 1), (0, 1, 0, drop),
                             (0, 9, 59, nominal - 1), (0, 10, 0, 0),
                             (1, 0, 0, 0), (10, 1, 0, drop),
                             (23, 59, 59, nominal - 1), (24, 0, 0, 0))
                for position in positions:
                    with self.subTest(enum=hex(enum), sr=sample_rate, tc=position):
                        samples = expected_samples(rate, nominal, drop, sample_rate, position)
                        self.assertEqual(engine.timecode_to_samples(*position), samples)
                        self.assertEqual(engine.samples_to_timecode(samples),
                                         ":".join("%02d" % v for v in position))

    def test_native_fractional_df_marker_oracles(self):
        for enum, first_frame in ((0x0C, 4), (0x13, 8)):
            engine = TimecodeEngine(48000, enum)
            for tc, samples in (((0, 1, 0, first_frame), 2882880),
                                ((0, 10, 0, 0), 28799971),
                                ((1, 0, 0, 0), 172799827)):
                self.assertEqual(engine.timecode_to_samples(*tc), samples)
                self.assertEqual(engine.samples_to_timecode(samples),
                                 ":".join("%02d" % v for v in tc))

    def test_forward_df_against_sequential_label_enumeration(self):
        # Enumerate legal labels directly: no ten-minute formula reused here.
        for enum, rate, nominal, drop in RATES:
            if not drop:
                continue
            engine = TimecodeEngine(48000, enum)
            physical_frame = 0
            for mm in range(11):
                for ss in range(60):
                    first = drop if mm % 10 and ss == 0 else 0
                    for ff in range(first, nominal):
                        samples = nearest(Fraction(physical_frame * 48000) / rate)
                        self.assertEqual(engine.samples_to_timecode(samples),
                                         "00:%02d:%02d:%02d" % (mm, ss, ff))
                        physical_frame += 1

    def test_invalid_df_labels_and_duration_distinction(self):
        for enum, rate, nominal, drop in RATES:
            engine = TimecodeEngine(48000, enum)
            for mm in (1, 9, 11, 59):
                for ff in range(drop):
                    with self.subTest(enum=hex(enum), mm=mm, ff=ff):
                        with self.assertRaisesRegex(ValueError, "dropped frame"):
                            engine.timecode_to_samples(0, mm, 0, ff)
                        self.assertEqual(engine.duration_to_samples(0, mm, 0, ff),
                                         nearest(Fraction((mm * 60 * nominal + ff) * 48000) / rate))
            self.assertEqual(engine.timecode_to_samples(0, 10, 0, 0),
                             expected_samples(rate, nominal, drop, 48000, (0, 10, 0, 0)))
            with self.assertRaisesRegex(ValueError, "Invalid timecode"):
                engine.timecode_to_samples(0, 0, 0, nominal)

    def test_non_drop_frame_rounding_and_three_digit_frames(self):
        for enum, rate, nominal, drop in RATES:
            if drop:
                continue
            engine = TimecodeEngine(48000, enum)
            half = Fraction(48000, 2) / rate
            below = (half.numerator - 1) // half.denominator
            above = -(-half.numerator // half.denominator)
            self.assertEqual(engine.samples_to_timecode(below), "00:00:00:00")
            self.assertEqual(engine.samples_to_timecode(above), "00:00:00:01")
        self.assertEqual(TimecodeEngine(48000, 0x14).samples_to_timecode(47600),
                         "00:00:00:119")

    def test_fractional_sample_rate_and_long_half_sample_tie(self):
        engine = TimecodeEngine(48000.5, 0x10)
        self.assertEqual(engine.timecode_to_samples(1, 0, 0, 47),
                         nearest(Fraction(3600 * 48 + 47) * Fraction(96001, 2) / 48))
        self.assertEqual(TimecodeEngine(44100, 0x15).timecode_to_samples(23, 59, 59, 119),
                         3806429393)
        self.assertEqual(TimecodeEngine(44100, 0x15).duration_to_samples(0, 0, 0, 1), 368)

    def test_move_and_split_accept_last_nominal_frame_at_every_rate(self):
        for enum, rate, nominal, drop in RATES:
            with self.subTest(enum=hex(enum)):
                expected = expected_samples(rate, nominal, drop, 48000,
                                            (0, 0, 0, nominal - 1))
                session, playlist = move_session()
                session.frame_rate_enum = enum
                self.assertEqual(session.move_clip("TARGET", 0, 0, 0, nominal - 1), 1)
                self.assertEqual([event_info(item) for item in playlist.items
                                  if isinstance(item, PTBlock)], [(0, expected), (1, 96000)])
                session, _, playlist = split_session()
                session.frame_rate_enum = enum
                self.assertEqual(session.split_clip("AUDIO TRACK", "CLIP", 0, 0, 0,
                                                    nominal - 1), (0, 1, 2, expected))
                self.assertEqual(timeline_info(playlist), [(1, 0, 500), (2, expected, -1)])

    def test_move_split_df_guards_are_atomic_for_all_dropped_labels(self):
        for enum, _, _, drop in RATES:
            for ff in range(drop):
                for operation in ("move", "split"):
                    session = move_session()[0] if operation == "move" else split_session()[0]
                    session.frame_rate_enum = enum
                    before = [root.to_bytes()[0] for root in session.root_items]
                    with self.assertRaisesRegex(ValueError, "dropped frame"):
                        if operation == "move":
                            session.move_clip("TARGET", 0, 1, 0, ff)
                        else:
                            session.split_clip("AUDIO TRACK", "CLIP", 0, 1, 0, ff)
                    self.assertEqual([root.to_bytes()[0] for root in session.root_items], before)

    def test_authoring_profiles_are_not_extended_to_other_rates(self):
        from tests.test_audio_session_builder import make_audio_import_template_session
        for enum, _, _, _ in RATES:
            if enum == 0x09:
                continue
            session = make_audio_import_template_session()
            session.frame_rate_enum = enum
            before = [root.to_bytes()[0] for root in session.root_items]
            with self.assertRaisesRegex(ValueError, "23.976"):
                session.validate_audio_import_template()
            with self.assertRaisesRegex(ValueError, "23.976"):
                session.create_empty_clip_group("Production 1", "GROUP", 0, 48000)
            self.assertEqual([root.to_bytes()[0] for root in session.root_items], before)


class ExactMetadataTests(unittest.TestCase):
    def load_payload(self, directory, payload):
        data = minimal_ptx(include_metadata=False, middle=(
            generic_block(1, 0x1028, b"\x00\x00\x80\xbb\x00\x00")
            + generic_block(1, 0x204D, payload)))
        path = os.path.join(directory, "source.ptx")
        with open(path, "wb") as stream:
            stream.write(data)
        return ProToolsSession(path), bytes(data)

    def test_exact_uint32_overrides_ambiguous_or_unknown_family(self):
        for exact, family in ((0x10, 1), (0x0F, 9), (0x13, 5), (0x11, 2), (9, 255)):
            with tempfile.TemporaryDirectory() as directory:
                session, _ = self.load_payload(directory, frame_rate_payload(exact, family))
                self.assertEqual(session.frame_rate_enum, exact)

    def test_full_uint32_unknown_is_preserved_not_truncated_or_guessed(self):
        for exact in (0x109, 0xFFFFFFFF, 4):
            with tempfile.TemporaryDirectory() as directory:
                session, original = self.load_payload(directory, frame_rate_payload(exact, 9))
                self.assertEqual(session.frame_rate_enum, exact)
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    TimecodeEngine(session.sample_rate, session.frame_rate_enum).get_frame_rate()
                output = os.path.join(directory, "saved.ptx")
                session.save(output)
                with open(output, "rb") as stream:
                    self.assertEqual(stream.read(), original)

    def test_truncated_exact_field_has_no_family_fallback(self):
        for length in (0, 1, 39, 40, 41, 42, 43):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, "0x204d frame-rate payload"):
                    self.load_payload(directory, bytes([9]) * length)

    def test_variable_payload_lengths_and_unknown_bytes_survive_noop(self):
        for length in (44, 67, 68, 85):
            with tempfile.TemporaryDirectory() as directory:
                payload = bytearray((index * 17) % 256 for index in range(length))
                payload[0] = 5
                payload[40:44] = b"\x13\x00\x00\x00"
                session, original = self.load_payload(directory, payload)
                for cycle in range(2):
                    output = os.path.join(directory, "saved_%d.ptx" % cycle)
                    session.save(output)
                    with open(output, "rb") as stream:
                        self.assertEqual(stream.read(), original)
                    session = ProToolsSession(output)
                    self.assertEqual(session.frame_rate_enum, 0x13)


NATIVE_DIRECTORY = os.path.join(os.path.dirname(__file__), "fixtures", "timecode_exact_rates")
NATIVE_ENUMS = (1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21)


@unittest.skipUnless(all(os.path.isfile(os.path.join(NATIVE_DIRECTORY, "%02x.ptx" % enum))
                            for enum in NATIVE_ENUMS),
                     "Local exact-rate native fixtures absent (not distributed).")
class NativeExactRateTests(unittest.TestCase):
    def test_eighteen_native_mappings_and_two_byte_identical_noop_cycles(self):
        for enum in NATIVE_ENUMS:
            with self.subTest(enum=hex(enum)), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(NATIVE_DIRECTORY, "%02x.ptx" % enum)
                with open(path, "rb") as stream:
                    original = stream.read()
                session = ProToolsSession(path)
                self.assertEqual(session.frame_rate_enum, enum)
                self.assertEqual(session.sample_rate, 48000)
                metadata = bytes(session._root_blocks(0x204D)[0].items[0])
                self.assertIn(len(metadata), (67, 68))
                tracks, clips, markers = session.get_tracks(), session.get_timeline_clips(), session.get_markers()
                for cycle in range(2):
                    output = os.path.join(directory, "noop_%d.ptx" % cycle)
                    session.save(output)
                    with open(output, "rb") as stream:
                        self.assertEqual(stream.read(), original)
                    session = ProToolsSession(output)
                    self.assertEqual(session.frame_rate_enum, enum)
                    self.assertEqual(bytes(session._root_blocks(0x204D)[0].items[0]), metadata)
                    self.assertEqual(session.get_tracks(), tracks)
                    self.assertEqual(session.get_timeline_clips(), clips)
                    self.assertEqual(session.get_markers(), markers)
                with open(path, "rb") as stream:
                    self.assertEqual(stream.read(), original)

    def test_native_df_markers_match_labels_entered_in_pro_tools(self):
        for enum, first_frame in ((0x0C, 4), (0x13, 8)):
            session = ProToolsSession(os.path.join(NATIVE_DIRECTORY, "%02x.ptx" % enum))
            self.assertEqual({row["name"]: row["timecode"] for row in session.get_markers()}, {
                "DF_FIRST_MINUTE": "00:01:00:%02d" % first_frame,
                "DF_TEN_MINUTES": "00:10:00:00", "DF_ONE_HOUR": "01:00:00:00",
            })


@unittest.skipUnless(all(os.path.isfile(os.path.join(NATIVE_DIRECTORY, "resaved_%02x.ptx" % enum))
                            for enum in (0x10, 0x13, 0x15)),
                     "Local Pro Tools-resaved exact-rate outputs absent (not distributed).")
class NativeResavedExactRateTests(unittest.TestCase):
    def test_native_resaved_moves_and_two_byte_identical_noop_cycles(self):
        for enum, start, end in ((0x10, 527000, 623000), (0x13, 2882880, 2978880),
                                 (0x15, 172627200, 172723200)):
            with self.subTest(enum=hex(enum)), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(NATIVE_DIRECTORY, "resaved_%02x.ptx" % enum)
                with open(path, "rb") as stream:
                    original = stream.read()
                session = ProToolsSession(path)
                self.assertEqual(session.sample_rate, 48000)
                self.assertEqual(session.frame_rate_enum, enum)
                clips = session.get_timeline_clips()
                self.assertEqual(len(clips), 1)
                self.assertEqual((clips[0]["start_samples"], clips[0]["end_samples"],
                                  clips[0]["length_samples"]), (start, end, 96000))
                reference = ProToolsSession(os.path.join(NATIVE_DIRECTORY, "%02x.ptx" % enum))
                self.assertEqual(session.get_tracks(), reference.get_tracks())
                self.assertEqual(session.get_markers(), reference.get_markers())
                for cycle in range(2):
                    output = os.path.join(directory, "noop_%d.ptx" % cycle)
                    session.save(output)
                    with open(output, "rb") as stream:
                        self.assertEqual(stream.read(), original)
                    session = ProToolsSession(output)
                    self.assertEqual(session.get_timeline_clips(), clips)
                    self.assertEqual(session.get_markers(), reference.get_markers())
                with open(path, "rb") as stream:
                    self.assertEqual(stream.read(), original)


if __name__ == "__main__":
    unittest.main()
