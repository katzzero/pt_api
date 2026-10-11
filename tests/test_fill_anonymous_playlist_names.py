"""Native mixed mono names and staged, identity-checked name materialization."""
import copy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import anonymous_session, snapshot, RESAVED
from tests.test_get_tracks import playlist, make_session
from tests.test_volume_stereo_read import mixed_session
from tests.test_stereo_to_stereo_bus_output import pointer_rows

RENAMED = Path(__file__).parent / 'fixtures' / 'native_anonymous_playlist_renamed.ptx'
FILLED_RESAVED = Path(__file__).parent / 'fixtures' / 'native_filled_playlist_names_pt_resaved.ptx'
VOLUME_RESAVED = Path(__file__).parent / 'fixtures' / 'native_volume_aaf_names_pt_resaved.ptx'


class FillAnonymousPlaylistTests(unittest.TestCase):
    def test_mixed_mono_read_resolves_names_without_mutation(self):
        s = anonymous_session()
        s._root_blocks(0x1054)[0].items[1] = playlist('MONO A')
        original = snapshot(s)
        self.assertEqual(s.get_tracks(), ['MONO A', 'MONO B'])
        self.assertEqual(snapshot(s), original)
        with self.assertRaisesRegex(ValueError, 'Track name cannot be empty'):
            s._validated_main_playlists()

    def test_fill_one_two_three_and_unicode_names_changes_only_headers(self):
        for names in [('ONE',), ('A', 'B'), ('PISTE É', 'SECOND', 'THIRD')]:
            with self.subTest(names=names):
                s = anonymous_session(names)
                expected = copy.deepcopy(s._root_blocks(0x1054)[0])
                for p, name in zip(expected.items[1:], names):
                    text = name.encode('utf-8')
                    p.items[0] = struct.pack('<I', len(text)) + text + p.items[0][4:]
                untouched = snapshot(s)[1:]
                self.assertEqual(s.fill_anonymous_playlist_names(), len(names))
                self.assertEqual(s._root_blocks(0x1054)[0].to_bytes()[0], expected.to_bytes()[0])
                self.assertEqual(snapshot(s)[1:], untouched)
                self.assertEqual(s.get_tracks(), list(names))
                before = snapshot(s)
                self.assertEqual(s.fill_anonymous_playlist_names(), 0)
                self.assertEqual(snapshot(s), before)

    def test_mixed_fill_keeps_named_header_and_opaque_suffix_exact(self):
        s = anonymous_session()
        main = s._root_blocks(0x1054)[0]
        main.items[1] = playlist('MONO A')
        main.items[1].items[0].extend(b'OPAQUE')
        original = main.items[1].items[0]
        expected = bytes(original)
        self.assertEqual(s.fill_anonymous_playlist_names(), 1)
        self.assertIs(main.items[1].items[0], original)
        self.assertEqual(bytes(original), expected)
        self.assertEqual(s.get_tracks(), ['MONO A', 'MONO B'])

    def test_stereo_widths_and_mixed_names_use_existing_identity_schema(self):
        for widths in [(2,), (2, 1), (1, 2), (2, 2), (1, 2, 2, 1)]:
            with self.subTest(widths=widths):
                s, names = mixed_session(widths)
                main = s._root_blocks(0x1054)[0]
                for p in main.items[1::2]:
                    p.items[0] = bytearray(8)
                missing = len(main.items[1::2])
                untouched = snapshot(s)[1:]
                self.assertEqual(s.fill_anonymous_playlist_names(), missing)
                self.assertEqual(snapshot(s)[1:], untouched)
                expanded = [name for name, width in zip(names, widths) for _ in range(width)]
                self.assertEqual(s.get_tracks(), expanded)
                self.assertEqual([r['track'] for r in s.get_volume_automation()], list(names))
                self.assertEqual(s.fill_anonymous_playlist_names(), 0)

    def test_live_tree_not_cached_source_is_used(self):
        s = anonymous_session(('A', 'B'))
        s.data = object()
        self.assertEqual(s.fill_anonymous_playlist_names(), 2)
        self.assertEqual(s.get_tracks(), ['A', 'B'])

    def test_late_invalid_header_never_mutates_earlier_playlist(self):
        s = anonymous_session()
        s._root_blocks(0x1054)[0].items[2].items[0].extend(b'opaque unknown anonymous suffix')
        before = snapshot(s)
        with self.assertRaisesRegex(ValueError, 'Unsupported anonymous'):
            s.fill_anonymous_playlist_names()
        self.assertEqual(snapshot(s), before)

    def test_named_mismatch_nul_and_invalid_utf8_are_refused(self):
        for name in ('WRONG', 'MONO A\x00'):
            s = anonymous_session()
            s._root_blocks(0x1054)[0].items[2] = playlist(name)
            before = snapshot(s)
            with self.assertRaisesRegex(ValueError, 'disagrees'):
                s.fill_anonymous_playlist_names()
            self.assertEqual(snapshot(s), before)
        s = anonymous_session()
        s._root_blocks(0x1054)[0].items[2] = playlist('MONO B')
        s._root_blocks(0x1054)[0].items[2].items[0][4] = 0xFF
        before = snapshot(s)
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            s.fill_anonymous_playlist_names()
        self.assertEqual(snapshot(s), before)

    def test_map_event_and_descriptor_counts_and_zero_width_are_not_guessed(self):
        for mode in ('main count', 'events', 'descriptor count', 'zero width', 'channel count'):
            with self.subTest(mode=mode):
                s = anonymous_session()
                if mode == 'main count':
                    struct.pack_into('<I', s._root_blocks(0x1054)[0].items[0], 0, 3)
                elif mode == 'events':
                    struct.pack_into('<I', s._root_blocks(0x1054)[0].items[2].items[0], 4, 1)
                elif mode == 'descriptor count':
                    struct.pack_into('<I', s._root_blocks(0x1015)[0].items[0], 0, 3)
                elif mode == 'zero width':
                    p = s._root_blocks(0x1015)[0].items[2].items[0]
                    offset = 4 + struct.unpack_from('<I', p)[0]
                    struct.pack_into('<I', p, offset + 1, 0)
                else:
                    main = s._root_blocks(0x1054)[0]
                    main.items.append(playlist(''))
                    struct.pack_into('<I', main.items[0], 0, 3)
                before = snapshot(s)
                with self.assertRaises(ValueError):
                    s.fill_anonymous_playlist_names()
                self.assertEqual(snapshot(s), before)

    def test_duplicate_missing_or_contradictory_identity_mirrors_are_atomic(self):
        for mode in ('missing', 'duplicate', 'metadata name', 'catalog identity', 'slot identity'):
            with self.subTest(mode=mode):
                s = anonymous_session()
                if mode in ('missing', 'duplicate'):
                    root = s._root_blocks(0x1015)[0]
                    if mode == 'missing': s.root_items.remove(root)
                    else: s.root_items.append(copy.deepcopy(root))
                elif mode == 'metadata name':
                    s._root_blocks(0x2107)[0].get_all_blocks(0x210b)[1].items[0][8] ^= 1
                elif mode == 'catalog identity':
                    p = s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[3].items[0]
                    p[6 + struct.unpack_from('<I', p, 2)[0] + 10] ^= 1
                else:
                    s._root_blocks(0x2624)[0].items[2].get_all_blocks(0x2619)[0].items[-1][8] ^= 1
                before = snapshot(s)
                with self.assertRaises(ValueError):
                    s.fill_anonymous_playlist_names()
                self.assertEqual(snapshot(s), before)

    def test_stereo_descriptor_indexes_and_slot_ordinals_are_corroborated(self):
        for mode in ('descriptor', 'slot'):
            s, names = mixed_session((2, 1))
            s._root_blocks(0x1054)[0].items[1].items[0] = bytearray(8)
            if mode == 'descriptor':
                p = s._root_blocks(0x1015)[0].items[1].items[0]
                struct.pack_into('<H', p, 4 + len(names[0].encode()) + 7, 9)
            else:
                struct.pack_into('<I', s._root_blocks(0x2624)[0].items[2].items[-1], 0, 9)
            before = snapshot(s)
            with self.assertRaises(ValueError):
                s.fill_anonymous_playlist_names()
            self.assertEqual(snapshot(s), before)

    def test_empty_map_and_named_noop_preserve_objects(self):
        s = make_session([])
        s.is_bigendian = False
        self.assertEqual(s.fill_anonymous_playlist_names(), 0)
        s = anonymous_session()
        main = s._root_blocks(0x1054)[0]
        main.items[1:] = [playlist('MONO A'), playlist('MONO B')]
        before = snapshot(s)
        headers = [p.items[0] for p in main.items[1:]]
        self.assertEqual(s.fill_anonymous_playlist_names(), 0)
        self.assertEqual(snapshot(s), before)
        self.assertTrue(all(p.items[0] is old for p, old in zip(main.items[1:], headers)))

    def test_big_endian_refused_without_mutation(self):
        s = anonymous_session()
        s.is_bigendian = True
        before = snapshot(s)
        with self.assertRaisesRegex(ValueError, 'little-endian'):
            s.fill_anonymous_playlist_names()
        self.assertEqual(snapshot(s), before)

    def test_late_uint32_name_overflow_refuses_before_any_mutation(self):
        class HugeBytes(bytes):
            def __len__(self): return 0x100000000
        class HugeName(str):
            def encode(self, *args, **kwargs): return HugeBytes(b'x')
        s = anonymous_session()
        before = snapshot(s)
        with mock.patch.object(s, '_validated_audio_playlist_names', return_value=['A', HugeName('B')]):
            with self.assertRaisesRegex(ValueError, 'too long'):
                s.fill_anonymous_playlist_names()
        self.assertEqual(snapshot(s), before)


