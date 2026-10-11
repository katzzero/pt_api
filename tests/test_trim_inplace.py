"""Strict in-place compact-virtual trim: exact native fields, no backup/rename."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from pt_api import PTBlock, ProToolsSession
from tests.test_timeline_read import block, event, playlist, geometry, make_session
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
TAIL = bytes.fromhex('fffffffffffffffffeff00000000ffff04000400') + bytes(21) + bytes.fromhex('ffffffff0000')


def definition(name='TAKE', start=1000000, offset=100, length=1000):
    n = name.encode('utf-8')
    p = (struct.pack('<I', len(n)) + n + bytes.fromhex('0110304408')
         + bytes([offset]) + length.to_bytes(3, 'little') + struct.pack('<II', start, start) + TAIL)
    return block(11, 0x2629, [block(4, 0x2628, [bytearray(p)])])


def session():
    return make_session([definition(), definition('OTHER', start=2000000)],
                        [playlist('TRACK', [event(0, 1000000), event(1, 2000000)])])


class TrimInplaceTests(unittest.TestCase):
    def test_head_changes_only_source_length_two_times_and_event_start(self):
        for sample_rate, enum in ((48000, 9), (48000, 2), (44100, 9), (96000, 2)):
            s = session()
            s.sample_rate, s.frame_rate_enum = sample_rate, enum
            expected = copy.deepcopy(s)
            p = expected._root_blocks(0x262a)[0].items[1].items[0].items[0]
            a = 8
            p[a + 5] = 200
            p[a + 6:a + 9] = (900).to_bytes(3, 'little')
            struct.pack_into('<II', p, a + 9, 1000100, 1000100)
            struct.pack_into('<Q', expected._validated_main_timeline_events()[0][3], 7, 1000100)
            definitions = s._root_blocks(0x262a)[0].items
            survivor = s._validated_main_timeline_events()[1][1]
            self.assertEqual(s.trim_clip_head('TAKE', 1000100), 0)
            self.assertEqual(snapshot(s), snapshot(expected))
            self.assertIs(s._root_blocks(0x262a)[0].items, definitions)
            self.assertIs(s._validated_main_timeline_events()[1][1], survivor)
            t = s.get_timeline_clips()[0]
            self.assertEqual((t['clip_name'], t['start_samples'], t['end_samples'], t['src_offset_samples']),
                             ('TAKE', 1000100, 1001000, 200))

    def test_tail_changes_only_length_and_preserves_event_object(self):
        s = session()
        expected = copy.deepcopy(s)
        expected._root_blocks(0x262a)[0].items[1].items[0].items[0][14:17] = (900).to_bytes(3, 'little')
        e = s._validated_main_timeline_events()[0][2].items[0]
        self.assertEqual(s.trim_clip_tail('TAKE', 1000900), 0)
        self.assertEqual(snapshot(s), snapshot(expected))
        self.assertIs(s._validated_main_timeline_events()[0][2].items[0], e)
        self.assertEqual(s._audio_clip_info_by_id(0)['src_offset'], 100)

    def test_head_then_tail_composition_is_exact_and_does_not_allocate_definitions(self):
        s = session()
        roots, offsets = s.root_items, s._removed_offsets
        self.assertEqual(s.trim_clip_head('TAKE', 1000100), 0)
        self.assertEqual(s.trim_clip_tail('TAKE', 1000900), 0)
        info = s._audio_clip_info_by_id(0)
        self.assertEqual((info['name'], info['src_offset'], info['length']), ('TAKE', 200, 800))
        self.assertIs(s.root_items, roots)
        self.assertIs(s._removed_offsets, offsets)
        self.assertEqual(len(s.get_clips()), 2)

    def test_bad_names_and_cuts_are_atomic(self):
        for method in ('trim_clip_head', 'trim_clip_tail'):
            for name, cut, exc in ((None, 1000100, TypeError), ('', 1000100, ValueError),
                                   ('T\x00', 1000100, ValueError), ('\ud800', 1000100, ValueError),
                                   ('TAKE', True, TypeError), ('TAKE', 1.1, TypeError),
                                   ('TAKE', -1, ValueError), ('TAKE', 0x100000000, ValueError),
                                   ('TAKE', 1000000, ValueError), ('TAKE', 1001000, ValueError),
                                   ('TAKE', 1002000, ValueError), ('missing', 1000100, ValueError)):
                s = session()
                before = snapshot(s)
                with self.assertRaises(exc): getattr(s, method)(name, cut)
                self.assertEqual(snapshot(s), before)

    def test_unknown_layout_gain_sentinel_and_shifted_source_are_refused(self):
        for variant in ('endian', 'type', 'shape', 'tuple', 'event_tuple', 'marker', 'flag', 'selector', 'tail',
                        'gain', 'source_zero', 'sentinel', 'shifted', 'width'):
            s = session()
            b = s._root_blocks(0x262a)[0].items[1].items[0]
            p, a = b.items[0], 8
            if variant == 'endian': s.is_bigendian = True
            elif variant == 'type': b.block_type = 1
            elif variant == 'shape': b.items.append(bytes(1))
            elif variant == 'tuple': b.items = tuple(b.items)
            elif variant == 'event_tuple':
                e = s._validated_main_timeline_events()[0][2]
                e.items = tuple(e.items)
            elif variant == 'marker': p[a + 3] = 0x14
            elif variant == 'flag': p[a:a + 2] = b'\x00\x10'
            elif variant == 'selector': p[a + 2] = 0x20
            elif variant == 'tail': p[-1] = 1
            elif variant == 'gain': p[-6:-2] = bytes(4)
            elif variant == 'source_zero': p[a + 5] = 0
            elif variant == 'sentinel': struct.pack_into('<I', p, a + 13, 0xffffffff)
            elif variant == 'shifted': struct.pack_into('<I', p, a + 9, 1000001)
            else: p[a + 5] = 250
            before = snapshot(s)
            with self.assertRaises(ValueError): s.trim_clip_head('TAKE', 1000100)
            self.assertEqual(snapshot(s), before)

    def test_shared_hidden_group_and_ambiguous_placements_do_not_get_edited(self):
        for variant in ('multiple', 'duplicate_name', 'hidden', 'macro_collision'):
            s = session()
            p = s._validated_main_playlists()[0][0]
            if variant == 'multiple':
                p.items.insert(2, event(0, 1000200))
                struct.pack_into('<I', p.items[0], 9, 3)
            elif variant == 'duplicate_name': s._root_blocks(0x262a)[0].items[2] = definition()
            elif variant == 'hidden': s.root_items.append(block(1, 0x2428, [playlist('INNER', [event(0, 10)])]))
            else:
                macro = event(0, 500000)
                macro.items[-1] = b'\x00\x00\x01'
                p.items.insert(1, macro)
                struct.pack_into('<I', p.items[0], 9, 3)
                # Empty macro with an ID collision is not an audio placement.
                self.assertEqual(s.trim_clip_head('TAKE', 1000100), 0)
                self.assertIs(p.items[1], macro)
                continue
            before = snapshot(s)
            with self.assertRaises(ValueError): s.trim_clip_head('TAKE', 1000100)
            self.assertEqual(snapshot(s), before)

    def test_any_fade_on_the_target_playlist_and_orphan_geometry_are_refused(self):
        for variant in ('fade', 'orphan'):
            s = session()
            s._root_blocks(0x2630)[0].items = [struct.pack('<I', 1), geometry(bytes(22))]
            if variant == 'fade':
                p = s._validated_main_playlists()[0][0]
                p.items.insert(1, event(0, 1000000, event_type=1))
                struct.pack_into('<I', p.items[0], 9, 3)
            before = snapshot(s)
            with self.assertRaises((ValueError, NotImplementedError)): s.trim_clip_head('TAKE', 1000100)
            self.assertEqual(snapshot(s), before)

    def test_late_encoding_failure_preserves_all_objects_and_bookkeeping(self):
        s = session()
        before, roots, offsets = snapshot(s), s.root_items, s._removed_offsets
        payload = s._root_blocks(0x262a)[0].items[1].items[0].items[0]
        native_pack = struct.pack_into
        calls = []
        def fail_on_event(*args):
            calls.append(args[0])
            if len(calls) == 2: raise RuntimeError('late encoding')
            return native_pack(*args)
        with mock.patch('pt_api.struct.pack_into', side_effect=fail_on_event):
            with self.assertRaises(RuntimeError): s.trim_clip_head('TAKE', 1000100)
        self.assertEqual(snapshot(s), before)
        self.assertIs(s.root_items, roots)
        self.assertIs(s._removed_offsets, offsets)
        self.assertIs(s._root_blocks(0x262a)[0].items[1].items[0].items[0], payload)


@unittest.skipUnless(all((FIXTURES / ('native_trim_inplace_' + n + '.ptx')).exists()
                         for n in ('before', 'head', 'tail')), 'Local native in-place trim oracles absent')
class NativeTrimInplaceTests(unittest.TestCase):
    def test_all_native_oracles_pass_two_byte_identical_cycles(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'noop.ptx'
            for n in ('before', 'head', 'tail'):
                path = FIXTURES / ('native_trim_inplace_' + n + '.ptx')
                s = ProToolsSession(str(path))
                for _ in range(2):
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), path.read_bytes())
                    s = ProToolsSession(str(out))

    def test_head_and_tail_match_native_payloads_and_events_without_native_backup(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'candidate.ptx'
            for n, cut in (('head', 1729728200), ('tail', 1738408572)):
                s = ProToolsSession(str(FIXTURES / 'native_trim_inplace_before.ptx'))
                native = ProToolsSession(str(FIXTURES / ('native_trim_inplace_' + n + '.ptx')))
                old = {id(b): b.to_bytes()[0] for b in s.root_items if isinstance(b, PTBlock)
                       and b.content_type not in (0x262a, 0x1054)}
                toc = pointer_rows(s)
                count = len(s.get_clips())
                old_defs = s._root_blocks(0x262a)[0].get_all_blocks(0x2629)
                parent = old_defs[0].to_bytes()[0]
                self.assertEqual(getattr(s, 'trim_clip_' + n)('Audio 1_01-01', cut), 1)
                new = old_defs[1].get_all_blocks(0x2628)[0]
                native_def = native._root_blocks(0x262a)[0].get_all_blocks(0x2629)[1].get_all_blocks(0x2628)[0]
                self.assertEqual(new.to_bytes()[0], native_def.to_bytes()[0])
                self.assertEqual(s._validated_main_timeline_events()[0][1].to_bytes()[0],
                                 native._validated_main_timeline_events()[0][1].to_bytes()[0])
                self.assertEqual(s.get_timeline_clips(), native.get_timeline_clips())
                self.assertEqual(old_defs[0].to_bytes()[0], parent)
                self.assertEqual(len(s.get_clips()), count)
                self.assertEqual(len(native.get_clips()), 3 if n == 'head' else 2)
                self.assertEqual(pointer_rows(s), toc)
                for b in s.root_items:
                    if id(b) in old: self.assertEqual(b.to_bytes()[0], old[id(b)])
                s.save(str(out))
                data = out.read_bytes()
                for _ in range(2):
                    saved = ProToolsSession(str(out))
                    self.assertEqual(saved.get_timeline_clips(), native.get_timeline_clips())
                    self.assertEqual(len(saved.get_clips()), count)
                    self.assertEqual(pointer_rows(saved), toc)
                    saved.save(str(out))
                    self.assertEqual(out.read_bytes(), data)

    def test_native_composition_retains_name_ids_and_original_definitions(self):
        s = ProToolsSession(str(FIXTURES / 'native_trim_inplace_before.ptx'))
        self.assertEqual(s.trim_clip_head('Audio 1_01-01', 1729728200), 1)
        self.assertEqual(s.trim_clip_tail('Audio 1_01-01', 1738408572), 1)
        t = s.get_timeline_clips()[0]
        self.assertEqual((t['start_samples'], t['end_samples'], t['length_samples'], t['src_offset_samples']),
                         (1729728200, 1738408572, 8680372, 200))
        self.assertEqual(len(s.get_clips()), 2)


@unittest.skipUnless(all((FIXTURES / ('native_trim_inplace_' + n + '_pt_resaved.ptx')).exists()
                         for n in ('head', 'tail')), 'Local Pro Tools-resaved in-place trims absent')
class ResavedTrimInplaceTests(unittest.TestCase):
    def test_geometry_catalog_pointers_audio_identity_and_exact_cycles(self):
        base = ProToolsSession(str(FIXTURES / 'native_trim_inplace_before.ptx'))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'cycle.ptx'
            for n, start, end, offset in (('head', 1729728200, 1738408672, 200),
                                           ('tail', 1729728100, 1738408572, 100)):
                path = FIXTURES / ('native_trim_inplace_' + n + '_pt_resaved.ptx')
                data = path.read_bytes()
                s = ProToolsSession(str(path))
                t = s.get_timeline_clips()[0]
                self.assertEqual((t['track'], t['clip_name'], t['start_samples'], t['end_samples'],
                                  t['length_samples'], t['src_offset_samples'], t['physical_filename']),
                                 ('Audio 1', 'Audio 1_01-01', start, end, 8680472, offset, 'Audio 1_01.wav'))
                self.assertEqual(s.get_tracks(), base.get_tracks())
                self.assertEqual(len(s.get_clips()), 2)
                self.assertEqual(s._audio_clip_info_by_id(0), base._audio_clip_info_by_id(0))
                self.assertEqual(pointer_rows(s), pointer_rows(base))
                for _ in range(2):
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), data)
                    s = ProToolsSession(str(out))

    def test_resaved_profiles_accept_composition_without_bin_allocation(self):
        for n in ('head', 'tail'):
            s = ProToolsSession(str(FIXTURES / ('native_trim_inplace_' + n + '_pt_resaved.ptx')))
            t = s.get_timeline_clips()[0]
            self.assertEqual(s.trim_clip_head('Audio 1_01-01', t['start_samples'] + 25), 1)
            self.assertEqual(s.trim_clip_tail('Audio 1_01-01', t['end_samples'] - 25), 1)
            result = s.get_timeline_clips()[0]
            self.assertEqual((result['start_samples'], result['end_samples'], result['length_samples'],
                              result['src_offset_samples']),
                             (t['start_samples'] + 25, t['end_samples'] - 25,
                              t['length_samples'] - 50, t['src_offset_samples'] + 25))
            self.assertEqual(len(s.get_clips()), 2)
