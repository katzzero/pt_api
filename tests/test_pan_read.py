"""Static mono Pan reader, grounded in an isolated native L50 edit."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession, PTBlock
from tests.test_get_tracks import block
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_volume_read import read_session, state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_bus_output import CENTER, pan_containers
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_pan_static_mono_before.ptx'
AFTER = FIXTURES / 'native_pan_static_mono_after.ptx'


def pan(s, index=0, value=0):
    data = bytearray(CENTER)
    struct.pack_into('<h', data, 26, value)
    container = state(s, index).items[8]
    container.items = [b'\x01', block(1, 0x260a, [data]), bytes(13)]
    return container.items[1]


class MonoPanReadTests(unittest.TestCase):
    def test_exact_integer_values_live_tree_and_no_source_cache(self):
        s = read_session(('PISTE É', 'SECOND'))
        lane = pan(s)
        for value in (-100, -50, 0, 50, 100):
            struct.pack_into('<h', lane.items[0], 26, value)
            before = snapshot(s)
            self.assertEqual(s.get_track_pan('PISTE É'), value)
            self.assertIs(type(s.get_track_pan('PISTE É')), int)
            self.assertEqual(snapshot(s), before)
        self.assertIs(type(s.data), object)

    def test_absent_pan_is_none_not_center(self):
        s = read_session()
        before = snapshot(s)
        self.assertIsNone(s.get_track_pan('FIRST'))
        self.assertIsNone(s.get_track_pan('SECOND'))
        self.assertEqual(snapshot(s), before)

    def test_mixed_widths_use_track_slot_not_channel_playlist_ordinal(self):
        for widths in ((1, 2, 1), (2, 1), (1, 2, 2, 1)):
            s, names = mixed_session(widths)
            for i, width in enumerate(widths):
                if width == 1:
                    pan(s, i, -50 + i)
                else:
                    # Stereo Pan remains out of this reader's verified scope.
                    pan(s, i, -100)
                    state(s, i).items[9].items = [b'opaque stereo witness']
            before = snapshot(s)
            for i, width in enumerate(widths):
                if width == 1:
                    self.assertEqual(s.get_track_pan(names[i]), -50 + i)
                else:
                    with self.assertRaisesRegex(ValueError, 'mono'):
                        s.get_track_pan(names[i])
            self.assertEqual(snapshot(s), before)

    def test_names_types_missing_and_empty_session_are_rejected(self):
        s = read_session()
        before = snapshot(s)
        for name, error in ((None, TypeError), (False, TypeError), (1, TypeError),
                            ('', ValueError), ('X\x00Y', ValueError),
                            ('\ud800', ValueError), ('first', ValueError), ('MISSING', ValueError)):
            with self.subTest(name=repr(name)):
                with self.assertRaises(error):
                    s.get_track_pan(name)
                self.assertEqual(snapshot(s), before)
        empty = ProToolsSession.__new__(ProToolsSession)
        empty.root_items = []
        with self.assertRaisesRegex(ValueError, 'Track not found'):
            empty.get_track_pan('MISSING')

    def test_unknown_mono_container_geometry_or_lane_is_rejected_without_mutation(self):
        for variant in ('selector', 'suffix', 'extra', 'duplicate_lane', 'nested_lane',
                        'lane_type', 'lane_kind', 'extra_payload', 'nonraw',
                        'second_populated', 'empty_first_wrong_size', 'second_suffix'):
            s = read_session()
            lane = pan(s)
            first, second = state(s).items[8:10]
            if variant == 'selector': first.items[0] = b'\x02'
            elif variant == 'suffix': first.items[2] = bytes(12) + b'\x01'
            elif variant == 'extra': first.items.append(b'\x00')
            elif variant == 'duplicate_lane': first.items.insert(2, copy.deepcopy(lane))
            elif variant == 'nested_lane': first.items[1] = block(1, 0x200b, [lane])
            elif variant == 'lane_type': lane.block_type = 2
            elif variant == 'lane_kind': lane.content_type = 0x260b
            elif variant == 'extra_payload': lane.items.append(b'\x00')
            elif variant == 'nonraw': lane.items[0] = block(1, 0x260a, [])
            elif variant == 'second_populated': second.items = copy.deepcopy(first.items)
            elif variant == 'empty_first_wrong_size': first.items = [bytes(13)]
            else: second.items = [bytes(13) + b'\x01']
            before = snapshot(s)
            with self.assertRaises(ValueError): s.get_track_pan('FIRST')
            self.assertEqual(snapshot(s), before)

    def test_static_payload_requires_exact_header_zero_timestamp_and_terminator(self):
        for variant in ('short', 'long', 'magic', 'size', 'padding', 'count', 'flags',
                        'segments', 'reserved', 'timestamp', 'terminator', 'low', 'high', 'curve'):
            s = read_session()
            lane = pan(s)
            p = lane.items[0]
            if variant == 'short': lane.items[0] = p[:-1]
            elif variant == 'long': lane.items[0] = p + b'\x00'
            elif variant == 'magic': p[0] ^= 1
            elif variant == 'size': struct.pack_into('<I', p, 4, 999)
            elif variant == 'padding': p[8] = 1
            elif variant == 'count': struct.pack_into('<I', p, 10, 2)
            elif variant == 'flags': p[14] = 4
            elif variant == 'segments': struct.pack_into('<I', p, 16, 1)
            elif variant == 'reserved': p[20] = 1
            elif variant == 'timestamp': struct.pack_into('<I', p, 22, 100)
            elif variant == 'terminator': p[-1] = 1
            elif variant == 'low': struct.pack_into('<h', p, 26, -101)
            elif variant == 'high': struct.pack_into('<h', p, 26, 101)
            else: lane.items[0] = payload(((0, 0), (100, -50)))
            before = snapshot(s)
            with self.assertRaisesRegex(ValueError, 'static pan'): s.get_track_pan('FIRST')
            self.assertEqual(snapshot(s), before)

    def test_late_malformed_other_mono_pan_does_not_return_partial_result(self):
        s = read_session()
        pan(s, 0, -50)
        pan(s, 1, 50).items[0][-1] = 1
        before = snapshot(s)
        with self.assertRaises(ValueError): s.get_track_pan('FIRST')
        self.assertEqual(snapshot(s), before)

    def test_existing_identity_state_and_volume_guards_are_not_bypassed(self):
        for variant in ('identity', 'width', 'name', 'state', 'volume', 'duplicate_root'):
            s = read_session()
            pan(s)
            if variant == 'identity': s._root_blocks(0x2107)[0].get_all_blocks(0x210b)[0].items[0][-24] ^= 1
            elif variant == 'width': s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[0].items[0][0] = 1
            elif variant == 'name': s._root_blocks(0x1054)[0].items[1].items[0][4] ^= 1
            elif variant == 'state': state(s).items.pop()
            elif variant == 'volume': state(s, 1).items[4].items[0][0] ^= 1
            else: s.root_items.append(copy.deepcopy(s._root_blocks(0x2624)[0]))
            before = snapshot(s)
            with self.assertRaises(ValueError): s.get_track_pan('FIRST')
            self.assertEqual(snapshot(s), before)


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'Local native mono static-pan pair absent')
class NativeMonoPanReadTests(unittest.TestCase):
    def test_native_edit_confirms_sign_and_only_target_pan_lane_changes(self):
        a, b = (ProToolsSession(str(path)) for path in (BEFORE, AFTER))
        self.assertEqual(a.get_track_pan('A_OUTSIDE'), 0)
        self.assertEqual(b.get_track_pan('A_OUTSIDE'), -50)
        self.assertIsNone(b.get_track_pan('B_INSIDE'))
        with self.assertRaises(ValueError): b.get_track_pan('C_STEREO')
        left_a, left_b = pan_containers(a, 0)[0], pan_containers(b, 0)[0]
        expected = bytearray(left_a.items[1].items[0])
        struct.pack_into('<h', expected, 26, -50)
        self.assertEqual(bytes(left_b.items[1].items[0]), bytes(expected))
        # Two native UI-state mirrors also change, but are not a reader source.
        ui_a = selected(a, 0x1029)
        ui_b = selected(b, 0x1029)
        expected_ui = bytearray(ui_a[0][2][0])
        self.assertEqual(len(expected_ui), 279)
        for offset in (13, 99):
            struct.pack_into('<i', expected_ui, offset, -50)
        self.assertEqual(ui_b[0][2][0], bytes(expected_ui))
        self.assertEqual(ui_a[1:], ui_b[1:])
        self.assertEqual(snapshot_root_pans(a, 1), snapshot_root_pans(b, 1))
        self.assertEqual(snapshot_root_pans(a, 2), snapshot_root_pans(b, 2))
        self.assertEqual(a.get_clips(), b.get_clips())
        self.assertEqual(a.get_tracks(), b.get_tracks())
        self.assertEqual(a.get_timeline_clips(), b.get_timeline_clips())
        self.assertEqual(a.get_volume_automation(), b.get_volume_automation())
        # The routing public reader remains mono-only; this pair is mixed.
        self.assertEqual(selected(a, 0x260e), selected(b, 0x260e))
        self.assertEqual(selected(a, 0x2602), selected(b, 0x2602))
        self.assertEqual(selected(a, 0x1015), selected(b, 0x1015))
        self.assertEqual(selected(a, 0x1001), selected(b, 0x1001))
        self.assertEqual(pointer_rows(a), pointer_rows(b))

    def test_native_reads_and_two_api_cycles_per_original_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for source, value in ((BEFORE, 0), (AFTER, -50)):
                s = ProToolsSession(str(source))
                original = source.read_bytes()
                before = snapshot(s)
                for _ in range(2):
                    self.assertEqual(s.get_track_pan('A_OUTSIDE'), value)
                    self.assertIsNone(s.get_track_pan('B_INSIDE'))
                    self.assertEqual(snapshot(s), before)
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), original)
                    s = ProToolsSession(str(out))
                    before = snapshot(s)
                s.data = object()
                self.assertEqual(s.get_track_pan('A_OUTSIDE'), value)
                self.assertEqual(source.read_bytes(), original)


def snapshot_root_pans(s, index):
    def shape(item):
        if isinstance(item, PTBlock):
            return item.block_type, item.content_type, tuple(shape(x) for x in item.items)
        return bytes(item)
    return tuple(shape(x) for x in pan_containers(s, index))


def selected(s, kind):
    def shape(item):
        if isinstance(item, PTBlock):
            return item.block_type, item.content_type, tuple(shape(x) for x in item.items)
        return bytes(item)
    def walk(items):
        for item in items:
            if isinstance(item, PTBlock):
                yield item
                yield from walk(item.items)
    return [shape(x) for x in walk(s._content_root_items()) if x.content_type == kind]


if __name__ == '__main__':
    unittest.main()