@unittest.skipUnless(RENAMED.exists() and RESAVED.exists(), 'optional native AAF rename pair absent')
class NativePlaylistNameTests(unittest.TestCase):
    def test_native_mixed_read_preserves_geometry_names_and_two_cycles(self):
        s = ProToolsSession(str(RENAMED))
        original = RENAMED.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         '710cd3db295baa4a4dae5b939efd5aafa46391c680e542610728549f5464e8ba')
        before = ProToolsSession(str(RESAVED)).get_timeline_clips()
        before[0]['track'] = 'AAF_RENAMED_LONG'
        self.assertEqual(s.get_tracks(), ['AAF_RENAMED_LONG', 'B_INSIDE'])
        self.assertEqual(s.get_timeline_clips(), before)
        self.assertEqual(s.get_timeline_clip_groups(), [])
        self.assertEqual(len(s.get_clips()), 4)
        pointers = pointer_rows(s)
        with tempfile.TemporaryDirectory() as temp:
            for i in range(2):
                out = Path(temp) / ('cycle%d.ptx' % i)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
                self.assertEqual(pointer_rows(s), pointers)
        self.assertEqual(RENAMED.read_bytes(), original)

    def test_fill_native_anonymous_and_mixed_preserves_all_other_roots_and_targets(self):
        for path, count in [(RESAVED, 2), (RENAMED, 1)]:
            with self.subTest(path=path):
                s = ProToolsSession(str(path))
                rows = s.get_timeline_clips()
                old_pointers = pointer_rows(s)
                main = s._root_blocks(0x1054)[0]
                other_roots = [b.to_bytes()[0] if hasattr(b, 'to_bytes') else bytes(b)
                               for b in s.root_items if b is not main]
                events = [p.items[1].to_bytes()[0] for p, _, _ in s._validated_main_playlist_records()]
                self.assertEqual(s.fill_anonymous_playlist_names(), count)
                self.assertEqual(s.get_timeline_clips(), rows)
                self.assertEqual([b.to_bytes()[0] if hasattr(b, 'to_bytes') else bytes(b)
                                  for b in s.root_items if b is not main], other_roots)
                self.assertEqual([p.items[1].to_bytes()[0] for p, _, _ in s._validated_main_playlist_records()], events)
                self.assertEqual(s.fill_anonymous_playlist_names(), 0)
                with tempfile.TemporaryDirectory() as temp:
                    out = Path(temp) / 'filled.ptx'
                    s.save(str(out))
                    original = out.read_bytes()
                    for i in range(2):
                        s = ProToolsSession(str(out))
                        self.assertEqual(s.get_timeline_clips(), rows)
                        self.assertEqual(pointer_rows(s), old_pointers)
                        out = Path(temp) / ('cycle%d.ptx' % i)
                        s.save(str(out))
                        self.assertEqual(out.read_bytes(), original)


