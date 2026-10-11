"""Stereo envelope writing validated by an exact native-profile candidate."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_pan_read import pan, selected
from tests.test_pan_stereo_read import stereo_pan
from tests.test_pan_automation_stereo_read import BEFORE
from tests.test_volume_read import state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_to_stereo_bus_output import pointer_rows

LEFT = [(0, -25), (1729728000, -25), (1730208480, 25)]
RIGHT = [(0, 25), (1729728000, 25), (1730448720, -25)]
RESAVED = Path(__file__).parent / 'fixtures' / 'native_pan_automation_stereo_write_pt_resaved.ptx'


class StereoPanAutomationWriteTests(unittest.TestCase):
    def test_each_channel_changes_only_selected_payload(self):
        for right, nodes in ((False, LEFT), (True, RIGHT)):
            s, names = mixed_session((1, 2, 2, 1))
            for i in (0, 3): pan(s, i)
            for i in (1, 2): stereo_pan(s, i)
            expected = copy.deepcopy(s)
            state(expected, 1).items[8 + int(right)].items[1].items[0] = payload(nodes)
            lane = state(s, 1).items[8 + int(right)].items[1]
            lane.original_offset = 1234
            self.assertEqual(s.set_pan_automation(names[1], reversed(nodes), right=right), 3)
            self.assertEqual(snapshot(s), snapshot(expected))
            self.assertIs(state(s, 1).items[8 + int(right)].items[1], lane)
            self.assertEqual(lane.original_offset, 1234)
            self.assertEqual(s.get_pan_automation(names[1], right=right), nodes)

    def test_noop_payload_identity_compositions_and_variable_counts(self):
        s, names = mixed_session((2,))
        lanes = stereo_pan(s, 0)
        before = snapshot(s)
        for right, initial in ((False, -100), (True, 100)):
            raw = lanes[int(right)].items[0]
            self.assertEqual(s.set_pan_automation(names[0], [(0, initial)], right=right), 1)
            self.assertIs(lanes[int(right)].items[0], raw)
            for count in (2, 3, 4, 25):
                nodes = [(i*100, -100 if i % 2 else 100) for i in range(count)]
                self.assertEqual(s.set_pan_automation(names[0], reversed(nodes), right=right), count)
                self.assertEqual(lanes[int(right)].items[0], payload(nodes))
            s.set_pan_automation(names[0], [(0, 100), (0xffffffff, -100)], right=right)
            s.set_pan_automation(names[0], [(0, initial)], right=right)
        self.assertEqual(snapshot(s), before)

    def test_selected_unselected_other_stereo_mono_volume_corruption_atomic(self):
        for variant in ('left', 'right', 'other_stereo', 'mono', 'volume'):
            s, names = mixed_session((2, 2, 1))
            lanes = stereo_pan(s, 0)
            other = stereo_pan(s, 1)
            pan(s, 2)
            if variant == 'left': lanes[0].items[0][-1] = 1
            elif variant == 'right': lanes[1].items[0][-1] = 1
            elif variant == 'other_stereo': other[1].items[0][-1] = 1
            elif variant == 'mono': state(s, 2).items[8].items[1].items[0][-1] = 1
            else: state(s, 2).items[4].items[0][0] ^= 1
            before = snapshot(s)
            for right in (False, True):
                with self.assertRaises(ValueError): s.set_pan_automation(names[0], RIGHT, right=right)
                self.assertEqual(snapshot(s), before)

    def test_late_invalid_input_and_encoding_failure_are_atomic(self):
        s, names = mixed_session((2,))
        stereo_pan(s, 0)
        before = snapshot(s)
        for right in (False, True):
            for nodes, error in (([], ValueError), ([(0, 0), (1, 101)], ValueError),
                                 ([(0, 0), (0, 25)], ValueError), ([(1, 0)], ValueError),
                                 ([(0, 0), (True, 25)], TypeError),
                                 ([(0, 0), (2**32, 25)], ValueError)):
                with self.assertRaises(error): s.set_pan_automation(names[0], nodes, right=right)
                self.assertEqual(snapshot(s), before)
            def broken():
                yield (0, 0)
                raise RuntimeError('interrupted')
            with self.assertRaises(RuntimeError): s.set_pan_automation(names[0], broken(), right=right)
            with mock.patch('pt_api.struct.pack', side_effect=RuntimeError('encoding')):
                with self.assertRaises(RuntimeError): s.set_pan_automation(names[0], RIGHT, right=right)
            self.assertEqual(snapshot(s), before)

    def test_unknown_stereo_lane_never_created_and_right_strict(self):
        s, names = mixed_session((2, 1))
        stereo_pan(s, 0)
        pan(s, 1)
        before = snapshot(s)
        for right in (None, 0, 1, 'right'):
            with self.assertRaises(TypeError): s.set_pan_automation(names[0], RIGHT, right=right)
        with self.assertRaisesRegex(ValueError, 'no right channel'):
            s.set_pan_automation(names[1], RIGHT, right=True)
        self.assertEqual(snapshot(s), before)
        state(s).items[9].items = [bytes(14)]
        before = snapshot(s)
        with self.assertRaises(ValueError): s.set_pan_automation(names[0], LEFT)
        self.assertEqual(snapshot(s), before)


@unittest.skipUnless(BEFORE.exists(), 'Local stereo Pan automation before absent')
class NativeStereoPanAutomationWriteTests(unittest.TestCase):
    def test_reproduces_exact_opened_candidate_cycles_and_restores_source(self):
        s = ProToolsSession(str(BEFORE))
        original = ProToolsSession(str(BEFORE))
        s.set_pan_automation('C_STEREO', LEFT)
        s.set_pan_automation('C_STEREO', RIGHT, right=True)
        self.assertEqual(pointer_rows(s), pointer_rows(original))
        for kind in (0x1029, 0x260e, 0x2602, 0x1015, 0x1001, 0x2628, 0x2637):
            self.assertEqual(selected(s, kind), selected(original, kind))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'cycle.ptx'
            s.save(str(out))
            data = out.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),
                             '3cd1e1325fd23b6c16a635c78017a4b37998a900df5b7c7c862e9a2363ce3b3a')
            for _ in range(2):
                s = ProToolsSession(str(out))
                self.assertEqual(s.get_pan_automation('C_STEREO'), LEFT)
                self.assertEqual(s.get_pan_automation('C_STEREO', right=True), RIGHT)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), data)
            s.set_pan_automation('C_STEREO', [(0, -50)])
            s.set_pan_automation('C_STEREO', [(0, 50)], right=True)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), BEFORE.read_bytes())


@unittest.skipUnless(RESAVED.exists(), 'Local stereo Pan writer native save absent')
class ResavedStereoPanAutomationWriteTests(unittest.TestCase):
    def test_exact_positions_native_cycles_and_reversible_compositions(self):
        s = ProToolsSession(str(RESAVED))
        self.assertEqual(s.get_pan_automation('C_STEREO'), LEFT)
        self.assertEqual(s.get_pan_automation('C_STEREO', right=True), RIGHT)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'cycle.ptx'
            for _ in range(2):
                for right, nodes in ((False, LEFT), (True, RIGHT)):
                    raw = state(s, 1).items[8 + int(right)].items[1].items[0]
                    s.set_pan_automation('C_STEREO', nodes, right=right)
                    self.assertIs(state(s, 1).items[8 + int(right)].items[1].items[0], raw)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), RESAVED.read_bytes())
                s = ProToolsSession(str(out))
            for right, nodes in ((False, LEFT), (True, RIGHT)):
                for changed in ([(0, 100), (0xffffffff, -100)], [(0, 0)], nodes):
                    s.set_pan_automation('C_STEREO', changed, right=right)
                    s.save(str(out))
                    s = ProToolsSession(str(out))
            self.assertEqual(out.read_bytes(), RESAVED.read_bytes())


if __name__ == '__main__':
    unittest.main()
