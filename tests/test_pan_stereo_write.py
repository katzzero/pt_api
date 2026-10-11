"""Atomic static stereo Pan edits grounded in a Pro Tools-validated output."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_pan_read import pan, selected, snapshot_root_pans
from tests.test_pan_stereo_read import BEFORE, AFTER, stereo_pan
from tests.test_volume_read import state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_bus_output import pan_containers
from tests.test_stereo_to_stereo_bus_output import pointer_rows

RESAVED = Path(__file__).parent / 'fixtures' / 'native_pan_static_stereo_write_pt_resaved.ptx'


class StereoPanWriteTests(unittest.TestCase):
    def test_both_channels_only_values_change_and_noop_preserves_objects(self):
        s, names = mixed_session((1, 2, 1))
        pan(s, 0, -50)
        lanes = stereo_pan(s, 1)
        reference = copy.deepcopy(s)
        for c, value in zip(state(reference, 1).items[8:10], (-50, 50)):
            p = bytearray(c.items[1].items[0])
            struct.pack_into('<h', p, 26, value)
            c.items[1].items[0] = bytes(p)
        ui = state(s, 1).items[0]
        self.assertEqual(s.set_track_pan(names[1], -50, 50), 1)
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertIs(state(s, 1).items[0], ui)
        saved = [lane.items[0] for lane in lanes]
        self.assertEqual(s.set_track_pan(names[1], -50, 50), 0)
        for lane, raw in zip(lanes, saved): self.assertIs(lane.items[0], raw)
        self.assertEqual(s._removed_offsets, [])
        self.assertIs(type(s.data), object)

    def test_left_only_and_right_only_preserve_the_other_lane_object(self):
        s, names = mixed_session((2,))
        left, right = stereo_pan(s, 0)
        original_left, original_right = left.items[0], right.items[0]
        self.assertEqual(s.set_track_pan(names[0], -50), 1)
        self.assertEqual(s.get_track_pan_lr(names[0]), (-50, 100))
        self.assertIs(right.items[0], original_right)
        left_now = left.items[0]
        self.assertEqual(s.set_track_pan(names[0], -50, 50), 1)
        self.assertIs(left.items[0], left_now)
        self.assertEqual(s.get_track_pan_lr(names[0]), (-50, 50))
        self.assertNotEqual(left.items[0], original_left)

    def test_invalid_right_never_leaves_left_partially_modified(self):
        for value, error in ((True, TypeError), (False, TypeError), ('50', TypeError),
                (50.0, TypeError), (float('nan'), TypeError), (float('inf'), TypeError),
                (-101, ValueError), (101, ValueError), (2**100, ValueError)):
            s, names = mixed_session((2,))
            stereo_pan(s, 0)
            before = snapshot(s)
            with self.subTest(value=value):
                with self.assertRaises(error): s.set_track_pan(names[0], -50, value)
                self.assertEqual(snapshot(s), before)

    def test_second_payload_preparation_error_is_atomic(self):
        s, names = mixed_session((2,))
        stereo_pan(s, 0)
        before = snapshot(s)
        original = struct.pack_into
        calls = []
        def pack(*args):
            calls.append(args)
            if len(calls) == 2: raise RuntimeError('second prepared payload failure')
            return original(*args)
        with mock.patch('pt_api.struct.pack_into', side_effect=pack):
            with self.assertRaisesRegex(RuntimeError, 'second'):
                s.set_track_pan(names[0], -50, 50)
        self.assertEqual(snapshot(s), before)

    def test_other_stereo_or_mono_invalid_rejects_before_any_write_or_noop(self):
        for bad in ('other_stereo', 'other_mono', 'target_right', 'volume'):
            for values in ((-50, 50), (-100, 100)):
                s, names = mixed_session((2, 2, 1))
                lanes = stereo_pan(s, 0)
                other = stereo_pan(s, 1)
                if bad == 'other_stereo': other[1].items[0][-1] = 1
                elif bad == 'other_mono': pan(s, 2).items[0][22] = 1
                elif bad == 'target_right': lanes[1].items[0] = payload(((0, 100), (100, 50)))
                else: state(s, 2).items[4].items[0][0] ^= 1
                before = snapshot(s)
                with self.assertRaises(ValueError): s.set_track_pan(names[0], *values)
                self.assertEqual(snapshot(s), before)

    def test_mixed_slot_order_and_boundary_compositions_restore_tree(self):
        for widths in ((2,), (2, 1), (1, 2), (1, 2, 2, 1)):
            s, names = mixed_session(widths)
            for i, width in enumerate(widths):
                if width == 2: stereo_pan(s, i)
                else: pan(s, i, -50)
            before = snapshot(s)
            for i, width in enumerate(widths):
                if width == 2:
                    for values in ((100, -100), (0, 0), (-50, 50), (-100, 100)):
                        s.set_track_pan(names[i], *values)
                        self.assertEqual(s.get_track_pan_lr(names[i]), values)
            self.assertEqual(snapshot(s), before)

    def test_absent_stereo_lanes_and_automation_are_not_created_or_collapsed(self):
        for variant in ('empty_left', 'empty_right', 'curve_left', 'curve_right'):
            s, names = mixed_session((2,))
            lanes = stereo_pan(s, 0)
            index = 0 if variant.endswith('left') else 1
            if variant.startswith('empty'): state(s).items[8 + index].items = [bytes(14)]
            else: lanes[index].items[0] = payload(((0, -100), (100, -50)))
            before = snapshot(s)
            with self.assertRaises(ValueError): s.set_track_pan(names[0], -50, 50)
            self.assertEqual(snapshot(s), before)


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'Local stereo Pan pair absent')
class NativeStereoPanWriteTests(unittest.TestCase):
    def test_left_only_payload_matches_native_oracle(self):
        s = ProToolsSession(str(BEFORE))
        right = pan_containers(s, 1)[1].items[1].items[0]
        self.assertEqual(s.set_track_pan('C_STEREO', -50), 1)
        oracle = ProToolsSession(str(AFTER))
        self.assertEqual(pan_containers(s, 1)[0].items[1].items[0],
                         pan_containers(oracle, 1)[0].items[1].items[0])
        self.assertIs(pan_containers(s, 1)[1].items[1].items[0], right)

    def test_both_writes_reproduce_validated_candidate_then_restore_source(self):
        s = ProToolsSession(str(BEFORE))
        b = ProToolsSession(str(BEFORE))
        self.assertEqual(s.set_track_pan('C_STEREO', -50, 50), 1)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'candidate.ptx'
            s.save(str(out))
            import hashlib
            self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(),
                'c0100f0a4f9ca2957fed23b28d914d5bd0abb2898c1cdfda0676c31cc159d30b')
            for kind in (0x1029, 0x260e, 0x2602, 0x1001, 0x2628, 0x2637):
                self.assertEqual(selected(s, kind), selected(b, kind))
            self.assertEqual(pointer_rows(s), pointer_rows(b))
            for i in (0, 2): self.assertEqual(snapshot_root_pans(s, i), snapshot_root_pans(b, i))
            s.set_track_pan('C_STEREO', -100, 100)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), BEFORE.read_bytes())


@unittest.skipUnless(RESAVED.exists(), 'Local stereo Pan writer native save absent')
class ResavedStereoPanWriteTests(unittest.TestCase):
    def test_native_save_cycles_and_reversible_one_and_two_channel_edits(self):
        s = ProToolsSession(str(RESAVED))
        original = RESAVED.read_bytes()
        self.assertEqual(s.get_track_pan_lr('C_STEREO'), (-50, 50))
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for _ in range(2):
                self.assertEqual(s.set_track_pan('C_STEREO', -50, 50), 0)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
            for left, right in ((-100, None), (-100, 100), (50, -50), (-50, 50)):
                s.set_track_pan('C_STEREO', left, right)
                s.save(str(out))
                s = ProToolsSession(str(out))
            self.assertEqual(out.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
