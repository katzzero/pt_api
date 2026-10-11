"""Both stereo Pan channels, corroborated by distinct native curve timings."""
from pathlib import Path
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_pan_read import pan, selected
from tests.test_pan_stereo_read import stereo_pan
from tests.test_volume_read import state, payload
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_pan_automation_stereo_before.ptx'
AFTER = FIXTURES / 'native_pan_automation_stereo_after.ptx'
LEFT = [(0, -50), (1729728000, -49), (1730208480, 50)]
RIGHT = [(0, 50), (1729728000, 50), (1730448720, -50)]


class StereoPanAutomationReadTests(unittest.TestCase):
    def test_both_channels_fresh_values_live_tree_and_not_ui_or_source_cache(self):
        s, names = mixed_session((2,))
        left, right = stereo_pan(s, 0)
        left.items[0], right.items[0] = payload(LEFT), payload(RIGHT)
        before = snapshot(s)
        for flag, expected in ((False, LEFT), (True, RIGHT)):
            result = s.get_pan_automation(names[0], right=flag)
            self.assertEqual(result, expected)
            result[0] = (0, 999)
            self.assertEqual(s.get_pan_automation(names[0], right=flag), expected)
        self.assertEqual(snapshot(s), before)
        right.items[0] = payload(((0, 100), (0xffffffff, -100)))
        self.assertEqual(s.get_pan_automation(names[0], right=True), [(0, 100), (0xffffffff, -100)])
        self.assertIs(type(s.data), object)

    def test_stereo_first_last_adjacent_and_channel_playlist_indexes_not_slots(self):
        for widths in ((2, 1), (1, 2), (1, 2, 2, 1), (2, 2)):
            s, names = mixed_session(widths)
            for i, width in enumerate(widths):
                if width == 1: pan(s, i, -50)
                else:
                    left, right = stereo_pan(s, i)
                    left.items[0], right.items[0] = payload(LEFT), payload(RIGHT)
            for i, width in enumerate(widths):
                self.assertEqual(s.get_pan_automation(names[i]), LEFT if width == 2 else [(0, -50)])
                if width == 2: self.assertEqual(s.get_pan_automation(names[i], right=True), RIGHT)
                else:
                    with self.assertRaisesRegex(ValueError, 'no right channel'):
                        s.get_pan_automation(names[i], right=True)

    def test_mono_scope_remains_opaque_to_stereo_witnesses(self):
        s, names = mixed_session((1, 2))
        pan(s)
        stereo_pan(s, 1)[1].items[0][-1] = 1
        self.assertEqual(s.get_pan_automation(names[0]), [(0, 0)])
        self.assertEqual(s.set_pan_automation(names[0], [(0, 0)]), 1)
        with self.assertRaises(ValueError): s.get_pan_automation(names[1])
        with self.assertRaises(ValueError):
            s.set_pan_automation(names[1], LEFT)

    def test_other_stereo_or_mono_invalid_blocks_either_stereo_channel(self):
        for variant in ('target_left', 'target_right', 'other_stereo', 'other_mono', 'volume'):
            s, names = mixed_session((2, 2, 1))
            left, right = stereo_pan(s, 0)
            other = stereo_pan(s, 1)
            pan(s, 2)
            if variant == 'target_left': left.items[0] = payload(((0, 0), (100, 101)))
            elif variant == 'target_right': right.items[0] = payload(((0, 0), (100, 0), (100, 50)))
            elif variant == 'other_stereo': other[1].items[0][-1] = 1
            elif variant == 'other_mono': pan(s, 2).items[0][-1] = 1
            else: state(s, 2).items[4].items[0][0] ^= 1
            before = snapshot(s)
            for channel in (False, True):
                with self.assertRaises(ValueError): s.get_pan_automation(names[0], right=channel)
                self.assertEqual(snapshot(s), before)

    def test_unknown_container_geometry_and_absence_not_invented_default(self):
        for variant in ('empty', 'selector', 'suffix', 'extra', 'type', 'split', 'truncated'):
            s, names = mixed_session((2,))
            left, right = stereo_pan(s, 0)
            c = state(s).items[9]
            if variant == 'empty': c.items = [bytes(14)]
            elif variant == 'selector': c.items[0] = b'\x02'
            elif variant == 'suffix': c.items[2] = bytes(12)
            elif variant == 'extra': c.items.append(b'\x00')
            elif variant == 'type': right.block_type = 2
            elif variant == 'split': right.items.append(b'\x00')
            else: right.items[0] = payload(RIGHT)[:-6]
            before = snapshot(s)
            with self.assertRaises(ValueError): s.get_pan_automation(names[0])
            self.assertEqual(snapshot(s), before)

    def test_right_argument_bool_only_and_exact_name_selection(self):
        s, names = mixed_session((2,))
        stereo_pan(s, 0)
        for flag in (0, 1, None, 'right', []):
            with self.assertRaises(TypeError): s.get_pan_automation(names[0], right=flag)
        with self.assertRaises(ValueError): s.get_pan_automation('MISSING', right=True)

    def test_variable_node_counts_same_complete_schema_and_static_stereo(self):
        s, names = mixed_session((2,))
        left, right = stereo_pan(s, 0)
        self.assertEqual(s.get_pan_automation(names[0]), [(0, -100)])
        self.assertEqual(s.get_pan_automation(names[0], right=True), [(0, 100)])
        for count in (2, 3, 4, 25):
            nodes = [(i * 100, -100 if i % 2 else 100) for i in range(count)]
            left.items[0] = payload(nodes)
            self.assertEqual(s.get_pan_automation(names[0]), nodes)
            self.assertEqual(s.get_pan_automation(names[0], right=True), [(0, 100)])

    def test_reading_automation_does_not_relax_static_methods(self):
        s, names = mixed_session((2,))
        stereo_pan(s, 0)[0].items[0] = payload(LEFT)
        before = snapshot(s)
        self.assertEqual(s.get_pan_automation(names[0]), LEFT)
        for method, args in ((s.get_track_pan_lr, (names[0],)),
                             (s.set_track_pan, (names[0], -50, 50))):
            with self.assertRaises(ValueError): method(*args)
            self.assertEqual(snapshot(s), before)


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'Local native stereo Pan automation pair absent')
class NativeStereoPanAutomationReadTests(unittest.TestCase):
    def test_native_pair_exact_both_channels_and_other_audio_unchanged(self):
        a, b = [ProToolsSession(str(p)) for p in (BEFORE, AFTER)]
        self.assertEqual(a.get_pan_automation('C_STEREO'), [(0, -50)])
        self.assertEqual(a.get_pan_automation('C_STEREO', right=True), [(0, 50)])
        self.assertEqual(b.get_pan_automation('C_STEREO'), LEFT)
        self.assertEqual(b.get_pan_automation('C_STEREO', right=True), RIGHT)
        self.assertEqual(a.get_pan_automation('A_OUTSIDE'), b.get_pan_automation('A_OUTSIDE'))
        self.assertIsNone(b.get_pan_automation('B_INSIDE'))
        for method in ('get_tracks', 'get_clips', 'get_timeline_clips', 'get_volume_automation'):
            self.assertEqual(getattr(a, method)(), getattr(b, method)())
        self.assertEqual(pointer_rows(a), pointer_rows(b))
        for kind in (0x260e, 0x2602, 0x1015, 0x1001, 0x2628, 0x2637):
            self.assertEqual(selected(a, kind), selected(b, kind))

    def test_readonly_both_channels_and_four_byte_exact_native_cycles(self):
        for path in (BEFORE, AFTER):
            s = ProToolsSession(str(path))
            before = snapshot(s)
            raw = [c.items[1].items[0] for c in state(s, 1).items[8:10]]
            s.data = object()
            for flag in (False, True): s.get_pan_automation('C_STEREO', right=flag)
            self.assertEqual(snapshot(s), before)
            for c, original in zip(state(s, 1).items[8:10], raw):
                self.assertIs(c.items[1].items[0], original)
            s = ProToolsSession(str(path))
            with tempfile.TemporaryDirectory() as folder:
                out = Path(folder) / 'cycle.ptx'
                for _ in range(2):
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), path.read_bytes())
                    s = ProToolsSession(str(out))


if __name__ == '__main__':
    unittest.main()
