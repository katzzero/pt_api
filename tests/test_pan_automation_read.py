"""Read-only mono Pan envelopes grounded in a native three-node comparison."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_pan_read import pan, selected
from tests.test_pan_stereo_read import stereo_pan
from tests.test_volume_read import read_session, state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_pan_automation_mono_before.ptx'
AFTER = FIXTURES / 'native_pan_automation_mono_after.ptx'
NODES = [(0, -50), (1729728000, -50), (1730208480, 50)]


class MonoPanAutomationReadTests(unittest.TestCase):
    def test_exact_samples_signed_values_fresh_result_and_live_tree(self):
        s = read_session(('PISTE É', 'SECOND'))
        lane = pan(s)
        lane.items[0] = payload(NODES)
        before = snapshot(s)
        rows = s.get_pan_automation('PISTE É')
        self.assertEqual(rows, NODES)
        self.assertTrue(all(type(ts) is int and type(value) is int for ts, value in rows))
        rows[0] = (0, 999)
        rows.append((100, 0))
        self.assertEqual(s.get_pan_automation('PISTE É'), NODES)
        self.assertEqual(snapshot(s), before)
        lane.items[0] = payload(((0, 100), (0xffffffff, -100)))
        self.assertEqual(s.get_pan_automation('PISTE É'), [(0, 100), (0xffffffff, -100)])
        self.assertIs(type(s.data), object)

    def test_static_lane_one_node_and_absence_not_invented_center(self):
        s = read_session()
        self.assertIsNone(s.get_pan_automation('FIRST'))
        for value in (-100, -50, 0, 50, 100):
            pan(s, value=value)
            self.assertEqual(s.get_pan_automation('FIRST'), [(0, value)])
        self.assertIsNone(s.get_pan_automation('SECOND'))

    def test_mono_after_stereo_uses_track_slots_and_stereo_witness_is_opaque(self):
        for widths in ((2, 1), (1, 2, 2, 1)):
            s, names = mixed_session(widths)
            for i, width in enumerate(widths):
                if width == 1: pan(s, i).items[0] = payload(NODES)
                else: state(s, i).items[8].items = [b'opaque stereo pan']
            before = snapshot(s)
            for i, width in enumerate(widths):
                if width == 1: self.assertEqual(s.get_pan_automation(names[i]), NODES)
                else:
                    with self.assertRaisesRegex(ValueError, 'stereo Pan automation'):
                        s.get_pan_automation(names[i])
            self.assertEqual(snapshot(s), before)

    def test_names_missing_and_right_argument_are_strict(self):
        s = read_session()
        before = snapshot(s)
        for name, error in ((None, TypeError), (True, TypeError), (1, TypeError),
                           ('', ValueError), ('X\x00Y', ValueError), ('\ud800', ValueError),
                           ('first', ValueError), ('MISSING', ValueError)):
            with self.assertRaises(error): s.get_pan_automation(name)
        for right in (None, 0, 1, 'False', []):
            with self.assertRaises(TypeError): s.get_pan_automation('FIRST', right=right)
        with self.assertRaisesRegex(ValueError, 'no right channel'):
            s.get_pan_automation('FIRST', right=True)
        self.assertEqual(snapshot(s), before)

    def test_empty_session_missing_track_is_not_silently_absent(self):
        s = ProToolsSession.__new__(ProToolsSession)
        s.root_items = []
        with self.assertRaisesRegex(ValueError, 'Track not found'):
            s.get_pan_automation('MISSING')

    def test_complete_header_counts_and_flags_validated_no_partial_return(self):
        for variant in ('short', 'truncated', 'extra', 'magic', 'size', 'count_zero',
                        'count_large', 'padding', 'flags', 'segments', 'reserved', 'terminator'):
            s = read_session()
            lane = pan(s)
            p = payload(NODES)
            if variant == 'short': p = p[:13]
            elif variant == 'truncated': p = p[:-6]
            elif variant == 'extra': p.extend(bytes(6))
            elif variant == 'magic': p[0] ^= 1
            elif variant == 'size': p[4] ^= 1
            elif variant == 'count_zero': struct.pack_into('<I', p, 10, 0)
            elif variant == 'count_large': struct.pack_into('<I', p, 10, 0xffffffff)
            elif variant == 'padding': p[8] = 1
            elif variant == 'flags': p[14] = 4
            elif variant == 'segments': p[16] ^= 1
            elif variant == 'reserved': p[20] = 1
            else: p[-1] = 1
            lane.items[0] = p
            before = snapshot(s)
            with self.subTest(variant=variant):
                with self.assertRaises(ValueError): s.get_pan_automation('FIRST')
                self.assertEqual(snapshot(s), before)

    def test_initial_timestamp_order_duplicates_and_all_value_limits_checked(self):
        for nodes in (((1, 0),), ((0, 0), (100, 50), (100, -50)),
                      ((0, 0), (100, 50), (50, -50)),
                      ((0, 0), (100, 50), (200, 101)),
                      ((0, -101), (100, 0)), ((0, 32767),), ((0, -32768),)):
            s = read_session()
            pan(s).items[0] = payload(nodes)
            before = snapshot(s)
            with self.assertRaises(ValueError): s.get_pan_automation('FIRST')
            self.assertEqual(snapshot(s), before)

    def test_container_ownership_geometry_and_second_mono_container_are_strict(self):
        for variant in ('selector', 'suffix', 'extra', 'duplicate', 'lane_type', 'lane_kind',
                        'split_payload', 'nonraw', 'second_populated', 'absent_wrong_size'):
            s = read_session()
            lane = pan(s)
            a, b = state(s).items[8:10]
            if variant == 'selector': a.items[0] = b'\x02'
            elif variant == 'suffix': a.items[2] = bytes(12) + b'\x01'
            elif variant == 'extra': a.items.append(b'\x00')
            elif variant == 'duplicate': a.items.insert(2, copy.deepcopy(lane))
            elif variant == 'lane_type': lane.block_type = 2
            elif variant == 'lane_kind': lane.content_type = 0x260b
            elif variant == 'split_payload': lane.items.append(b'\x00')
            elif variant == 'nonraw': lane.items[0] = object()
            elif variant == 'second_populated': b.items = copy.deepcopy(a.items)
            else: a.items = [bytes(13)]
            with self.subTest(variant=variant):
                with self.assertRaises(ValueError): s.get_pan_automation('FIRST')

    def test_other_mono_state_volume_identity_or_name_invalid_blocks_filtered_result(self):
        for variant in ('other_pan', 'volume', 'identity', 'state', 'name'):
            s = read_session()
            pan(s).items[0] = payload(NODES)
            if variant == 'other_pan': pan(s, 1).items[0][-1] = 1
            elif variant == 'volume': state(s, 1).items[4].items[0][0] ^= 1
            elif variant == 'identity': s._root_blocks(0x2107)[0].get_all_blocks(0x210b)[0].items[0][-24] ^= 1
            elif variant == 'state': state(s, 1).items.pop()
            else: s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[0].items[0][6] ^= 1
            before = snapshot(s)
            with self.assertRaises(ValueError): s.get_pan_automation('FIRST')
            self.assertEqual(snapshot(s), before)

    def test_static_methods_keep_refusal_of_automation_even_after_read(self):
        s, names = mixed_session((1, 2))
        pan(s).items[0] = payload(NODES)
        stereo_pan(s, 1)
        before = snapshot(s)
        self.assertEqual(s.get_pan_automation(names[0]), NODES)
        for method, args in ((s.get_track_pan, (names[0],)),
                             (s.get_track_pan_lr, (names[0],)),
                             (s.set_track_pan, (names[0], -50))):
            with self.assertRaises(ValueError): method(*args)
            self.assertEqual(snapshot(s), before)
        # A curve on the other track no longer blocks a static target.
        self.assertEqual(s.set_track_pan(names[1], -50, 50), 1)
        self.assertEqual(s.get_pan_automation(names[0]), NODES)


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'Local native mono Pan automation pair absent')
class NativeMonoPanAutomationReadTests(unittest.TestCase):
    def test_native_pair_exact_nodes_and_unchanged_audio(self):
        a, b = [ProToolsSession(str(p)) for p in (BEFORE, AFTER)]
        self.assertEqual(a.get_pan_automation('A_OUTSIDE'), [(0, -50)])
        self.assertEqual(b.get_pan_automation('A_OUTSIDE'), NODES)
        self.assertIsNone(b.get_pan_automation('B_INSIDE'))
        for method in ('get_tracks', 'get_clips', 'get_timeline_clips', 'get_volume_automation'):
            self.assertEqual(getattr(a, method)(), getattr(b, method)())
        for kind in (0x260e, 0x2602, 0x1015, 0x1001, 0x2628, 0x2637):
            self.assertEqual(selected(a, kind), selected(b, kind))
        self.assertEqual(b.get_pan_automation('C_STEREO'), [(0, -50)])
        self.assertEqual(b.get_pan_automation('C_STEREO', right=True), [(0, 50)])

    def test_reads_are_nonmutating_and_native_cycles_are_exact(self):
        for path in (BEFORE, AFTER):
            s = ProToolsSession(str(path))
            original = path.read_bytes()
            before = snapshot(s)
            pointers = pointer_rows(s)
            raw = state(s).items[8].items[1].items[0]
            s.data = object()
            for _ in range(2): s.get_pan_automation('A_OUTSIDE')
            self.assertEqual(snapshot(s), before)
            self.assertEqual(pointer_rows(s), pointers)
            self.assertIs(state(s).items[8].items[1].items[0], raw)
            s = ProToolsSession(str(path))
            with tempfile.TemporaryDirectory() as folder:
                out = Path(folder) / 'cycle.ptx'
                for _ in range(2):
                    s.get_pan_automation('A_OUTSIDE')
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), original)
                    s = ProToolsSession(str(out))

    def test_automation_never_collapsed_by_static_writer(self):
        s = ProToolsSession(str(AFTER))
        before = snapshot(s)
        with self.assertRaises(ValueError): s.get_track_pan('A_OUTSIDE')
        with self.assertRaises(ValueError): s.set_track_pan('A_OUTSIDE', -50)
        self.assertEqual(snapshot(s), before)
        self.assertEqual(s.get_pan_automation('A_OUTSIDE'), NODES)


if __name__ == '__main__':
    unittest.main()
