"""Static Pan targets may coexist with validated, untouched Pan curves."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_pan_read import pan
from tests.test_pan_stereo_read import stereo_pan
from tests.test_volume_read import state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'


class StaticPanCoexistenceTests(unittest.TestCase):
    def test_mono_target_with_other_mono_curve_changes_only_two_bytes(self):
        s, names = mixed_session((1, 1))
        target = pan(s, 0, 20)
        other = pan(s, 1)
        other.items[0] = payload(((0, -50), (123, 25), (0xffffffff, 100)))
        other_raw = other.items[0]
        before = snapshot(s)
        self.assertEqual(s.get_track_pan(names[0]), 20)
        self.assertEqual(s.get_track_pan_lr(names[0]), (20, None))
        self.assertEqual(snapshot(s), before)
        expected = copy.deepcopy(s)
        data = bytearray(target.items[0])
        struct.pack_into('<h', data, 26, -75)
        state(expected, 0).items[8].items[1].items[0] = bytes(data)
        self.assertEqual(s.set_track_pan(names[0], -75), 1)
        self.assertEqual(snapshot(s), snapshot(expected))
        self.assertIs(other.items[0], other_raw)
        self.assertEqual(s.set_track_pan(names[0], -75), 0)

    def test_stereo_target_coexists_with_mono_and_stereo_curves_in_any_slot(self):
        for widths in ((2, 1, 2), (1, 2, 2), (2, 2, 1)):
            for target_index, width in enumerate(widths):
                if width != 2:
                    continue
                with self.subTest(widths=widths, target=target_index):
                    s, names = mixed_session(widths)
                    for i, w in enumerate(widths):
                        lanes = stereo_pan(s, i) if w == 2 else (pan(s, i),)
                        if i != target_index:
                            for lane in lanes:
                                lane.items[0] = payload(((0, 0), (101, -25), (555, 50)))
                    expected = copy.deepcopy(s)
                    for c, value in zip(state(expected, target_index).items[8:10], (-40, 60)):
                        raw = bytearray(c.items[1].items[0])
                        struct.pack_into('<h', raw, 26, value)
                        c.items[1].items[0] = bytes(raw)
                    self.assertEqual(s.get_track_pan_lr(names[target_index]), (-100, 100))
                    self.assertEqual(s.set_track_pan(names[target_index], -40, 60), 1)
                    self.assertEqual(snapshot(s), snapshot(expected))
                    self.assertEqual(s.get_track_pan_lr(names[target_index]), (-40, 60))

    def test_lr_mono_reader_preserves_stereo_curves(self):
        s, names = mixed_session((2, 1))
        for lane in stereo_pan(s, 0):
            lane.items[0] = payload(((0, 100), (4321, -100)))
        pan(s, 1, -25)
        before = snapshot(s)
        self.assertEqual(s.get_track_pan_lr(names[1]), (-25, None))
        self.assertEqual(snapshot(s), before)

    def test_curve_target_is_still_rejected_by_all_static_methods(self):
        for widths in ((1, 1), (2, 1)):
            s, names = mixed_session(widths)
            lanes = stereo_pan(s, 0) if widths[0] == 2 else (pan(s, 0),)
            lanes[0].items[0] = payload(((0, 0), (100, 50)))
            before = snapshot(s)
            for call in (lambda: s.get_track_pan(names[0]),
                         lambda: s.get_track_pan_lr(names[0]),
                         lambda: s.set_track_pan(names[0], -50)):
                with self.assertRaises(ValueError):
                    call()
                self.assertEqual(snapshot(s), before)

    def test_unknown_other_curve_is_not_allowed_even_for_noop(self):
        for other_width in (1, 2):
            for defect in ('size', 'count', 'flags', 'order', 'range', 'terminator'):
                s, names = mixed_session((2, other_width))
                stereo_pan(s, 0)
                lane = (stereo_pan(s, 1) if other_width == 2 else (pan(s, 1),))[-1]
                raw = bytearray(payload(((0, 0), (100, 50))))
                if defect == 'size': struct.pack_into('<I', raw, 4, 999)
                elif defect == 'count': struct.pack_into('<I', raw, 10, 1)
                elif defect == 'flags': raw[14] = 4
                elif defect == 'order': struct.pack_into('<I', raw, 28, 0)
                elif defect == 'range': struct.pack_into('<h', raw, 32, 101)
                else: raw[-1] = 1
                lane.items[0] = bytes(raw)
                before = snapshot(s)
                with self.subTest(width=other_width, defect=defect):
                    for call in (lambda: s.get_track_pan_lr(names[0]),
                                 lambda: s.set_track_pan(names[0], -100, 100)):
                        with self.assertRaises(ValueError):
                            call()
                        self.assertEqual(snapshot(s), before)

    def test_no_automatic_lane_creation_with_other_valid_curve(self):
        s, names = mixed_session((1, 1))
        pan(s, 1).items[0] = payload(((0, 0), (100, -50)))
        before = snapshot(s)
        self.assertIsNone(s.get_track_pan(names[0]))
        self.assertEqual(s.get_track_pan_lr(names[0]), (None, None))
        with self.assertRaisesRegex(ValueError, 'existing'):
            s.set_track_pan(names[0], 0)
        self.assertEqual(snapshot(s), before)


class NativePanCoexistenceTests(unittest.TestCase):
    def check_native(self, basename, track_name, ordinal, stereo, prepare_static=False):
        source = FIXTURES / basename
        if not source.exists():
            self.skipTest('Local native Pan automation fixture absent')
        s = ProToolsSession(str(source))
        source_file = source.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            if prepare_static:
                # Composition on a native base, NOT a new native oracle:
                # use the already validated automation writer to make the
                # target static while preserving the native stereo curves.
                s.set_pan_automation(track_name, [(0, -50)])
                control = Path(folder) / 'control.ptx'
                s.save(str(control))
                s = ProToolsSession(str(control))
                original_file = control.read_bytes()
            else:
                original_file = source_file
            original = bytes(s.data)
            old = s.get_track_pan_lr(track_name)
            containers = state(s, ordinal).items[8:10]
            lanes = [c.items[1] for c in containers] if stereo else [containers[0].items[1]]
            expected = bytearray(original)
            targets = (-25, 75) if stereo else (-25,)
            for lane, value in zip(lanes, targets):
                struct.pack_into('<h', expected, lane.original_offset + 9 + 26, value)
            pointers = pointer_rows(s)
            self.assertEqual(s.set_track_pan(track_name, targets[0], targets[1] if stereo else None), 1)
            out = Path(folder) / 'cycle.ptx'
            s.save(str(out))
            for _ in range(2):
                s = ProToolsSession(str(out))
                self.assertEqual(bytes(s.data), bytes(expected))
                self.assertEqual(pointer_rows(s), pointers)
                self.assertEqual(s.get_track_pan_lr(track_name), (targets[0], targets[1] if stereo else None))
                s.save(str(out))
            s.set_track_pan(track_name, *old)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), original_file)
        self.assertEqual(source.read_bytes(), source_file)

    def test_stereo_static_write_with_native_mono_automation(self):
        self.check_native('native_pan_automation_mono_after.ptx', 'C_STEREO', 1, True)

    def test_mono_static_composition_with_native_stereo_automation(self):
        self.check_native('native_pan_automation_stereo_after.ptx', 'A_OUTSIDE', 0,
                          False, prepare_static=True)


if __name__ == '__main__':
    unittest.main()
