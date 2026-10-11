"""Two native UTF-8 comment copies; opt-in reads preserve the old contract."""
import copy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import uuid

from pt_api import ProToolsSession
from tests.test_markers import (
    add_marker_track_catalogue, make_session, marker_blocks,
)

BEFORE = Path(__file__).parent / 'fixtures' / 'native_marker_comment_before.ptx'
AFTER = Path(__file__).parent / 'fixtures' / 'native_marker_comment_after.ptx'
RESAVED = Path(__file__).parent / 'fixtures' / 'native_marker_comment_pt_resaved.ptx'
TEXT = 'Commentaire API — café, été, façade.'


def raw_tree(session):
    return b''.join(b.to_bytes()[0] for b in session.root_items)


class MarkerCommentTests(unittest.TestCase):
    def test_unicode_copies_use_byte_lengths_and_shift_uuid(self):
        s, ruler = make_session()
        s.add_marker('Nom é', 48048, index=3, comment=TEXT)
        marker = marker_blocks(ruler)[0]
        p, tail = marker.items[0], marker.items[2]
        offset = 26 + len('Nom é'.encode()) + 60
        text = TEXT.encode()
        self.assertEqual(struct.unpack_from('<I', p, offset)[0], len(text))
        self.assertEqual(p[offset + 4:offset + 4 + len(text)], text)
        self.assertEqual(tail[4:8 + len(text)], struct.pack('<I', len(text)) + text)
        self.assertEqual(uuid.UUID(bytes=bytes(tail[23 + len(text):39 + len(text)])).version, 4)
        self.assertEqual(s.get_markers(include_comments=True), [
            dict(index=3, name='Nom é', timecode='00:00:01:00', comment=TEXT)])

    def test_default_read_contract_is_unchanged(self):
        s, _ = make_session()
        s.add_marker('M', 0, comment=TEXT)
        self.assertEqual(s.get_markers(), [dict(index=1, name='M', timecode='00:00:00:00')])
        self.assertNotIn('comment', s.get_markers()[0])

    def test_none_and_empty_comments_preserve_the_empty_shape(self):
        for comment in (None, ''):
            with self.subTest(comment=comment):
                s, ruler = make_session()
                s.add_marker('M', 0, comment=comment)
                marker = marker_blocks(ruler)[0]
                self.assertEqual(len(marker.items[0]), 137)
                self.assertEqual(len(marker.items[2]), 51)
                self.assertEqual(s.get_markers(include_comments=True)[0]['comment'], '')

    def test_large_comment_has_no_arbitrary_uint16_cutoff(self):
        s, _ = make_session()
        text = 'é' * 32769
        s.add_marker('M', 0, comment=text)
        self.assertEqual(s.get_markers(include_comments=True)[0]['comment'], text)

    def test_invalid_comment_arguments_are_atomic(self):
        for text, error in [(42, TypeError), (b'bytes', TypeError),
                            ('bad\x00text', ValueError), ('bad\ud800', ValueError)]:
            with self.subTest(text=repr(text)):
                s, _ = make_session()
                original = raw_tree(s)
                with self.assertRaises(error):
                    s.add_marker('M', 0, comment=text)
                self.assertEqual(raw_tree(s), original)

    def test_include_comments_requires_boolean(self):
        s, _ = make_session()
        for value in (1, None, 'yes'):
            with self.assertRaisesRegex(TypeError, 'Boolean'):
                s.get_markers(include_comments=value)

    def test_unknown_geometry_is_only_refused_on_comment_opt_in(self):
        s, ruler = make_session()
        s.add_marker('M', 0)
        marker = marker_blocks(ruler)[0]
        marker.items.insert(2, copy.deepcopy(marker.items[1]))
        original = raw_tree(s)
        self.assertEqual(s.get_markers()[0]['name'], 'M')
        with self.assertRaisesRegex(ValueError, 'one-property shape'):
            s.get_markers(include_comments=True)
        self.assertEqual(raw_tree(s), original)

    def test_bounds_lengths_mirrors_and_envelope_are_checked_without_mutation(self):
        for mode in ('primary length', 'secondary length', 'mismatch',
                     'invalid UTF-8', 'NUL', 'envelope', 'range', 'child', 'assignment'):
            with self.subTest(mode=mode):
                s, ruler = make_session()
                s.add_marker('M', 0, comment='abc')
                m = marker_blocks(ruler)[0]
                p, tail = bytearray(m.items[0]), bytearray(m.items[2])
                offset = 87  # 26 + one-byte name + 60.
                if mode == 'primary length':
                    struct.pack_into('<I', p, offset, 0xFFFFFFFF)
                elif mode == 'secondary length':
                    struct.pack_into('<I', tail, 4, 2)
                elif mode == 'mismatch':
                    tail[8] = ord('z')
                elif mode == 'invalid UTF-8':
                    p[offset + 4] = tail[8] = 0xFF
                elif mode == 'NUL':
                    p[offset + 4] = tail[8] = 0
                elif mode == 'envelope':
                    tail[-1] = 0
                elif mode == 'range':
                    struct.pack_into('<q', p, 19, 1)
                elif mode == 'child':
                    m.items[1].items[0] = bytes(7)
                elif mode == 'assignment':
                    m.items[-1] = bytes(9)
                m.items[0], m.items[2] = p, tail
                original = raw_tree(s)
                with self.assertRaises(ValueError):
                    s.get_markers(include_comments=True)
                self.assertEqual(raw_tree(s), original)

    def test_comments_use_live_tree_and_reads_are_immutable(self):
        s, ruler = make_session()
        s.data = b'not the live session'
        s.add_marker('M', 0, comment='abc')
        m = marker_blocks(ruler)[0]
        p, tail = bytearray(m.items[0]), bytearray(m.items[2])
        p[91:94] = tail[8:11] = b'xyz'
        m.items[0], m.items[2] = p, tail
        original = raw_tree(s)
        self.assertEqual(s.get_markers(include_comments=True)[0]['comment'], 'xyz')
        self.assertEqual(raw_tree(s), original)

    def test_marker_track_filter_composes_with_comment_read(self):
        s, ruler = make_session()
        s.add_marker('A', 0, comment='first')
        s.add_marker('B', 48048, comment=TEXT)
        for track_id, marker in enumerate(marker_blocks(ruler)):
            marker.items[-1] = struct.pack('<II', 0, track_id)
        add_marker_track_catalogue(s, ['Ruler A', 'Ruler B'])
        self.assertEqual(s.get_markers('Ruler B', include_comments=True), [
            dict(index=2, name='B', timecode='00:00:01:00', comment=TEXT)])

    def test_composition_keeps_existing_marker_exact(self):
        s, ruler = make_session()
        s.add_marker('A', 0, comment='first')
        first = marker_blocks(ruler)[0].to_bytes()[0]
        s.add_marker('B', 48048, comment=TEXT)
        self.assertEqual(marker_blocks(ruler)[0].to_bytes()[0], first)
        self.assertEqual([m['comment'] for m in s.get_markers(include_comments=True)], ['first', TEXT])


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'optional native comment pair absent')
class NativeMarkerCommentTests(unittest.TestCase):
    def test_native_pair_and_two_exact_cycles(self):
        for path, expected, digest in (
                (BEFORE, '', 'ed0a513baf6f5b92d583cb6c967fe083cc956d8cd95e3050ee734770a7bf41b6'),
                (AFTER, TEXT, '4ec0700e7c2bc4fac113e016d538e87e5eee1b932d7790c1b22dffaf32380cab')):
            with self.subTest(path=path):
                original = path.read_bytes()
                self.assertEqual(hashlib.sha256(original).hexdigest(), digest)
                s = ProToolsSession(str(path))
                self.assertEqual((s.sample_rate, s.frame_rate_enum, s.get_tracks()), (48000, 9, ['Audio 1']))
                self.assertEqual(s.get_markers(include_comments=True), [
                    dict(index=1, name='API_IMPORT_TEST', timecode='10:00:10:00', comment=expected)])
                with tempfile.TemporaryDirectory() as tmp:
                    for index in range(2):
                        target = Path(tmp) / ('cycle%d.ptx' % index)
                        s.save(str(target))
                        self.assertEqual(target.read_bytes(), original)
                        s = ProToolsSession(str(target))
                self.assertEqual(path.read_bytes(), original)

    def test_writer_reconstructs_native_marker_except_random_uuid(self):
        s, native = ProToolsSession(str(BEFORE)), ProToolsSession(str(AFTER))
        s.delete_marker(index=1)
        s.add_marker('API_IMPORT_TEST', 1730208480, index=1, comment=TEXT)
        written = s._root_blocks(0x2030)[0].get_all_blocks(0x2077)[0]
        expected = native._root_blocks(0x2030)[0].get_all_blocks(0x2077)[0]
        uuid_start = 23 + len(TEXT.encode())
        normalized = copy.deepcopy(written)
        tail = bytearray(normalized.items[2])
        tail[uuid_start:uuid_start + 16] = expected.items[2][uuid_start:uuid_start + 16]
        normalized.items[2] = tail
        self.assertEqual(normalized.to_bytes()[0], expected.to_bytes()[0])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'written.ptx'
            s.save(str(target))
            original = target.read_bytes()
            for index in range(2):
                reloaded = ProToolsSession(str(target))
                self.assertEqual(reloaded.get_markers(include_comments=True)[0]['comment'], TEXT)
                target = Path(tmp) / ('cycle%d.ptx' % index)
                reloaded.save(str(target))
                self.assertEqual(target.read_bytes(), original)


