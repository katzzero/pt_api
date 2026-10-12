"""Reading unused native fade definitions does not relax writer invariants."""
import struct
import unittest

from tests.test_timeline_read import definition, event, geometry, make_session, playlist


def session_with_unused_fade():
    fade_in = bytearray.fromhex("00000000002000001a1d030100000000000000000000")
    unused = bytes.fromhex("0000000000220000f71ef71e020100000000000000000000000000")
    return make_session(
        [definition("AUDIO", length=10000)],
        [playlist("TRACK", [event(0, 100000, event_type=1), event(0, 100000)])],
        [geometry(fade_in), geometry(unused)],
    )


def snapshot(session):
    return [root.to_bytes()[0] for root in session.root_items]


class UnusedFadeCatalogTests(unittest.TestCase):
    def test_unused_geometry_is_not_a_timeline_event_and_is_preserved(self):
        session = session_with_unused_fade()
        before = snapshot(session)
        rows = session.get_timeline_clips()
        self.assertEqual(len(rows), 2)
        fade = next(row for row in rows if row["is_fade"])
        self.assertEqual((fade["start_samples"], fade["end_samples"]), (100000, 107450))
        self.assertEqual(snapshot(session), before)

    def test_no_visible_fades_can_have_unused_unknown_geometry(self):
        session = make_session(
            [definition("AUDIO")], [playlist("TRACK", [event(0, 100000)])],
            [geometry(bytes(32))],
        )
        before = snapshot(session)
        self.assertEqual(len(session.get_timeline_clips()), 1)
        self.assertEqual(snapshot(session), before)

    def test_count_and_references_remain_strict_in_reading_mode(self):
        session = session_with_unused_fade()
        struct.pack_into("<I", session.root_items[-1].items[0], 0, 1)
        with self.assertRaisesRegex(ValueError, "Inconsistent 0x2630 geometry count"):
            session.get_timeline_clips()
        session = session_with_unused_fade()
        payload = session.root_items[1].items[1].items[1].items[0].items[0]
        struct.pack_into("<I", payload, 2, 2)
        with self.assertRaisesRegex(ValueError, "unknown geometry ID"):
            session.get_timeline_clips()

    def test_writer_binding_contract_is_not_relaxed(self):
        operations = (
            lambda s: s._validated_fade_geometry_bindings(),
            lambda s: s._fade_bindings_for_audio_placement(100000, 10000, s.root_items[1].items[1]),
            lambda s: s.move_clip("AUDIO", 0, 0, 5, 0),
            lambda s: s.duplicate_clip("AUDIO", 0, 0, 5, 0),
            lambda s: s.delete_clip("AUDIO"),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                session = session_with_unused_fade()
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, "geometry counts do not match"):
                    operation(session)
                self.assertEqual(snapshot(session), before)
                self.assertEqual(session._removed_offsets, [])

    def test_private_flag_must_be_boolean(self):
        with self.assertRaisesRegex(TypeError, "allow_unused"):
            session_with_unused_fade()._validated_fade_geometry_bindings(allow_unused=1)


if __name__ == "__main__":
    unittest.main()
