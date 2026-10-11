"""Verified static mono Pan writes; no automation collapse or UI inference."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_pan_read import BEFORE, AFTER, pan, selected, snapshot_root_pans
from tests.test_volume_read import read_session, state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_bus_output import pan_containers
from tests.test_stereo_to_stereo_bus_output import pointer_rows

RESAVED = Path(__file__).parent / 'fixtures' / 'native_pan_static_mono_write_pt_resaved.ptx'


class MonoPanWriteTests(unittest.TestCase):
    def test_value_only_changes_and_noop_preserves_lane_and_opaque_ui(self):
        s = read_session(('PISTE É', 'SECOND'))
        lane = pan(s)
        reference = copy.deepcopy(s)
        expected = bytearray(state(reference).items[8].items[1].items[0])
        struct.pack_into('<h', expected, 26, -50)
        state(reference).items[8].items[1].items[0] = bytes(expected)
        ui = state(s).items[0]
        removed = list(s._removed_offsets)
        self.assertEqual(s.set_track_pan('PISTE É', -50), 1)
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertIs(state(s).items[8].items[1], lane)
        self.assertIs(state(s).items[0], ui)
        before_noop = lane.items[0]
        self.assertEqual(s.set_track_pan('PISTE É', -50), 0)
        self.assertIs(lane.items[0], before_noop)
        self.assertEqual(s._removed_offsets, removed)
        self.assertIs(type(s.data), object)

    def test_boundaries_and_compositions_restore_entire_tree(self):
        s = read_session()
        pan(s, 0, 0)
        pan(s, 1, 30)
        before = snapshot(s)
        for value in (-100, -50, 0, 50, 100, 0):
            s.set_track_pan('FIRST', value)
            self.assertEqual(s.get_track_pan('FIRST'), value)
            self.assertEqual(s.get_track_pan('SECOND'), 30)
        self.assertEqual(snapshot(s), before)

    def test_invalid_values_and_right_channel_fail_before_mutation(self):
        for value, right, error in ((True, None, TypeError), (False, None, TypeError),
                (None, None, TypeError), ('50', None, TypeError), (50.0, None, TypeError),
                (float('nan'), None, TypeError), (float('inf'), None, TypeError),
                (-101, None, ValueError), (101, None, ValueError), (2**100, None, ValueError),
                (-50, 0, ValueError), (-50, -100, ValueError), (-50, 'bad', ValueError)):
            with self.subTest(value=value, right=right):
                s = read_session()
                pan(s)
                before = snapshot(s)
                with self.assertRaises(error): s.set_track_pan('FIRST', value, right)
                self.assertEqual(snapshot(s), before)

    def test_invalid_names_and_missing_targets_are_atomic(self):
        s = read_session()
        pan(s)
        before = snapshot(s)
        for name, error in ((None, TypeError), (False, TypeError), ('', ValueError),
                ('X\x00Y', ValueError), ('\ud800', ValueError), ('first', ValueError),
                ('ABSENT', ValueError)):
            with self.subTest(name=repr(name)):
                with self.assertRaises(error): s.set_track_pan(name, -50)
                self.assertEqual(snapshot(s), before)

    def test_absent_lane_is_not_created_even_for_center(self):
        s = read_session()
        before = snapshot(s)
        for value in (0, -50):
            with self.assertRaisesRegex(ValueError, 'existing'):
                s.set_track_pan('FIRST', value)
            self.assertEqual(snapshot(s), before)

    def test_mono_after_stereo_uses_live_slot_not_channel_playlist(self):
        s, names = mixed_session((1, 2, 1))
        pan(s, 0, 10)
        pan(s, 2, 20)
        stereo = snapshot_root_pans(s, 1)
        self.assertEqual(s.set_track_pan(names[2], -50), 1)
        self.assertEqual(s.get_track_pan(names[0]), 10)
        self.assertEqual(s.get_track_pan(names[2]), -50)
        self.assertEqual(snapshot_root_pans(s, 1), stereo)
        before = snapshot(s)
        with self.assertRaisesRegex(ValueError, 'stereo'): s.set_track_pan(names[1], -50)
        self.assertEqual(snapshot(s), before)

    def test_late_malformed_pan_or_volume_blocks_noop_and_write(self):
        for kind in ('other_pan', 'other_volume', 'target_automation'):
            for value in (0, -50):
                s = read_session()
                lane = pan(s)
                if kind == 'other_pan': pan(s, 1).items[0][-1] = 1
                elif kind == 'other_volume': state(s, 1).items[4].items[0][0] ^= 1
                else: lane.items[0] = payload(((0, 0), (100, -50)))
                before = snapshot(s)
                with self.assertRaises(ValueError): s.set_track_pan('FIRST', value)
                self.assertEqual(snapshot(s), before)


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'Local native Pan pair absent')
class NativeMonoPanWriteTests(unittest.TestCase):
    def test_native_lane_equality_and_only_two_bytes_change(self):
        s = ProToolsSession(str(BEFORE))
        original = bytes(s.data)
        lane = pan_containers(s, 0)[0].items[1]
        offset = lane.original_offset + 9 + 26
        expected = bytearray(original)
        struct.pack_into('<h', expected, offset, -50)
        self.assertEqual(s.set_track_pan('A_OUTSIDE', -50), 1)
        oracle = ProToolsSession(str(AFTER))
        self.assertEqual(lane.items[0], pan_containers(oracle, 0)[0].items[1].items[0])
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'candidate.ptx'
            s.save(str(out))
            reload = ProToolsSession(str(out))
            self.assertEqual(bytes(reload.data), bytes(expected))
            baseline = ProToolsSession(str(BEFORE))
            for kind in (0x1029, 0x260e, 0x2602, 0x2628, 0x1001, 0x2637):
                self.assertEqual(selected(reload, kind), selected(baseline, kind))
            self.assertEqual(pointer_rows(reload), pointer_rows(baseline))

    def test_native_save_cycles_and_reverse_restore_original(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for source in (BEFORE, AFTER):
                s = ProToolsSession(str(source))
                original = source.read_bytes()
                old = s.get_track_pan('A_OUTSIDE')
                s.set_track_pan('A_OUTSIDE', 50)
                s.save(str(out))
                edited = out.read_bytes()
                for _ in range(2):
                    s = ProToolsSession(str(out))
                    self.assertEqual(s.get_track_pan('A_OUTSIDE'), 50)
                    self.assertEqual(s.set_track_pan('A_OUTSIDE', 50), 0)
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), edited)
                s.set_track_pan('A_OUTSIDE', old)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)


@unittest.skipUnless(RESAVED.exists(), 'Local Pro Tools-resaved Pan output absent')
class ResavedMonoPanWriteTests(unittest.TestCase):
    def test_resaved_value_and_reversible_edits_preserve_all_metadata(self):
        s = ProToolsSession(str(RESAVED))
        self.assertEqual(s.get_track_pan('A_OUTSIDE'), -50)
        original = RESAVED.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for _ in range(2):
                self.assertEqual(s.set_track_pan('A_OUTSIDE', -50), 0)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
            s.set_track_pan('A_OUTSIDE', 50)
            s.save(str(out))
            s = ProToolsSession(str(out))
            s.set_track_pan('A_OUTSIDE', -50)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