class VolumeIdentityCompositionTests(unittest.TestCase):
    def test_filled_mixed_channels_use_live_identities_not_cached_offsets(self):
        for widths in [(1, 2, 1), (2, 1), (2, 2, 1)]:
            with self.subTest(widths=widths):
                s, names = mixed_session(widths)
                for p in s._root_blocks(0x1054)[0].items[1:]:
                    p.items[0] = bytearray(8)
                s.data = object()
                self.assertEqual(s.fill_anonymous_playlist_names(), sum(widths))
                lanes = s._validated_named_audio_volume_lanes()
                untouched = [bytes(lane.items[0]) for _, lane, _ in lanes[:-1]]
                self.assertEqual(s.set_volume_automation(names[-1], [(1730448720, 6)],
                                                        replace=False), [(len(widths) - 1, 2)])
                rows = s.get_volume_automation()
                self.assertEqual([r['track'] for r in rows], list(names))
                self.assertEqual(rows[-1]['nodes'][-1]['sample'], 1730448720)
                self.assertEqual(rows[-1]['nodes'][-1]['db'], 6)
                self.assertEqual([bytes(lane.items[0]) for _, lane, _ in lanes[:-1]], untouched)

    def test_corrupt_live_identity_refuses_writer_without_cached_or_ordinal_fallback(self):
        for mode in ('slot', 'mirror'):
            s, names = mixed_session()
            s.data = object()
            if mode == 'slot':
                s._root_blocks(0x2624)[0].items[-1].get_all_blocks(0x2619)[0].items[-1][8] ^= 1
            else:
                p = s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[-1].items[0]
                p[6 + struct.unpack_from('<I', p, 2)[0] + 10] ^= 1
            before = snapshot(s)
            with self.assertRaisesRegex(ValueError, 'unverified track identities'):
                s.set_volume_automation(names[0], [(1730448720, 6)], replace=False)
            self.assertEqual(snapshot(s), before)


