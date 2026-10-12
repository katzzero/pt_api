"""Read-only native Sync Point profile; no implicit writer expansion."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_timeline_read import block, definition, event, geometry, make_session, playlist


def sync_definition(name="SYNC_TAKE", sync_offset=33364, length=43697):
    encoded = name.encode("utf-8")
    attributes = bytearray.fromhex(
        "00302244082e75075482b1aad273ebafd273ebaf"
        "fffffffffffffffffeff00000000ffff04000400"
        "0000000000000000000000000000000000000000000000000000000000"
    )
    struct.pack_into("<HH", attributes, 8, sync_offset, length)
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded) + attributes
    return block(11, 0x2629, [block(4, 0x2628, [payload])])


def sync_session(sync_offset=33364, anchor=1740374500, length=43697):
    return make_session(
        [sync_definition(sync_offset=sync_offset, length=length)],
        [playlist("TRACK", [event(0, anchor)])], geometries=None,
    )


def snapshot(session):
    return [root.to_bytes()[0] for root in session.root_items]


class AudioSyncPointTests(unittest.TestCase):
    def test_native_unsigned_sync_offset_length_and_audible_bounds(self):
        session = sync_session()
        before = snapshot(session)
        info = session._audio_clip_info_by_id(0)
        self.assertEqual(info["src_offset"], 488750)
        self.assertEqual(info["sync_offset"], 33364)
        self.assertEqual(info["length"], 43697)
        clip = session.get_timeline_clips()[0]
        self.assertEqual(
            (clip["start_samples"], clip["length_samples"], clip["end_samples"]),
            (1740341136, 43697, 1740384833),
        )
        self.assertEqual(clip["src_offset_samples"], 488750)
        self.assertEqual(session.get_clips()[0]["length"], "00:00:00:22")
        self.assertEqual(snapshot(session), before)

    def test_multiple_occurrences_use_independent_anchors_and_sort_by_start(self):
        session = make_session(
            [sync_definition(), definition("PLAIN")],
            [playlist("TRACK", [event(0, 100000), event(1, 80000), event(0, 200000)])],
            geometries=None,
        )
        self.assertEqual(
            [row["start_samples"] for row in session.get_timeline_clips()],
            [66636, 80000, 166636],
        )

    def test_zero_sync_offset_is_still_a_sync_profile(self):
        session = sync_session(sync_offset=0, anchor=100000)
        self.assertEqual(session.get_timeline_clips()[0]["start_samples"], 100000)
        with self.assertRaisesRegex(NotImplementedError, "reading only"):
            session._editable_audio_clip_info_by_id(0)

    def test_sync_point_outside_clip_does_not_change_source_offset(self):
        session = sync_session(sync_offset=65535, anchor=100000, length=1)
        row = session.get_timeline_clips()[0]
        self.assertEqual(row["start_samples"], 34465)
        self.assertEqual(row["length_samples"], 1)
        self.assertEqual(row["src_offset_samples"], 488750)

    def test_anchor_underflow_is_rejected_without_mutation(self):
        session = sync_session(anchor=33363)
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, "before sample zero"):
            session.get_timeline_clips(False)
        self.assertEqual(snapshot(session), before)

    def test_unverified_sync_headers_and_selectors_remain_rejected(self):
        for relative_offset, value in ((0, 1), (1, 0x20), (2, 0x12),
                                      (2, 0x32), (2, 0x42), (3, 0x34), (4, 0)):
            with self.subTest(relative_offset=relative_offset, value=value):
                session = sync_session()
                payload = session.root_items[0].items[1].items[0].items[0]
                a = 4 + len("SYNC_TAKE")
                payload[a + relative_offset] = value
                before = snapshot(session)
                with self.assertRaises(ValueError):
                    session.get_timeline_clips(False)
                self.assertEqual(snapshot(session), before)

    def test_truncated_sync_and_length_fields_are_rejected(self):
        session = sync_session()
        payload = session.root_items[0].items[1].items[0].items[0]
        a = 4 + len("SYNC_TAKE")
        for size in range(a + 5, a + 12):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, "Truncated source-offset/length"):
                    session._decode_audio_clip_payload(payload[:size])

    def test_fades_bind_to_audible_edges_not_sync_anchor(self):
        fade_in = bytearray(22)
        struct.pack_into("<H", fade_in, 8, 100)
        fade_out = bytearray(27)
        struct.pack_into("<H", fade_out, 8, 200)
        session = make_session(
            [sync_definition()],
            [playlist("TRACK", [event(0, 1740341136, event_type=1),
                                event(0, 1740374500),
                                event(1, 1740384833, event_type=1)])],
            [geometry(fade_in), geometry(fade_out)],
        )
        before = snapshot(session)
        fades = [row for row in session.get_timeline_clips() if row["is_fade"]]
        self.assertEqual([(r["start_samples"], r["end_samples"]) for r in fades],
                         [(1740341136, 1740341236), (1740384633, 1740384833)])
        self.assertTrue(all(row["clip_name"] == "SYNC_TAKE" for row in fades))
        self.assertEqual(snapshot(session), before)

    def test_geometry_writers_refuse_sync_profile_without_mutation(self):
        operations = (
            lambda s: s.move_clip("SYNC_TAKE", 10, 1, 0, 0),
            lambda s: s.duplicate_clip("SYNC_TAKE", 10, 1, 0, 0),
            lambda s: s.delete_clip("SYNC_TAKE"),
            lambda s: s.add_fade("TRACK", "SYNC_TAKE", 10, 1, 0, 0),
            lambda s: s.create_subclip(0, "NEW", 100, 1000),
            lambda s: s.trim_clip_start("SYNC_TAKE", 100),
            lambda s: s.trim_clip_end("SYNC_TAKE", 100),
            lambda s: s.trim_clip_head("SYNC_TAKE", 1740341200),
            lambda s: s.trim_clip_tail("SYNC_TAKE", 1740384700),
            lambda s: s.keep_clip_regions("SYNC_TAKE", [(1740341200, 1740384700)]),
            lambda s: s.set_clip_gain("SYNC_TAKE", 6),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                session = sync_session()
                # Match verified event shape for in-place writers; rejection
                # must be the sync profile, not unrelated fixture geometry.
                e = session.root_items[1].items[1].items[1]
                e.items[0].block_type = 10
                session.root_items.append(block(1, 0x2637, [bytearray(4)]))
                before = snapshot(session)
                with self.assertRaisesRegex(NotImplementedError, "reading only"):
                    operation(session)
                self.assertEqual(snapshot(session), before)
                self.assertEqual(session._removed_offsets, [])

    def test_relink_preflight_does_not_advertise_sync_writer_support(self):
        session = sync_session()
        status = session.get_relink_write_status("TRACK", "SYNC_TAKE", 1740341136)
        self.assertFalse(status["supported"])
        self.assertEqual(status["code"], "unsupported_clip_layout")

    def test_independent_name_and_mute_fields_preserve_sync_geometry(self):
        session = sync_session()
        info = session._audio_clip_info_by_id(0)
        session.rename_clip("SYNC_TAKE", "RENAMED")
        session.mute_clip("RENAMED")
        row = session.get_timeline_clips(False)[0]
        self.assertTrue(row["muted"])
        self.assertEqual(row["start_samples"], 1740341136)
        updated = session._audio_clip_info_by_id(0)
        self.assertEqual({k: v for k, v in updated.items() if k != "name"},
                         {k: v for k, v in info.items() if k != "name"})


NATIVE = Path(__file__).parent / "fixtures" / "audio_sync_point" / "native.ptx"


@unittest.skipUnless(NATIVE.is_file(), "Private native Sync Point fixture unavailable")
class NativeAudioSyncPointTests(unittest.TestCase):
    def test_audio_only_read_matches_native_capture_and_noop_save_is_identical(self):
        original = NATIVE.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         "763f9bb87e58005ab9858c558dbca6fff3999c7a03621460bea83ed41b9c764c")
        session = ProToolsSession(str(NATIVE))
        self.assertEqual(len(session.get_clips()), 13)
        rows = session.get_timeline_clips(False)
        self.assertEqual(len(rows), 14)
        target = next(row for row in rows if row["clip_name"] == "SIM_020_06-17.L")
        self.assertEqual((target["start_samples"], target["length_samples"], target["end_samples"]),
                         (1740341136, 43697, 1740384833))
        self.assertEqual(target["src_offset_samples"], 488750)
        self.assertEqual(target["physical_filename"], "SIM_020_06.L.wav")
        with tempfile.TemporaryDirectory() as folder:
            output = str(Path(folder) / "noop.ptx")
            session.save(output)
            self.assertEqual(Path(output).read_bytes(), original)
            reloaded = ProToolsSession(output)
            self.assertEqual(reloaded.get_timeline_clips(False), rows)

    def test_native_full_timeline_with_compact_crossfades_and_volume_reads(self):
        session = ProToolsSession(str(NATIVE))
        original = NATIVE.read_bytes()
        before = snapshot(session)
        rows = session.get_timeline_clips()
        self.assertEqual(len(rows), 27)
        self.assertEqual(sum(row["is_fade"] for row in rows), 13)
        for anchor, duration, clip_name in (
            (1740137918, 337, "SIM_020-TCE_06.L"),
            (1740384833, 2160, "SIM_020-TCE_07.L"),
            (1740408795, 675, "SIM_020-TCE_08.L"),
            (1740427223, 877, "SIM_020-TCE_09.L"),
        ):
            matched = [row for row in rows if row["is_fade"] and row["start_samples"] == anchor]
            self.assertEqual(len(matched), 1)
            self.assertEqual(matched[0]["length_samples"], duration)
            self.assertEqual(matched[0]["end_samples"], anchor + duration)
            self.assertEqual(matched[0]["clip_name"], clip_name)
        self.assertEqual(snapshot(session), before)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "full_read_noop.ptx"
            session.save(str(output))
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(ProToolsSession(str(output)).get_timeline_clips(), rows)
        lanes = session.get_volume_automation()
        self.assertEqual([lane["track"] for lane in lanes], ["Audio 1", "1PS"])
        self.assertTrue(all(lane["ok"] for lane in lanes))


if __name__ == "__main__":
    unittest.main()