@unittest.skipUnless(RESAVED.exists(), 'optional Pro Tools-resaved comment output absent')
class NativeResavedMarkerCommentTests(unittest.TestCase):
    def test_pro_tools_save_preserves_both_markers_comments_and_exact_cycles(self):
        original = RESAVED.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         '7130bcbef15641ed044b3cca092f088c35e4429150b17d551aff8e453ad3eb19')
        s = ProToolsSession(str(RESAVED))
        self.assertEqual((s.sample_rate, s.frame_rate_enum, s.get_tracks()),
                         (48000, 9, ['Audio 1']))
        expected = [dict(index=1, name='API_IMPORT_TEST', timecode='10:00:10:00', comment=''),
                    dict(index=2, name='API_COMMENT_TEST', timecode='10:00:20:00', comment=TEXT)]
        self.assertEqual(s.get_markers(include_comments=True), expected)
        markers = s._root_blocks(0x2030)[0].get_all_blocks(0x2077)
        for marker, position in zip(markers, (1730208480, 1730688960)):
            nlen = struct.unpack_from('<I', marker.items[0], 6)[0]
            self.assertEqual(struct.unpack_from('<qq', marker.items[0], 10 + nlen),
                             (position, position))
            self.assertEqual(len(marker.get_all_blocks(0x2506)), 1)
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(2):
                target = Path(tmp) / ('cycle%d.ptx' % i)
                s.save(str(target))
                self.assertEqual(target.read_bytes(), original)
                s = ProToolsSession(str(target))
                self.assertEqual(s.get_markers(include_comments=True), expected)
        self.assertEqual(RESAVED.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