@unittest.skipUnless(FILLED_RESAVED.exists(), 'optional Pro Tools-resaved playlist-name output absent')
class NativeFilledOutputTests(unittest.TestCase):
    def test_pro_tools_resaved_names_placements_noop_and_exact_cycles(self):
        original = FILLED_RESAVED.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         '056f01c9815d06a1d85ae2ee002900cb4e28f8eac3f1248c078ae4930bcb3e4b')
        s = ProToolsSession(str(FILLED_RESAVED))
        self.assertEqual(s.get_tracks(), ['AAF_RENAMED_LONG', 'B_INSIDE'])
        rows = s.get_timeline_clips()
        self.assertEqual([(r['start_samples'], r['length_samples'], r['src_offset_samples'])
                          for r in rows], [(1729728000, 1355354, 0), (1730208480, 1355354, 0)])
        pointers = pointer_rows(s)
        self.assertEqual(len(pointers), 172)
        before = snapshot(s)
        self.assertEqual(s.fill_anonymous_playlist_names(), 0)
        self.assertEqual(snapshot(s), before)
        with tempfile.TemporaryDirectory() as temp:
            for i in range(2):
                out = Path(temp) / ('cycle%d.ptx' % i)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
                self.assertEqual(s.get_timeline_clips(), rows)
                self.assertEqual(pointer_rows(s), pointers)
        self.assertEqual(FILLED_RESAVED.read_bytes(), original)

    def test_existing_volume_resolver_on_aaf_names_restores_file_exactly(self):
        original = FILLED_RESAVED.read_bytes()
        s = ProToolsSession(str(FILLED_RESAVED))
        rows = s.get_timeline_clips()
        pointers = pointer_rows(s)
        lanes = s._validated_named_audio_volume_lanes()
        old = [bytes(lane.items[0]) for _, lane, _ in lanes]
        self.assertEqual(s.set_volume_automation('B_INSIDE', [(1730448720, 6)],
                                                replace=False, ordinal=1), [(1, 2)])
        self.assertEqual(bytes(lanes[0][1].items[0]), old[0])
        self.assertEqual(s.get_volume_automation('B_INSIDE')[0]['nodes'], [
            {'sample': 0, 'timecode': '00:00:00:00', 'db': 0.0},
            {'sample': 1730448720, 'timecode': '10:00:15:00', 'db': 6.0}])
        self.assertEqual(s.get_timeline_clips(), rows)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'volume.ptx'
            s.save(str(out))
            authored = out.read_bytes()
            for _ in range(2):
                s = ProToolsSession(str(out))
                self.assertEqual(pointer_rows(s), pointers)
                self.assertEqual(s.get_timeline_clips(), rows)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), authored)
            self.assertEqual(s.set_volume_automation('B_INSIDE', [(0, 0)]), [(1, 1)])
            s.save(str(out))
            self.assertEqual(out.read_bytes(), original)


