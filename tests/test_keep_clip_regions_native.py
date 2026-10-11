"""Native fragmentation oracle and isolated output; NO public writer integrated."""
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession, PTBlock
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_trim_inplace_before.ptx'
AFTER = FIXTURES / 'native_keep_clip_regions_after.ptx'
CANDIDATE = FIXTURES / 'native_keep_clip_regions_candidate.ptx'
TAIL = bytes.fromhex('fffffffffffffffffeff00000000ffff04000400') + bytes(21) + bytes.fromhex('ffffffff0000')
GEOMETRY = [(1729728100, 1729776100, 100), (1729872100, 1729920100, 144100)]


def definitions(s):
    return s._root_blocks(0x262a)[0].get_all_blocks(0x2629)


def attributes(d):
    p = bytes(d.get_all_blocks(0x2628)[0].items[0])
    return p[4 + struct.unpack_from('<I', p, 0)[0]:]


def geometry(s):
    return [(x['start_samples'], x['end_samples'], x['src_offset_samples'])
            for x in s.get_timeline_clips()]


@unittest.skipUnless(all(p.exists() for p in (BEFORE, AFTER, CANDIDATE)),
                         'Local native fragmentation/candidate fixtures absent')
class NativeKeepClipRegionsTests(unittest.TestCase):
    def test_native_two_regions_have_exact_widths_timestamps_and_no_payload_uuid(self):
        s = ProToolsSession(str(AFTER))
        self.assertEqual(geometry(s), GEOMETRY)
        self.assertEqual(s.get_tracks(), ['Audio 1'])
        ds = definitions(s)
        self.assertEqual(len(ds), 5)  # parent, two retained, two deleted edit-history pieces
        for ordinal, source, width, start, header in (
                (1, 100, 1, 1729728100, bytes.fromhex('0110204408')),
                (3, 144100, 3, 1729872100, bytes.fromhex('0130204408'))):
            self.assertEqual(attributes(ds[ordinal]), header + source.to_bytes(width, 'little')
                             + (48000).to_bytes(2, 'little') + struct.pack('<II', start, start) + TAIL)
            self.assertEqual(len(ds[ordinal].items[1]), 48)
            self.assertEqual(ds[ordinal].items[1][22], 0xff)
        for entry, ordinal in zip(s._validated_main_timeline_events(), (1, 3)):
            self.assertEqual(struct.unpack_from('<I', entry[3], 2)[0], ordinal)
            self.assertEqual(bytes(entry[1].items[1]), b'\x00\x01\x01')
        self.assertTrue(callable(s.keep_clip_regions))

    def test_candidate_native_fields_ordinals_identity_parent_and_other_roots(self):
        s = ProToolsSession(str(CANDIDATE))
        before = ProToolsSession(str(BEFORE))
        native = ProToolsSession(str(AFTER))
        self.assertEqual(geometry(s), GEOMETRY)
        self.assertEqual(s.get_tracks(), before.get_tracks())
        ds, original, native_ds = definitions(s), definitions(before), definitions(native)
        self.assertEqual(len(ds), 3)  # no unnecessary native edit-history definitions
        self.assertEqual(ds[0].to_bytes()[0], original[0].to_bytes()[0])
        self.assertEqual(ds[1].items[1], original[1].items[1])
        self.assertNotEqual(ds[2].items[1][23:39], original[1].items[1][23:39])
        self.assertEqual(ds[2].items[1][:23], original[1].items[1][:23])
        self.assertEqual(ds[2].items[1][39:], original[1].items[1][39:])
        for i, native_id in ((1, 1), (2, 3)):
            self.assertEqual(attributes(ds[i]), attributes(native_ds[native_id]))
            self.assertEqual(ds[i].items[2].to_bytes()[0], original[1].items[2].to_bytes()[0])
            self.assertEqual(ds[i].items[3], original[1].items[3])
            event = s._validated_main_timeline_events()[i - 1]
            self.assertEqual(struct.unpack_from('<I', event[3], 2)[0], i)
            expected = bytearray(native._validated_main_timeline_events()[i - 1][3])
            struct.pack_into('<I', expected, 2, i)
            self.assertEqual(event[3], expected)
        self.assertEqual(len(s.root_items), len(before.root_items))
        for i, (a, b) in enumerate(zip(s.root_items, before.root_items)):
            # Special pointer root is represented by its pointer low UInt16.
            if i and isinstance(a, PTBlock) and a.content_type not in (0x262a, 0x1054, 2):
                self.assertEqual(a.to_bytes()[0], b.to_bytes()[0])
        self.assertEqual(pointer_rows(s), pointer_rows(before))
        self.assertEqual(len(pointer_rows(s)), 167)
        self.assertEqual([x['clip_name'] for x in s.get_timeline_clips()],
                         ['Audio 1_01-01', 'Audio 1_01-01#1729872100'])
        self.assertEqual([x['physical_filename'] for x in s.get_timeline_clips()],
                         ['Audio 1_01.wav', 'Audio 1_01.wav'])

    def test_oracles_and_candidate_pass_two_byte_identical_cycles_each(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'cycle.ptx'
            for path in (BEFORE, AFTER, CANDIDATE):
                original = path.read_bytes()
                s = ProToolsSession(str(path))
                clips, tracks, pointers = s.get_timeline_clips(), s.get_tracks(), pointer_rows(s)
                for _ in range(2):
                    s.save(str(out))
                    self.assertEqual(out.read_bytes(), original)
                    s = ProToolsSession(str(out))
                    self.assertEqual(s.get_timeline_clips(), clips)
                    self.assertEqual(s.get_tracks(), tracks)
                    self.assertEqual(pointer_rows(s), pointers)
