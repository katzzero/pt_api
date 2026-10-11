"""Transactional mono Pan replacement, with native opening/save validation."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_pan_read import pan, selected
from tests.test_pan_automation_read import BEFORE, AFTER, NODES
from tests.test_volume_read import read_session, state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_to_stereo_bus_output import pointer_rows

RESAVED = Path(__file__).parent / 'fixtures' / 'native_pan_automation_mono_write_pt_resaved.ptx'


class MonoPanAutomationWriteTests(unittest.TestCase):
    def test_sorted_replacement_changes_only_target_payload_and_preserves_metadata(self):
        s = read_session(('PISTE É', 'SECOND'))
        lane = pan(s, value=-50)
        reference = copy.deepcopy(s)
        state(reference).items[8].items[1].items[0] = payload(NODES)
        lane.original_offset = 1234
        s._removed_offsets = [99]
        s._removed_block_types = {99: 0x2077}
        old_ui = state(s).items[0]
        offsets, types = s._removed_offsets, s._removed_block_types
        self.assertEqual(s.set_pan_automation('PISTE É', reversed(NODES)), 3)
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertIs(state(s).items[8].items[1], lane)
        self.assertEqual(lane.original_offset, 1234)
        self.assertIs(state(s).items[0], old_ui)
        self.assertIs(s._removed_offsets, offsets)
        self.assertIs(s._removed_block_types, types)
        self.assertEqual(s.get_pan_automation('PISTE É'), NODES)
        self.assertIs(type(s.data), object)

    def test_identical_input_preserves_payload_object_and_compositions_restore_tree(self):
        s = read_session()
        lane = pan(s, value=-50)
        before = snapshot(s)
        old = lane.items[0]
        self.assertEqual(s.set_pan_automation('FIRST', [(0, -50)]), 1)
        self.assertIs(lane.items[0], old)
        for nodes in (NODES, [(0, 100), (0xffffffff, -100)], [(0, -50)]):
            self.assertEqual(s.set_pan_automation('FIRST', nodes), len(nodes))
        self.assertEqual(snapshot(s), before)

    def test_invalid_nodes_are_rejected_atomically_including_late_errors(self):
        cases = [(None, TypeError), (False, TypeError), ([], ValueError),
                 (['00'], TypeError), ([(0,)], TypeError), ([(0, 0, 0)], TypeError),
                 ([(0, 0), (True, 50)], TypeError), ([(0, 0), (1.0, 50)], TypeError),
                 ([(0, 0), (-1, 50)], ValueError), ([(0, 0), (2**32, 50)], ValueError),
                 ([(0, 0), (0, 50)], ValueError), ([(0, True)], TypeError),
                 ([(0, '50')], TypeError), ([(0, 50.0)], TypeError),
                 ([(0, float('nan'))], TypeError), ([(0, float('inf'))], TypeError),
                 ([(0, 101)], ValueError), ([(0, -101)], ValueError),
                 ([(1, 0)], ValueError), ([(0, 0), (100, 2**100)], ValueError)]
        for nodes, error in cases:
            s = read_session()
            pan(s)
            before = snapshot(s)
            with self.subTest(nodes=nodes):
                with self.assertRaises(error): s.set_pan_automation('FIRST', nodes)
                self.assertEqual(snapshot(s), before)

    def test_generator_exception_and_preparation_exception_leave_tree_unchanged(self):
        s = read_session()
        pan(s)
        before = snapshot(s)
        def broken():
            yield (0, -50)
            raise RuntimeError('input interrupted')
        with self.assertRaisesRegex(RuntimeError, 'input interrupted'):
            s.set_pan_automation('FIRST', broken())
        with mock.patch('pt_api.struct.pack', side_effect=RuntimeError('encoding failed')):
            with self.assertRaisesRegex(RuntimeError, 'encoding failed'):
                s.set_pan_automation('FIRST', NODES)
        self.assertEqual(snapshot(s), before)

    def test_names_and_right_argument_are_strict(self):
        s = read_session()
        pan(s)
        before = snapshot(s)
        for name, error in ((None, TypeError), (1, TypeError), ('', ValueError),
                            ('A\x00B', ValueError), ('\ud800', ValueError), ('MISSING', ValueError)):
            with self.assertRaises(error): s.set_pan_automation(name, NODES)
        for right in (None, 0, 1, 'False'):
            with self.assertRaises(TypeError): s.set_pan_automation('FIRST', NODES, right=right)
        with self.assertRaisesRegex(ValueError, 'no right channel'):
            s.set_pan_automation('FIRST', NODES, right=True)
        self.assertEqual(snapshot(s), before)

    def test_absent_lane_is_never_created_and_unknown_stereo_is_not_targeted(self):
        s, names = mixed_session((1, 2, 1))
        pan(s, 0)
        before = snapshot(s)
        for name in names[1:]:
            with self.assertRaises(ValueError): s.set_pan_automation(name, NODES)
            self.assertEqual(snapshot(s), before)

    def test_other_mono_or_volume_or_identity_invalid_prevents_even_noop(self):
        for variant in ('other_pan', 'target_pan', 'volume', 'identity', 'state'):
            s = read_session()
            lane = pan(s)
            if variant == 'other_pan': pan(s, 1).items[0][-1] = 1
            elif variant == 'target_pan': lane.items[0][-1] = 1
            elif variant == 'volume': state(s, 1).items[4].items[0][0] ^= 1
            elif variant == 'identity': s._root_blocks(0x2107)[0].get_all_blocks(0x210b)[0].items[0][-24] ^= 1
            else: state(s, 1).items.pop()
            before = snapshot(s)
            with self.assertRaises(ValueError): s.set_pan_automation('FIRST', [(0, 0)])
            self.assertEqual(snapshot(s), before)

    def test_live_mono_slot_after_stereo_preserves_opaque_witnesses(self):
        s, names = mixed_session((1, 2, 2, 1))
        for i in (0, 3): pan(s, i)
        for i in (1, 2): state(s, i).items[8].items = [b'opaque stereo witness']
        expected = copy.deepcopy(s)
        state(expected, 3).items[8].items[1].items[0] = payload(NODES)
        self.assertEqual(s.set_pan_automation(names[3], NODES), 3)
        self.assertEqual(snapshot(s), snapshot(expected))

    def test_verified_schema_variable_node_count_and_boundaries(self):
        s = read_session()
        pan(s)
        for count in (1, 2, 3, 4, 25):
            nodes = [(i * 100, -100 if i % 2 else 100) for i in range(count)]
            self.assertEqual(s.set_pan_automation('FIRST', reversed(nodes)), count)
            self.assertEqual(s.get_pan_automation('FIRST'), nodes)
            self.assertEqual(state(s).items[8].items[1].items[0], payload(nodes))

    def test_static_writer_still_refuses_created_curve_without_collapsing_it(self):
        s = read_session()
        pan(s)
        s.set_pan_automation('FIRST', NODES)
        before = snapshot(s)
        with self.assertRaises(ValueError): s.set_track_pan('FIRST', -50)
        self.assertEqual(snapshot(s), before)


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'Local native mono Pan pair absent')
class NativeMonoPanAutomationWriteTests(unittest.TestCase):
    def test_public_writer_reproduces_exact_native_envelope(self):
        s, native = [ProToolsSession(str(p)) for p in (BEFORE, AFTER)]
        self.assertEqual(s.set_pan_automation('A_OUTSIDE', NODES), 3)
        self.assertEqual(state(s).items[8].items[1].items[0], state(native).items[8].items[1].items[0])
        b = ProToolsSession(str(BEFORE))
        for kind in (0x1029, 0x260e, 0x2602, 0x1015, 0x1001, 0x2628, 0x2637):
            self.assertEqual(selected(s, kind), selected(b, kind))
        self.assertEqual(pointer_rows(s), pointer_rows(b))

    def test_public_writer_reproduces_opened_candidate_and_restores_source(self):
        s = ProToolsSession(str(BEFORE))
        self.assertEqual(s.set_pan_automation('A_OUTSIDE', [(0,-50),(1729728000,-50),(1730208480,25)]), 3)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            s.save(str(out))
            self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(),
                             '34422a3342446e4ac368cad25b42a7520e33ca0926af7ffd68e38f74eff598cb')
            candidate = out.read_bytes()
            for _ in range(2):
                s = ProToolsSession(str(out))
                s.save(str(out))
                self.assertEqual(out.read_bytes(), candidate)
            s.set_pan_automation('A_OUTSIDE', [(0,-50)])
            s.save(str(out))
            self.assertEqual(out.read_bytes(), BEFORE.read_bytes())


@unittest.skipUnless(RESAVED.exists(), 'Local mono Pan writer native save absent')
class ResavedMonoPanAutomationWriteTests(unittest.TestCase):
    def test_native_save_preserves_actual_edited_timestamp_and_exact_cycles(self):
        s = ProToolsSession(str(RESAVED))
        original = RESAVED.read_bytes()
        nodes = [(0,-50),(1729730002,-50),(1730208480,25)]
        self.assertEqual(s.get_pan_automation('A_OUTSIDE'), nodes)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for _ in range(2):
                raw = state(s).items[8].items[1].items[0]
                self.assertEqual(s.set_pan_automation('A_OUTSIDE', nodes), 3)
                self.assertIs(state(s).items[8].items[1].items[0], raw)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
            for changed in ([(0,100),(0xffffffff,-100)], [(0,-50)], nodes):
                s.set_pan_automation('A_OUTSIDE', changed)
                s.save(str(out))
                s = ProToolsSession(str(out))
            self.assertEqual(out.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