@unittest.skipUnless(VOLUME_RESAVED.exists() and FILLED_RESAVED.exists(),
                     'optional Pro Tools-resaved AAF Volume output absent')
class NativeVolumeIdentityOutputTests(unittest.TestCase):
    def test_confirmed_native_target_with_user_moved_timestamp_and_exact_cycles(self):
        # The user explicitly moved the +6 dB point by one frame before saving.
        # Preserve that native position; this is not an API compensation oracle.
        original = VOLUME_RESAVED.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         '957f05f7a4634b245ded0c5b00b340882fe6ce8da911a3e72dbebf3c9966dbe0')
        s = ProToolsSession(str(VOLUME_RESAVED))
        base = ProToolsSession(str(FILLED_RESAVED))
        self.assertEqual(s.get_tracks(), base.get_tracks())
        self.assertEqual(s.get_clips(), base.get_clips())
        self.assertEqual(s.get_timeline_clips(), base.get_timeline_clips())
        self.assertEqual(s.get_timeline_clip_groups(), base.get_timeline_clip_groups())
        self.assertEqual(s.get_markers(), base.get_markers())
        self.assertEqual(s.get_volume_automation('AAF_RENAMED_LONG'),
                         base.get_volume_automation('AAF_RENAMED_LONG'))
        expected = [{'sample': 0, 'timecode': '00:00:00:00', 'db': 0.0},
                    {'sample': 1730450722, 'timecode': '10:00:15:01', 'db': 6.0}]
        self.assertEqual(s.get_volume_automation('B_INSIDE')[0]['nodes'], expected)
        pointers = pointer_rows(base)
        self.assertEqual(pointer_rows(s), pointers)
        states = lambda session: [slot.get_all_blocks(0x260d)[0]
                                  for slot in session._root_blocks(0x2624)[0].get_all_blocks(0x261c)]
        for before, after in zip(states(base), states(s)):
            for index in (2, 6, 8, 9, 11):
                self.assertEqual(after.items[index].to_bytes()[0], before.items[index].to_bytes()[0])
        self.assertEqual(s.fill_anonymous_playlist_names(), 0)
        with tempfile.TemporaryDirectory() as temp:
            for i in range(2):
                out = Path(temp) / ('cycle%d.ptx' % i)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
                self.assertEqual(s.get_volume_automation('B_INSIDE')[0]['nodes'], expected)
                self.assertEqual(pointer_rows(s), pointers)
        self.assertEqual(VOLUME_RESAVED.read_bytes(), original)
