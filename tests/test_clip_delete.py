"""Timeline-only deletion: native empty playlists, fade removal, atomic TOC purge."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from pt_api import PTBlock, ProToolsSession, xor_session
from tests.test_timeline_read import definition, event, playlist, geometry, make_session
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_loading import minimal_ptx, PREFIX_0002
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
FADE = bytes.fromhex('0000000000330000c07701c07701020200000000000000000000000000')


def plain():
    return make_session([definition('TARGET', 192192), definition('OTHER', 1000)],
                        [playlist('TRACK', [event(0, 1728000000), event(1, 1729000000)])])


def faded():
    return make_session([definition('TARGET', 192192)],
                        [playlist('TRACK', [event(0, 1728000000),
                                            event(0, 1728192192, event_type=1)])],
                        [geometry(FADE)])


class ClipDeleteTests(unittest.TestCase):
    def test_plain_deletes_only_one_event_preserving_bin_gain_tracks_and_survivor_objects(self):
        for rate, enum in ((48000, 2), (48000, 9), (44100, 9), (96000, 2)):
            s = plain()
            s.sample_rate, s.frame_rate_enum = rate, enum
            p = s._validated_main_playlists()[0][0]
            survivor, trailer = p.items[2], p.items[-1]
            before = {id(b): b.to_bytes()[0] for b in s.root_items if b.content_type != 0x1054}
            self.assertEqual(s.delete_clip('TARGET'), 1)
            self.assertIs(p.items[1], survivor)
            self.assertIs(p.items[-1], trailer)
            self.assertEqual(struct.unpack_from('<I', p.items[0], 9)[0], 1)
            self.assertEqual([i['clip_name'] for i in s.get_timeline_clips()], ['OTHER'])
            for b in s.root_items:
                if id(b) in before: self.assertEqual(b.to_bytes()[0], before[id(b)])
            self.assertEqual(s.delete_clip('OTHER'), 1)
            self.assertEqual(s.get_timeline_clips(), [])
            self.assertEqual(s.get_tracks(), ['TRACK'])

    def test_native_uint24_fade_out_removed_with_geometry_not_left_orphaned(self):
        s = faded()
        clips = s._root_blocks(0x262a)[0].to_bytes()[0]
        self.assertEqual(s.delete_clip('TARGET'), 2)
        self.assertEqual(s.get_timeline_clips(), [])
        self.assertEqual(s._root_blocks(0x2630)[0].items, [bytes(4)])
        self.assertEqual(s._root_blocks(0x262a)[0].to_bytes()[0], clips)

    def test_missing_and_repeated_placements_or_duplicate_definition_names_are_atomic(self):
        for variant in ('absent', 'unplaced', 'multiple', 'duplicate'):
            s = plain()
            p = s._validated_main_playlists()[0][0]
            name = 'TARGET'
            if variant == 'absent': name = 'target'
            elif variant == 'unplaced':
                p.items = [struct.pack('<I', 5) + b'TRACK' + struct.pack('<I', 1), p.items[2], p.items[-1]]
            elif variant == 'multiple':
                p.items.insert(2, event(0, 20))
                struct.pack_into('<I', p.items[0], 9, 3)
            else:
                s._root_blocks(0x262a)[0].items[2] = definition('TARGET', 1000)
            before, items = snapshot(s), p.items
            with self.assertRaises(ValueError): s.delete_clip(name)
            self.assertEqual(snapshot(s), before)
            self.assertIs(p.items, items)

    def test_input_and_endianness_or_malformed_headers_are_atomic(self):
        for value, error in ((None, TypeError), (True, TypeError), (1, TypeError),
                             ('', ValueError), ('A\x00', ValueError), ('\ud800', ValueError)):
            s = plain()
            before = snapshot(s)
            with self.assertRaises(error): s.delete_clip(value)
            self.assertEqual(snapshot(s), before)
        for variant in ('endian', 'count', 'payload'):
            s = plain()
            p = s._validated_main_playlists()[0][0]
            if variant == 'endian': s.is_bigendian = True
            elif variant == 'count': struct.pack_into('<I', p.items[0], 9, 99)
            else: p.items[1].items[0].items[0] = bytes(16)
            before = snapshot(s)
            with self.assertRaises(ValueError): s.delete_clip('TARGET')
            self.assertEqual(snapshot(s), before)

    def test_groups_with_colliding_ids_and_unrelated_tracks_are_preserved(self):
        s = plain()
        macro = event(0, 100)
        macro.items[-1] = b'\x00\x00\x01'
        second = playlist('OTHER TRACK', [macro, event(1, 200)])
        root = s._root_blocks(0x1054)[0]
        root.items.append(second)
        struct.pack_into('<I', root.items[0], 0, 2)
        before = second.to_bytes()[0]
        self.assertEqual(s.delete_clip('TARGET'), 1)
        self.assertEqual(second.to_bytes()[0], before)

    def test_unknown_crossfade_or_compaction_profiles_refuse_without_mutation(self):
        for variant in ('crossfade_flag', 'crossfade_geometry', 'fade_in', 'legacy',
                        'other_geometry', 'unknown', 'interior', 'orphan', 'too_long', 'duration'):
            s = faded()
            p = s._validated_main_playlists()[0][0]
            g = s._root_blocks(0x2630)[0]
            if variant == 'crossfade_flag': p.items[1].items[0].items[0][33] = 1
            elif variant in ('crossfade_geometry', 'fade_in', 'legacy', 'unknown'):
                g.items[1].items = [bytes({'crossfade_geometry': 34, 'fade_in': 22,
                                         'legacy': 27, 'unknown': 25}[variant])]
                if variant == 'fade_in': struct.pack_into('<Q', p.items[2].items[0].items[0], 7, 1728000000)
            elif variant == 'other_geometry':
                g.items.append(geometry(FADE))
                struct.pack_into('<I', g.items[0], 0, 2)
                p.items.insert(3, event(1, 1728192192, event_type=1))
                struct.pack_into('<I', p.items[0], 9, 3)
            elif variant == 'interior': struct.pack_into('<Q', p.items[2].items[0].items[0], 7, 1728000100)
            elif variant == 'orphan':
                p.items.pop(2)
                struct.pack_into('<I', p.items[0], 9, 1)
            elif variant == 'too_long':
                g.items[1].items[0][8:11] = g.items[1].items[0][11:14] = (192193).to_bytes(3, 'little')
            else: g.items[1].items[0][11] ^= 1
            before = snapshot(s)
            offsets = s._removed_offsets
            with self.assertRaises((ValueError, NotImplementedError)): s.delete_clip('TARGET')
            self.assertEqual(snapshot(s), before)
            self.assertIs(s._removed_offsets, offsets)

    def test_fade_out_with_two_possible_owners_is_not_deleted(self):
        s = faded()
        cl = s._root_blocks(0x262a)[0]
        cl.items.append(definition('OTHER', 192192))
        struct.pack_into('<I', cl.items[0], 0, 2)
        p = s._validated_main_playlists()[0][0]
        p.items.insert(2, event(1, 1728000000))
        struct.pack_into('<I', p.items[0], 9, 3)
        before = snapshot(s)
        with self.assertRaisesRegex(ValueError, 'ownership is ambiguous'): s.delete_clip('TARGET')
        self.assertEqual(snapshot(s), before)

    def test_plain_deletion_preserves_unrelated_fade_ids_and_geometry_objects(self):
        s = make_session([definition('TARGET', 1000), definition('OTHER', 192192)],
                         [playlist('TRACK', [event(0, 100), event(1, 10000),
                                             event(0, 202192, event_type=1)])], [geometry(FADE)])
        p = s._validated_main_playlists()[0][0]
        fade, geom = p.items[3], s._root_blocks(0x2630)[0]
        original_fade, original_geom = fade.to_bytes()[0], geom.to_bytes()[0]
        self.assertEqual(s.delete_clip('TARGET'), 1)
        self.assertIs(p.items[2], fade)
        self.assertEqual(fade.to_bytes()[0], original_fade)
        self.assertEqual(geom.to_bytes()[0], original_geom)
        self.assertEqual([item['clip_name'] for item in s.get_timeline_clips()], ['OTHER', 'OTHER'])

    def test_zero_duration_or_uint64_end_overflow_is_atomic(self):
        for length, start in ((0, 10), (1000, 0xffffffffffffffff)):
            s = make_session([definition('TARGET', length)], [playlist('TRACK', [event(0, start)])])
            before = snapshot(s)
            with self.assertRaisesRegex(ValueError, 'bounds'): s.delete_clip('TARGET')
            self.assertEqual(snapshot(s), before)

    def test_preparation_failure_preserves_objects_and_prior_removal_metadata(self):
        for hook in ('struct.pack_into', '_collect_offsets_recursive'):
            s = faded()
            s._removed_offsets, s._removed_block_types = [77], {77: 0x2077}
            before, roots = snapshot(s), s.root_items
            offsets, types = s._removed_offsets, s._removed_block_types
            context = (mock.patch('pt_api.struct.pack_into', side_effect=RuntimeError('prepare'))
                       if hook == 'struct.pack_into' else
                       mock.patch.object(s, hook, side_effect=RuntimeError('prepare')))
            with context:
                with self.assertRaises(RuntimeError): s.delete_clip('TARGET')
            self.assertEqual(snapshot(s), before)
            self.assertIs(s.root_items, roots)
            self.assertIs(s._removed_offsets, offsets)
            self.assertIs(s._removed_block_types, types)


    def test_standard_pointers_to_events_descendants_and_geometry_are_purged(self):
        s = faded()
        p = s._validated_main_playlists()[0][0]
        p.items.insert(3, event(1, 1729000000))
        struct.pack_into('<I', p.items[0], 9, 3)
        cl = s._root_blocks(0x262a)[0]
        cl.items.append(definition('OTHER', 1000))
        struct.pack_into('<I', cl.items[0], 0, 2)
        middle = b''.join(b.to_bytes()[0] for b in s.root_items)
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / 'source.ptx', Path(d) / 'out.ptx'
            xor_session(minimal_ptx(struct.pack('>H', 0), middle=middle), str(src))
            loaded = ProToolsSession(str(src))
            events = loaded._validated_main_timeline_events()
            doomed = [events[0][1], events[0][2], events[1][1], events[1][2],
                      loaded._root_blocks(0x2630)[0].items[1]]
            survivor = events[2][1]
            records = [PREFIX_0002 + struct.pack('<I', b.original_offset) + bytes(3)
                       for b in [*doomed, survivor]]
            xor_session(minimal_ptx(struct.pack('>H', 6) + b''.join(records), middle=middle), str(src))
            loaded = ProToolsSession(str(src))
            self.assertEqual(loaded.delete_clip('TARGET'), 2)
            loaded.save(str(out))
            loaded = ProToolsSession(str(out))
            self.assertEqual([i['clip_name'] for i in loaded.get_timeline_clips()], ['OTHER'])
            table = bytes(loaded._raw_0002_payload(loaded._root_blocks(2)[0]))
            records = loaded._validate_0002_record_layout(table)
            self.assertEqual(len(records), 1)
            self.assertEqual(struct.unpack_from('<I', table, records[0][1])[0],
                             loaded._validated_main_timeline_events()[0][1].original_offset)
            data = out.read_bytes()
            loaded.save(str(out))
            self.assertEqual(out.read_bytes(), data)


class ResavedClipDeleteTests(unittest.TestCase):
    def check_saved(self, case):
        base_path = FIXTURES / ('native_clip_delete_' + case + '_before.ptx')
        oracle_path = FIXTURES / ('native_clip_delete_' + case + '_after.ptx')
        path = FIXTURES / ('native_clip_delete_' + case + '_pt_resaved.ptx')
        if not all(p.exists() for p in (base_path, oracle_path, path)):
            self.skipTest('Local Pro Tools-resaved timeline deletion candidate absent')
        base, native, saved = [ProToolsSession(str(p)) for p in (base_path, oracle_path, path)]
        self.assertEqual(saved.get_tracks(), base.get_tracks())
        self.assertEqual(saved.get_timeline_clips(), [])
        self.assertEqual(saved.get_clips(), base.get_clips())
        self.assertEqual(saved._audio_clip_info_by_id(0), base._audio_clip_info_by_id(0))
        self.assertEqual(saved._validated_physical_audio_catalog()[3], base._validated_physical_audio_catalog()[3])
        for code in (0x1054, 0x2630):
            self.assertEqual(saved._root_blocks(code)[0].to_bytes()[0], native._root_blocks(code)[0].to_bytes()[0])
        self.assertEqual(saved._root_blocks(0x2637)[0].to_bytes()[0], base._root_blocks(0x2637)[0].to_bytes()[0])
        self.assertEqual(pointer_rows(saved), pointer_rows(base))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'noop.ptx'
            for _ in range(2):
                saved.save(str(out))
                self.assertEqual(out.read_bytes(), path.read_bytes())
                saved = ProToolsSession(str(out))

    def test_resaved_plain_deletion_keeps_native_empty_profile_and_bin(self):
        self.check_saved('plain')

    def test_resaved_fade_deletion_keeps_native_empty_profile_and_bin(self):
        self.check_saved('fade')


@unittest.skipUnless(all((FIXTURES / ('native_clip_delete_' + case + '_' + step + '.ptx')).exists()
                         for case in ('plain', 'fade') for step in ('before', 'after')),
                     'Local native timeline deletion oracles absent')
class NativeClipDeleteTests(unittest.TestCase):
    def test_four_native_oracles_pass_two_byte_identical_cycles_each(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'noop.ptx'
            for case in ('plain', 'fade'):
                for step in ('before', 'after'):
                    path = FIXTURES / ('native_clip_delete_' + case + '_' + step + '.ptx')
                    s = ProToolsSession(str(path))
                    for _ in range(2):
                        s.save(str(out))
                        self.assertEqual(out.read_bytes(), path.read_bytes())
                        s = ProToolsSession(str(out))

    def test_deletion_matches_native_timeline_and_geometry_preserving_every_other_root(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'candidate.ptx'
            for case, count in (('plain', 1), ('fade', 2)):
                s = ProToolsSession(str(FIXTURES / ('native_clip_delete_' + case + '_before.ptx')))
                native = ProToolsSession(str(FIXTURES / ('native_clip_delete_' + case + '_after.ptx')))
                old = {id(b): b.to_bytes()[0] for b in s.root_items if isinstance(b, PTBlock)
                       and b.content_type not in (0x1054, 0x2630)}
                clips = s.get_clips()
                self.assertEqual(s.delete_clip('Audio 1_01'), count)
                for code in (0x1054, 0x2630):
                    self.assertEqual(s._root_blocks(code)[0].to_bytes()[0], native._root_blocks(code)[0].to_bytes()[0])
                for b in s.root_items:
                    if id(b) in old: self.assertEqual(b.to_bytes()[0], old[id(b)])
                self.assertEqual(s.get_clips(), clips)
                s.save(str(out))
                data = out.read_bytes()
                for _ in range(2):
                    s = ProToolsSession(str(out))
                    self.assertEqual(s.get_clips(), clips)
                    self.assertEqual(s.get_timeline_clips(), [])
                    self.assertEqual(s.get_tracks(), ['Audio 1'])
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), data)

    def test_missing_malformed_toc_and_metadata_conflict_are_atomic(self):
        for variant in ('missing', 'malformed', 'short', 'conflict'):
            s = ProToolsSession(str(FIXTURES / 'native_clip_delete_fade_before.ptx'))
            table = s._root_blocks(2)[0]
            s._removed_offsets, s._removed_block_types = [99], {99: 0x2077}
            if variant == 'missing': s.root_items.remove(table)
            elif variant == 'malformed':
                payload = bytes(s._raw_0002_payload(table))
                _, pos = s._validate_0002_record_layout(payload)[0]
                table.items = [payload[:pos + 2]]
            elif variant == 'short': table.items = [bytes(1)]
            else: s._removed_block_types[s._validated_main_timeline_events()[0][1].original_offset] = 0x9999
            before = snapshot(s)
            offsets, types = s._removed_offsets, s._removed_block_types
            with self.assertRaises(ValueError): s.delete_clip('Audio 1_01')
            self.assertEqual(snapshot(s), before)
            self.assertIs(s._removed_offsets, offsets)
            self.assertIs(s._removed_block_types, types)
