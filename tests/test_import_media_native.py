"""Read-only PCM24 import oracles; isolated candidate is not a public writer."""
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession, PTBlock
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
FILES = [FIXTURES / ('native_import_pcm24_%s.ptx' % label)
         for label in ('before', 'after', 'candidate')]


def one(parent, content_type):
    matches = [x for x in parent.items
               if isinstance(x, PTBlock) and x.content_type == content_type]
    assert len(matches) == 1
    return matches[0]


@unittest.skipUnless(all(p.exists() for p in FILES), 'Local native PCM24 import fixtures required')
class NativeImportPCM24Tests(unittest.TestCase):
    def sessions(self):
        return [ProToolsSession(str(p)) for p in FILES]

    def test_native_append_geometry_catalog_and_independent_bwf_timestamp(self):
        before, after, candidate = self.sessions()
        old = before.get_timeline_clips()
        self.assertEqual(len(old), 1)
        for s in (after, candidate):
            self.assertEqual(s.get_tracks(), before.get_tracks())
            timeline = s.get_timeline_clips()
            self.assertEqual(timeline[:1], old)
            self.assertEqual(len(timeline), 2)
            clip = timeline[1]
            self.assertEqual((clip['start_samples'], clip['end_samples'],
                              clip['length_samples'], clip['src_offset_samples']),
                             (1738456672, 1747137344, 8680672, 0))
            self.assertEqual(clip['physical_filename'], 'Audio 2_01.wav')
            self.assertEqual(s._validated_physical_audio_catalog()[3],
                             ['Audio 1_01.wav', 'Audio 2_01.wav'])
            d = s._root_blocks(0x262a)[0].items[-1]
            payload = one(d, 0x2628).items[0]
            a = 4 + struct.unpack_from('<I', payload)[0]
            self.assertEqual(payload[a:a + 5], bytes.fromhex('0000304400'))
            self.assertEqual(int.from_bytes(payload[a + 5:a + 8], 'little'), 8680672)
            self.assertEqual(struct.unpack_from('<II', payload, a + 8), (1729728000, 1729728000))
            self.assertEqual(payload[a + 36:a + 48], bytes(12))
        self.assertEqual(after.get_timeline_clips(), candidate.get_timeline_clips())

    def test_new_media_identity_matches_native_and_clip_media_index_not_bin_ordinal(self):
        _, after, candidate = self.sessions()
        for s in (after, candidate):
            entry = s._validated_physical_audio_catalog()[1][1]
            info = one(entry, 0x1001).items[0]
            self.assertEqual(len(info), 31)
            self.assertEqual(info[:6], bytes.fromhex('80bb00000118'))
            self.assertEqual(int.from_bytes(info[6:9], 'little'), 8680672)
            self.assertEqual(info[22:31], bytes.fromhex('2a9b80523aef858000'))
            detail = one(entry, 0x2106)
            self.assertEqual([len(x) if isinstance(x, (bytes, bytearray)) else x.content_type
                              for x in detail.items], [151, 0x4301, 58])
            self.assertEqual(struct.unpack_from('<I', detail.items[0], 100)[0], 1729728000)
            self.assertEqual(detail.items[2][18:50], bytes.fromhex(
                '060a2b340101010501010f10130000009b80523aef858000c989cb5acce13e00'))
            d = s._root_blocks(0x262a)[0].items[-1]
            (_, _, identity), (_, _, link) = s._validated_2629_fixed_records(d)
            self.assertEqual(struct.unpack_from('<I', identity)[0], 1)
            self.assertEqual(struct.unpack_from('<I', link, 96)[0], 1)
            self.assertTrue(any(identity[23:39]))

    def test_candidate_preserves_existing_records_and_matches_native_new_parent(self):
        before, after, candidate = self.sessions()
        self.assertEqual(before._validated_physical_audio_catalog()[1][0].to_bytes()[0],
                         candidate._validated_physical_audio_catalog()[1][0].to_bytes()[0])
        b = before._root_blocks(0x262a)[0]
        c = candidate._root_blocks(0x262a)[0]
        self.assertEqual([x.to_bytes()[0] for x in b.items[1:]],
                         [x.to_bytes()[0] for x in c.items[1:3]])
        self.assertEqual(before._validated_main_playlists()[0][2][0].to_bytes()[0],
                         candidate._validated_main_playlists()[0][2][0].to_bytes()[0])
        self.assertEqual(one(c.items[-1], 0x2628).to_bytes()[0],
                         one(after._root_blocks(0x262a)[0].items[-1], 0x2628).to_bytes()[0])
        self.assertEqual(len(pointer_rows(before)), 167)
        self.assertEqual(len(pointer_rows(candidate)), 167)

    def test_native_and_candidate_two_exact_save_reload_cycles(self):
        with tempfile.TemporaryDirectory() as folder:
            for original, s in zip(FILES, self.sessions()):
                for i in range(2):
                    path = Path(folder) / (original.stem + str(i) + '.ptx')
                    s.save(str(path))
                    self.assertEqual(path.read_bytes(), original.read_bytes())
                    s = ProToolsSession(str(path))


if __name__ == '__main__':
    unittest.main()
