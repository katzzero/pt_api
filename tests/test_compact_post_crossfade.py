"""Native compact post-splice crossfade reader; writers stay excluded."""
import struct
import unittest

from tests.test_timeline_read import definition, event, geometry, make_session, playlist


def compact_payload(duration=337):
    payload = bytearray.fromhex(
        "0000000000200000510101010000000000000000000000000000000000000000"
    )
    struct.pack_into("<H", payload, 8, duration)
    return payload


def make_crossfade_session(duration=337):
    return make_session(
        [definition("LEFT", length=10000), definition("RIGHT", length=100000)],
        [playlist("TRACK", [event(0, 90000), event(0, 100000, event_type=1), event(1, 100000)])],
        [geometry(compact_payload(duration))],
    )


def snapshot(session):
    return [root.to_bytes()[0] for root in session.root_items]


class CompactPostCrossfadeTests(unittest.TestCase):
    def test_native_width_values_use_anchor_as_start_and_prefer_right_clip(self):
        for duration in (1, 255, 256, 337, 675, 877, 2160, 65535):
            with self.subTest(duration=duration):
                session = make_crossfade_session(duration)
                before = snapshot(session)
                rows = session.get_timeline_clips()
                fade = next(row for row in rows if row["is_fade"])
                self.assertEqual((fade["start_samples"], fade["end_samples"]),
                                 (100000, 100000 + duration))
                self.assertEqual(fade["length_samples"], duration)
                self.assertEqual(fade["clip_name"], "RIGHT")
                self.assertEqual(snapshot(session), before)

    def test_post_crossfade_can_start_at_sample_zero(self):
        session = make_session(
            [definition("RIGHT")], [playlist("TRACK", [event(0, 0, event_type=1), event(0, 0)])],
            [geometry(compact_payload())],
        )
        fade = next(row for row in session.get_timeline_clips() if row["is_fade"])
        self.assertEqual((fade["start_samples"], fade["end_samples"]), (0, 337))

    def test_missing_right_clip_retains_existing_crossfade_left_fallback(self):
        session = make_session(
            [definition("LEFT", length=10000)],
            [playlist("TRACK", [event(0, 90000), event(0, 100000, event_type=1)])],
            [geometry(compact_payload())],
        )
        fade = next(row for row in session.get_timeline_clips() if row["is_fade"])
        self.assertEqual(fade["clip_name"], "LEFT")

    def test_unverified_header_mode_shape_tail_and_zero_duration_are_refused(self):
        for offset, value in ((0, 1), (4, 1), (5, 0x22), (6, 1), (7, 1),
                              (10, 2), (11, 2), (12, 1), (31, 1)):
            with self.subTest(offset=offset):
                session = make_crossfade_session()
                session.root_items[-1].items[1].items[0][offset] = value
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, "compact Post Crossfade layout"):
                    session.get_timeline_clips()
                self.assertEqual(snapshot(session), before)
        with self.assertRaisesRegex(ValueError, "duration must be positive"):
            make_crossfade_session(0).get_timeline_clips()

    def test_truncated_and_wrong_size_profiles_are_refused(self):
        session = make_crossfade_session()
        for size in (0, 8, 10, 12, 31, 33):
            raw = compact_payload()[:size]
            if size == 33:
                raw.append(0)
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, "compact Post Crossfade layout"):
                    session._decode_compact_post_crossfade_length(raw)

    def test_ambiguous_or_missing_audio_association_is_refused(self):
        session = make_crossfade_session()
        track = session.root_items[1].items[1]
        track.items.insert(-1, event(0, 100000))
        struct.pack_into("<I", track.items[0], 4 + len("TRACK"), 4)
        with self.assertRaisesRegex(ValueError, "associated with one audio placement"):
            session.get_timeline_clips()
        session = make_crossfade_session()
        fade_payload = session.root_items[1].items[1].items[2].items[0].items[0]
        struct.pack_into("<Q", fade_payload, 7, 100001)
        with self.assertRaisesRegex(ValueError, "associated with one audio placement"):
            session.get_timeline_clips()

    def test_timing_writers_refuse_either_side_even_without_link_flag(self):
        for name in ("LEFT", "RIGHT"):
            operations = (
                lambda s: s.move_clip(name, 0, 0, 5, 0),
                lambda s: s.duplicate_clip(name, 0, 0, 5, 0),
                lambda s: s.delete_clip(name),
                lambda s: s.trim_clip_start(name, 100),
                lambda s: s.trim_clip_end(name, 100),
                lambda s: s.add_fade("TRACK", name, 0, 0, 2 if name == "LEFT" else 3, 0,
                                     duration_ss=0, duration_ff=1),
            )
            for operation in operations:
                with self.subTest(name=name, operation=operation):
                    session = make_crossfade_session()
                    before = snapshot(session)
                    with self.assertRaisesRegex(NotImplementedError, "compact 32-byte crossfade"):
                        operation(session)
                    self.assertEqual(snapshot(session), before)
                    self.assertEqual(session._removed_offsets, [])


if __name__ == "__main__":
    unittest.main()
