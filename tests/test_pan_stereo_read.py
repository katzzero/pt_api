"""Strict live-tree static L/R Pan reading, grounded in a native left edit."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_get_tracks import block
from tests.test_pan_read import pan, selected, snapshot_root_pans
from tests.test_volume_read import read_session, state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_bus_output import CENTER, pan_containers
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_pan_static_stereo_before.ptx'
AFTER = FIXTURES / 'native_pan_static_stereo_after.ptx'


def stereo_pan(s, index, left=-100, right=100):
    lanes = []
    for container, value in zip(state(s, index).items[8:10], (left, right)):
        raw = bytearray(CENTER)
        struct.pack_into('<h', raw, 26, value)
        lane = block(1, 0x260a, [raw])
        container.items = [b'\x01', lane, bytes(13)]
        lanes.append(lane)
    return lanes


class StereoPanReadTests(unittest.TestCase):
    def test_exact_pairs_and_live_values_without_source_cache(self):
        s, names = mixed_session((1, 2, 1))
        pan(s, 0, -50)
        left, right = stereo_pan(s, 1)
        for values in ((-100, 100), (-50, 100), (0, 0), (100, -100), (-25, 75)):
            for lane, value in zip((left, right), values):
                struct.pack_into('<h', lane.items[0], 26, value)
            before = snapshot(s)
            self.assertEqual(s.get_track_pan_lr(names[1]), values)
            self.assertEqual(s.get_track_pan_lr(names[0]), (-50, None))
            self.assertEqual(s.get_track_pan_lr(names[2]), (None, None))
            self.assertIs(type(s.get_track_pan_lr(names[1])), tuple)
            self.assertTrue(all(type(v) is int for v in s.get_track_pan_lr(names[1])))
            self.assertEqual(snapshot(s), before)
        self.assertIs(type(s.data), object)

    def test_stereo_first_last_adjacent_and_mono_after_use_track_slots(self):
        for widths in ((2,), (2, 1), (1, 2), (1, 2, 1), (2, 2), (1, 2, 2, 1)):
            s, names = mixed_session(widths)
            expected = []
            for i, width in enumerate(widths):
                if width == 2:
                    values = (-100 + i, 100 - i)
                    stereo_pan(s, i, *values)
                else:
                    pan(s, i, -20 + i)
                    values = (-20 + i, None)
                expected.append(values)
            before = snapshot(s)
            self.assertEqual([s.get_track_pan_lr(name) for name in names], expected)
            self.assertEqual(snapshot(s), before)

    def test_invalid_names_missing_and_empty_session_raise_without_mutation(self):
        s, names = mixed_session((1, 2))
        stereo_pan(s, 1)
        before = snapshot(s)
        for name, error in ((None, TypeError), (False, TypeError), (1, TypeError),
                ('', ValueError), ('X\x00Y', ValueError), ('\ud800', ValueError),
                (names[1].lower(), ValueError), ('ABSENT', ValueError)):
            with self.subTest(name=repr(name)):
                with self.assertRaises(error): s.get_track_pan_lr(name)
                self.assertEqual(snapshot(s), before)
        empty = ProToolsSession.__new__(ProToolsSession)
        empty.root_items = []
        with self.assertRaisesRegex(ValueError, 'Track not found'):
            empty.get_track_pan_lr('ABSENT')

    def test_either_channel_unknown_container_or_payload_is_rejected(self):
        for channel in (0, 1):
            for variant in ('selector', 'suffix', 'extra', 'duplicate', 'nested',
                    'lane_type', 'kind', 'nonraw', 'split_payload', 'short', 'long',
                    'header', 'timestamp', 'terminator', 'low', 'high'):
                s, names = mixed_session((2,))
                lanes = stereo_pan(s, 0)
                c = state(s).items[8 + channel]
                lane = lanes[channel]
                p = lane.items[0]
                if variant == 'selector': c.items[0] = b'\x02'
                elif variant == 'suffix': c.items[2] = bytes(12) + b'\x01'
                elif variant == 'extra': c.items.append(b'\x00')
                elif variant == 'duplicate': c.items.insert(2, copy.deepcopy(lane))
                elif variant == 'nested': c.items[1] = block(1, 0x200b, [lane])
                elif variant == 'lane_type': lane.block_type = 2
                elif variant == 'kind': lane.content_type = 0x260b
                elif variant == 'nonraw': lane.items[0] = block(1, 0x260a, [])
                elif variant == 'split_payload': lane.items = [p[:20], p[20:]]
                elif variant == 'short': lane.items[0] = p[:-1]
                elif variant == 'long': lane.items[0] = p + b'\x00'
                elif variant == 'header': p[14] = 4
                elif variant == 'timestamp': struct.pack_into('<I', p, 22, 100)
                elif variant == 'terminator': p[-1] = 1
                elif variant == 'low': struct.pack_into('<h', p, 26, -101)
                else: struct.pack_into('<h', p, 26, 101)
                before = snapshot(s)
                with self.subTest(channel=channel, variant=variant):
                    with self.assertRaises(ValueError): s.get_track_pan_lr(names[0])
                    self.assertEqual(snapshot(s), before)

    def test_automation_in_either_channel_is_not_read_as_static(self):
        for channel in (0, 1):
            s, names = mixed_session((2,))
            stereo_pan(s, 0)[channel].items[0] = payload(((0, -100), (100, -50)))
            before = snapshot(s)
            with self.assertRaisesRegex(ValueError, 'static pan'):
                s.get_track_pan_lr(names[0])
            self.assertEqual(snapshot(s), before)

    def test_malformed_other_tracks_and_volume_reject_before_filtered_result(self):
        for variant in ('other_stereo', 'other_mono', 'volume', 'identity', 'state'):
            s, names = mixed_session((1, 2, 1))
            pan(s, 0, -50)
            lanes = stereo_pan(s, 1)
            if variant == 'other_stereo': lanes[1].items[0][-1] = 1
            elif variant == 'other_mono': pan(s, 2).items[0][22] = 1
            elif variant == 'volume': state(s, 2).items[4].items[0][0] ^= 1
            elif variant == 'identity': s._root_blocks(0x2107)[0].get_all_blocks(0x210b)[0].items[0][-24] ^= 1
            else: state(s, 2).items.pop()
            before = snapshot(s)
            with self.assertRaises(ValueError): s.get_track_pan_lr(names[0])
            self.assertEqual(snapshot(s), before)

    def test_existing_mono_reader_writer_scope_is_not_silently_broadened(self):
        s, names = mixed_session((1, 2))
        pan(s, 0, -50)
        stereo_pan(s, 1)[1].items[0][-1] = 1
        self.assertEqual(s.get_track_pan(names[0]), -50)
        self.assertEqual(s.set_track_pan(names[0], -50), 0)
        with self.assertRaises(ValueError): s.get_track_pan_lr(names[0])
        with self.assertRaisesRegex(ValueError, 'mono'): s.get_track_pan(names[1])
        with self.assertRaisesRegex(ValueError, 'stereo'): s.set_track_pan(names[1], -50)
        before = snapshot(s)
        with self.assertRaisesRegex(ValueError, 'right'): s.set_track_pan(names[0], -50, 0)
        self.assertEqual(snapshot(s), before)

    def test_empty_stereo_containers_are_unverified_not_invented_default_pans(self):
        for channel in (0, 1, 2):
            s, names = mixed_session((2,))
            stereo_pan(s, 0)
            for index in ((channel,) if channel < 2 else (0, 1)):
                state(s).items[8 + index].items = [bytes(14)]
            before = snapshot(s)
            with self.assertRaises(ValueError): s.get_track_pan_lr(names[0])
            self.assertEqual(snapshot(s), before)
        mono = read_session()
        self.assertEqual(mono.get_track_pan_lr('FIRST'), (None, None))


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'Local native static stereo Pan pair absent')
class NativeStereoPanReadTests(unittest.TestCase):
    def test_pair_corroborates_left_only_edit_and_readonly_results(self):
        a, b = (ProToolsSession(str(p)) for p in (BEFORE, AFTER))
        for session, expected in ((a, (-100, 100)), (b, (-50, 100))):
            before = snapshot(session)
            self.assertEqual(session.get_track_pan_lr('C_STEREO'), expected)
            self.assertEqual(session.get_track_pan_lr('A_OUTSIDE'), (-50, None))
            self.assertEqual(session.get_track_pan_lr('B_INSIDE'), (None, None))
            self.assertEqual(snapshot(session), before)
            with self.assertRaises(ValueError): session.get_track_pan('C_STEREO')
        self.assertEqual(snapshot_root_pans(a, 0), snapshot_root_pans(b, 0))
        self.assertEqual(snapshot_root_pans(a, 2), snapshot_root_pans(b, 2))
        self.assertEqual(pan_containers(a, 1)[1].to_bytes()[0], pan_containers(b, 1)[1].to_bytes()[0])
        for method in ('get_tracks', 'get_clips', 'get_timeline_clips', 'get_volume_automation'):
            self.assertEqual(getattr(a, method)(), getattr(b, method)())
        self.assertEqual(pointer_rows(a), pointer_rows(b))
        ui_a, ui_b = selected(a, 0x1029), selected(b, 0x1029)
        expected_ui = bytearray(ui_a[1][2][0])
        self.assertEqual(len(expected_ui), 279)
        for offset in (13, 99):
            self.assertEqual(struct.unpack_from('<i', expected_ui, offset)[0], -100)
            struct.pack_into('<i', expected_ui, offset, -50)
        self.assertEqual(ui_b[1][2][0], bytes(expected_ui))
        self.assertEqual(ui_a[0], ui_b[0])
        self.assertEqual(ui_a[2], ui_b[2])
        for kind in (0x260e, 0x2602, 0x1001, 0x2628, 0x2637):
            self.assertEqual(selected(a, kind), selected(b, kind))

    def test_reading_originals_and_four_cycles_preserve_every_byte(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for source, expected in ((BEFORE, (-100, 100)), (AFTER, (-50, 100))):
                original = source.read_bytes()
                s = ProToolsSession(str(source))
                for _ in range(2):
                    self.assertEqual(s.get_track_pan_lr('C_STEREO'), expected)
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), original)
                    s = ProToolsSession(str(out))
                s.data = object()
                before = snapshot(s)
                self.assertEqual(s.get_track_pan_lr('C_STEREO'), expected)
                self.assertEqual(snapshot(s), before)
                self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
