"""Atomic legacy point-marker deletion and subtree TOC purge regression."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from pt_api import PTBlock, ProToolsSession, xor_session
from tests.test_markers import make_session, marker_blocks
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_loading import minimal_ptx, PREFIX_0002, SESSION_METADATA

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_marker_delete_before.ptx'
ONE = FIXTURES / 'native_marker_delete_one.ptx'
ALL = FIXTURES / 'native_marker_delete_all.ptx'
RESAVED_ONE = FIXTURES / 'native_marker_delete_one_pt_resaved.ptx'
RESAVED_ALL = FIXTURES / 'native_marker_delete_all_pt_resaved.ptx'


def three_markers():
    s, ruler = make_session()
    for index, name in ((1, 'Début'), (2, 'Même nom'), (3, 'Même nom')):
        s.add_marker(name, index * 48048, index=index)
    return s, ruler


class MarkerDeleteTests(unittest.TestCase):
    def test_index_and_name_and_both_exact_selectors(self):
        for kwargs, expected in (({'index': 2}, [1, 3]),
                                 ({'name': 'Même nom'}, [1]),
                                 ({'index': 3, 'name': 'Même nom'}, [1, 2])):
            s, ruler = three_markers()
            survivors = {struct.unpack_from('<H', m.items[0])[0]: m for m in marker_blocks(ruler)}
            expected_tree = copy.deepcopy(s)
            er = expected_tree._root_blocks(0x2030)[0]
            er.items = [struct.pack('<I', len(expected)),
                        *[m for m in marker_blocks(er) if struct.unpack_from('<H', m.items[0])[0] in expected],
                        b'TRAILER!']
            self.assertEqual(s.delete_marker(**kwargs), 3 - len(expected))
            self.assertEqual(snapshot(s), snapshot(expected_tree))
            self.assertEqual([m['index'] for m in s.get_markers()], expected)
            for index, marker in zip(expected, marker_blocks(ruler)):
                self.assertIs(marker, survivors[index])

    def test_no_match_and_empty_container_preserve_all_objects_and_metadata(self):
        s, ruler = three_markers()
        before = snapshot(s)
        items, offsets = ruler.items, s._removed_offsets
        for kwargs in ({'index': 99}, {'name': 'même nom'}, {'index': 1, 'name': 'Même nom'}):
            self.assertEqual(s.delete_marker(**kwargs), 0)
            self.assertEqual(snapshot(s), before)
            self.assertIs(ruler.items, items)
            self.assertIs(s._removed_offsets, offsets)
        s, ruler = make_session()
        items = ruler.items
        self.assertEqual(s.delete_marker(index=1), 0)
        self.assertIs(ruler.items, items)

    def test_last_deletion_preserves_opaque_nonzero_trailer_and_can_add_again(self):
        s, ruler = three_markers()
        self.assertEqual(s.delete_marker(name='Même nom'), 2)
        self.assertEqual(s.delete_marker(index=1), 1)
        self.assertEqual(ruler.items, [bytes(4) + b'TRAILER!'])
        self.assertEqual(s.get_markers(), [])
        self.assertEqual(s.add_marker('', 0), 1)
        self.assertEqual(s.delete_marker(name=''), 1)
        self.assertEqual(ruler.items, [bytes(4) + b'TRAILER!'])

    def test_input_types_ranges_and_names_are_strict_and_atomic(self):
        s, _ = three_markers()
        before = snapshot(s)
        for kwargs, error in (({}, ValueError), ({'index': True}, TypeError),
                              ({'index': 1.0}, TypeError), ({'index': 0}, ValueError),
                              ({'index': 65536}, ValueError), ({'name': 1}, TypeError),
                              ({'name': 'Bad\x00Name'}, ValueError),
                              ({'name': '\ud800'}, ValueError)):
            with self.assertRaises(error): s.delete_marker(**kwargs)
            self.assertEqual(snapshot(s), before)

    def test_all_markers_validated_even_late_nonselected_one_and_no_match(self):
        for variant in ('header', 'name_length', 'utf8', 'nul', 'time', 'secondary',
                        'child', 'assignment', 'modern', 'duplicate'):
            s, ruler = three_markers()
            m = marker_blocks(ruler)[-1]
            if variant == 'header': m.items[0][2] = 0
            elif variant == 'name_length': struct.pack_into('<I', m.items[0], 6, 9999)
            elif variant == 'utf8': m.items[0][10] = 0xff
            elif variant == 'nul': m.items[0][10] = 0
            elif variant == 'time':
                n = struct.unpack_from('<I', m.items[0], 6)[0]
                struct.pack_into('<q', m.items[0], 18 + n, 1)
            elif variant == 'secondary': m.items[2][-1] = 0
            elif variant == 'child': m.items[3].content_type = 0x4827
            elif variant == 'assignment': m.items[-1][-1] = 1
            elif variant == 'modern': m.items.pop(1)
            else: struct.pack_into('<H', m.items[0], 0, 1)
            before = snapshot(s)
            offsets = s._removed_offsets
            for index in (1, 99):
                with self.assertRaises(ValueError): s.delete_marker(index=index)
                self.assertEqual(snapshot(s), before)
                self.assertIs(s._removed_offsets, offsets)

    def test_container_and_ruler_unknown_or_ambiguous_are_atomic(self):
        for variant in ('count', 'trailer', 'type', 'missing', 'ambiguous', 'nested', 'endian'):
            s, ruler = three_markers()
            if variant == 'count': struct.pack_into('<I', ruler.items[0], 0, 99)
            elif variant == 'trailer': ruler.items[-1] = bytes(7)
            elif variant == 'type': ruler.block_type = 3
            elif variant == 'missing': s.root_items.remove(ruler)
            elif variant == 'ambiguous': s.root_items.append(copy.deepcopy(ruler))
            elif variant == 'nested':
                holder = PTBlock(1, 0x2000, 1)
                holder.items = marker_blocks(ruler)
                ruler.items = [holder]
            else: s.is_bigendian = True
            before = snapshot(s)
            with self.assertRaises(ValueError): s.delete_marker(index=1)
            self.assertEqual(snapshot(s), before)

    def test_preparation_failure_does_not_mutate_tree_or_bookkeeping(self):
        s, _ = three_markers()
        s._removed_block_types = {99: 0x2077}
        before = snapshot(s)
        offsets, types = s._removed_offsets, s._removed_block_types
        with mock.patch('pt_api.struct.pack', side_effect=RuntimeError('encoding')):
            with self.assertRaises(RuntimeError): s.delete_marker(index=1)
        with mock.patch.object(s, '_collect_offsets_recursive', side_effect=RuntimeError('collect')):
            with self.assertRaises(RuntimeError): s.delete_marker(index=1)
        self.assertEqual(snapshot(s), before)
        self.assertIs(s._removed_offsets, offsets)
        self.assertIs(s._removed_block_types, types)

    def test_standard_references_to_marker_and_descendants_are_purged_not_reused(self):
        s, ruler = three_markers()
        raw, _ = ruler.to_bytes(False, 20 + 11 + len(SESSION_METADATA))
        # Serialized positions derived structurally, never searched by marker name.
        start = 20 + 11 + len(SESSION_METADATA)
        marker_start = start + 9 + 4
        first = marker_blocks(ruler)[0]
        child_start = marker_start + 9 + len(first.items[0])
        third_start = marker_start + sum(len(m.to_bytes()[0]) for m in marker_blocks(ruler)[:2])
        records = [PREFIX_0002 + struct.pack('<I', x) + bytes(3)
                   for x in (marker_start, child_start, third_start)]
        plain = minimal_ptx(struct.pack('>H', 3) + b''.join(records), middle=raw)
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / 'source.ptx', Path(d) / 'out.ptx'
            xor_session(plain, str(src))
            s = ProToolsSession(str(src))
            self.assertEqual(s.delete_marker(index=1), 1)
            self.assertIn(marker_start, s._removed_offsets)
            self.assertIn(child_start, s._removed_offsets)
            s.save(str(out))
            loaded = ProToolsSession(str(out))
            self.assertEqual([m['index'] for m in loaded.get_markers()], [2, 3])
            p = bytes(loaded._raw_0002_payload(loaded._root_blocks(2)[0]))
            runs = loaded._validate_0002_record_layout(p)
            self.assertEqual(len(runs), 1)
            self.assertEqual(struct.unpack_from('<I', p, runs[0][1])[0],
                             marker_blocks(loaded._root_blocks(0x2030)[0])[-1].original_offset)
            self.assertEqual(loaded.delete_marker(index=2), 1)
            self.assertEqual(loaded.delete_marker(index=3), 1)
            loaded.save(str(out))
            empty = ProToolsSession(str(out))
            self.assertEqual(empty.get_markers(), [])
            self.assertEqual(len(empty._validate_0002_record_layout(
                bytes(empty._raw_0002_payload(empty._root_blocks(2)[0])))), 0)

    def test_deletion_never_converts_marker_samples_using_session_frame_rate(self):
        for rate, enum in ((48000, 2), (48000, 9), (44100, 9), (96000, 2)):
            s, ruler = three_markers()
            s.sample_rate, s.frame_rate_enum = rate, enum
            first, _, third = marker_blocks(ruler)
            originals = [first.to_bytes()[0], third.to_bytes()[0]]
            self.assertEqual(s.delete_marker(index=2), 1)
            self.assertEqual([m.to_bytes()[0] for m in marker_blocks(ruler)], originals)


@unittest.skipUnless(all(p.exists() for p in (BEFORE, ONE, ALL)), 'Local native marker deletion oracles absent')
class NativeMarkerDeleteTests(unittest.TestCase):
    def test_all_native_oracles_are_byte_identical_across_two_noop_cycles(self):
        for path in (BEFORE, ONE, ALL):
            s = ProToolsSession(str(path))
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / 'noop.ptx'
                for _ in range(2):
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), path.read_bytes())
                    s = ProToolsSession(str(out))

    def test_toc_or_bookkeeping_invalid_refuses_before_mutation(self):
        for variant in ('missing', 'malformed', 'conflict'):
            s = ProToolsSession(str(BEFORE))
            marker = marker_blocks(s._root_blocks(0x2030)[0])[0]
            table = s._root_blocks(2)[0]
            s._removed_offsets = [99]
            s._removed_block_types = {99: 0x2077}
            if variant == 'missing': s.root_items.remove(table)
            elif variant == 'malformed':
                p = bytes(s._raw_0002_payload(table))
                first, pos = s._validate_0002_record_layout(p)[0]
                table.items = [p[:pos + 2]]
            else: s._removed_block_types[marker.original_offset] = 0x9999
            before = snapshot(s)
            offsets, types = s._removed_offsets, s._removed_block_types
            with self.assertRaises(ValueError): s.delete_marker(index=1)
            self.assertEqual(snapshot(s), before)
            self.assertIs(s._removed_offsets, offsets)
            self.assertIs(s._removed_block_types, types)

    def test_native_container_geometry_and_survivor_identity_except_native_uuid(self):
        a, b, c = [ProToolsSession(str(p)) for p in (BEFORE, ONE, ALL)]
        self.assertEqual([m['index'] for m in b.get_markers()], [1, 3])
        self.assertEqual(c.get_markers(), [])
        source = marker_blocks(a._root_blocks(0x2030)[0])
        after = marker_blocks(b._root_blocks(0x2030)[0])
        for left, right in zip((source[0], source[2]), after):
            copied = copy.deepcopy(left)
            p = bytearray(copied.items[2])
            p[23:39] = right.items[2][23:39]
            copied.items[2] = p
            self.assertEqual(copied.to_bytes()[0], right.to_bytes()[0])
        self.assertEqual(c._root_blocks(0x2030)[0].items, [bytes(12)])

    def test_api_partial_and_empty_keep_other_roots_exact_and_pass_cycles(self):
        for indices, expected in (((2,), [1, 3]), ((1, 2, 3), [])):
            s = ProToolsSession(str(BEFORE))
            original = {id(b): b.to_bytes()[0] for b in s.root_items if isinstance(b, PTBlock)
                        and b.content_type not in (0x2030, 0x2519)}
            expected_catalogue = copy.deepcopy(s._root_blocks(0x2519)[0])
            zero_entry = expected_catalogue.get_all_blocks(0x251c)[0]
            raw = bytearray(zero_entry.items[0])
            raw[6] = int(bool(expected))
            zero_entry.items[0] = bytes(raw)
            for index in indices: self.assertEqual(s.delete_marker(index=index), 1)
            self.assertEqual([m['index'] for m in s.get_markers()], expected)
            for b in s.root_items:
                if id(b) in original: self.assertEqual(b.to_bytes()[0], original[id(b)])
            self.assertEqual(s._root_blocks(0x2519)[0].to_bytes()[0], expected_catalogue.to_bytes()[0])
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / 'candidate.ptx'
                s.save(str(out))
                data = out.read_bytes()
                for _ in range(2):
                    s = ProToolsSession(str(out))
                    self.assertEqual([m['index'] for m in s.get_markers()], expected)
                    self.assertEqual(s.delete_marker(index=99), 0)
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), data)


@unittest.skipUnless(all(p.exists() for p in (BEFORE, RESAVED_ONE, RESAVED_ALL)),
                     'Local Pro Tools-resaved deletion candidates absent')
class ResavedMarkerDeleteTests(unittest.TestCase):
    def check_saved(self, path, indices):
        base, saved = ProToolsSession(str(BEFORE)), ProToolsSession(str(path))
        self.assertEqual([m['index'] for m in saved.get_markers()], indices)
        source = {struct.unpack_from('<H', m.items[0])[0]: m
                  for m in marker_blocks(base._root_blocks(0x2030)[0])}
        for index, marker in zip(indices, marker_blocks(saved._root_blocks(0x2030)[0])):
            self.assertEqual(marker.to_bytes()[0], source[index].to_bytes()[0])
        for method in ('get_tracks', 'get_clips', 'get_timeline_clips'):
            self.assertEqual(getattr(saved, method)(), getattr(base, method)())
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'cycle.ptx'
            for _ in range(2):
                original_items = saved._root_blocks(0x2030)[0].items
                self.assertEqual(saved.delete_marker(index=99), 0)
                self.assertIs(saved._root_blocks(0x2030)[0].items, original_items)
                saved.save(str(out))
                self.assertEqual(out.read_bytes(), path.read_bytes())
                saved = ProToolsSession(str(out))
            for index in indices: self.assertEqual(saved.delete_marker(index=index), 1)
            saved.save(str(out))
            emptied = ProToolsSession(str(out))
            self.assertEqual(emptied.get_markers(), [])
            self.assertEqual(emptied._root_blocks(0x2030)[0].items, [bytes(12)])

    def test_partial_native_save_keeps_exact_survivors_and_can_finish_deletion(self):
        self.check_saved(RESAVED_ONE, [1, 3])

    def test_empty_native_save_remains_exact_and_noop(self):
        self.check_saved(RESAVED_ALL, [])


if __name__ == '__main__':
    unittest.main()
