"""Verified PCM24 append import: transactional preparation, no file writes."""
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock
import uuid

from pt_api import ProToolsSession, _inspect_pcm24_import_wave
from tests.test_timeline_read import block, playlist, event, make_session
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_import_media_native import FILES, one
from tests.test_stereo_to_stereo_bus_output import pointer_rows

RESAVED = FILES[0].parent / 'native_import_pcm24_pt_resaved.ptx'
LINK_PREFIX = bytes.fromhex('000001007f4d644368756e01004f0000004d644e4c4203001600000000000000000000000b00000000000000010001000000000000004d64455301000f0000004d6454454c01000400000000000000000000002a000000')


def wave_bytes(stem, reference=3000, samples=100, token=0x70):
    umid = bytes.fromhex('060a2b340101010501010f1013000000') + bytes([token]) * 16
    identifier = b'\x2a' + umid[16:24]
    fmt = struct.pack('<HHIIHH', 1, 1, 48000, 144000, 3, 24) + bytes(24)
    bext = bytearray(602)
    struct.pack_into('<Q', bext, 338, reference)
    bext[348:380] = umid
    regn = bytearray(92)
    regn[11:20] = identifier
    struct.pack_into('<Q', regn, 28, samples)
    struct.pack_into('<QQ', regn, 44, reference, reference)
    encoded = stem.encode('utf-8')
    regn[60] = len(encoded)
    regn[61:61 + len(encoded)] = encoded
    umid_chunk = bytearray(24)
    umid_chunk[7:16] = identifier
    chunks = [(b'fmt ', fmt), (b'bext', bext), (b'minf', bytes(16)),
              (b'regn', regn), (b'umid', umid_chunk), (b'data', bytes(samples * 3))]
    body = b'WAVE' + b''.join(k + struct.pack('<I', len(v)) + v + bytes(len(v) % 2)
                             for k, v in chunks)
    return b'RIFF' + struct.pack('<I', len(body)) + body


def session(folder):
    attributes = (bytes.fromhex('0000304400') + (60000).to_bytes(3, 'little')
                  + struct.pack('<II', 2000, 2000)
                  + bytes.fromhex('feff00000000ffff040004000000000000000000')
                  + bytes.fromhex('2a000000fc14e8e6a7235cd4')
                  + bytes.fromhex('00ffffffff0000'))
    identity = (bytes.fromhex('0000000000000000803f0000803f0000000000000000ff')
                + uuid.UUID(int=10).bytes + bytes(9))
    definition = block(11, 0x2629, [
        block(4, 0x2628, [bytearray(struct.pack('<I', 3) + b'OLD' + attributes)]),
        bytearray(identity), block(1, 0x4403, [block(0, 0x1000, [bytes(18)]), b'\x00']),
        bytearray(LINK_PREFIX + bytes(8) + b'\x01' + bytes(8))])
    e = event(0, 100000)
    e.items[0].block_type = 10
    s = make_session([definition], [playlist('TRACK', [e]), playlist('OTHER', [])])
    s.frame_rate_enum = 9
    s.file_path = str(folder / 'session.ptx')
    info = bytearray.fromhex('80bb00000118e07484000000000003000000000000002a9bef420fef858000')
    info[6:9] = (60000).to_bytes(3, 'little')
    header = bytearray(151)
    struct.pack_into('<I', header, 100, 2000)
    header[135:151] = uuid.UUID(int=11).bytes
    tail = bytearray(58)
    tail[18:50] = bytes.fromhex('060a2b340101010501010f10130000009bef420fef8580006e09cb5acce13e00')
    media = block(9, 0x1003, [bytearray(struct.pack('<I', 1)), block(2, 0x1001, [info]),
        block(2, 0x1033, []), block(35, 0x2106, [header, block(1, 0x4301, [bytes(32)]), tail]), bytes(4)])
    names = (struct.pack('<I', 4) + b'\x01' + struct.pack('<II', 3, 11) + b'Audio Files' + bytes(4)
             + b'\x02' + bytes(4) + struct.pack('<I', 7) + b'old.wavEVAW'
             + b'\x00\xff\xff\xff\xff' + struct.pack('<I', 3) + b'VOL' + bytes.fromhex('46221895')
             + b'\x01' + struct.pack('<II', 2, 4) + b'ROOT' + bytes(4))
    s.root_items.append(block(1, 0x1004, [bytearray(struct.pack('<I', 1)),
        block(1, 0x103a, [bytearray(names)]), media, block(1, 0x103a, [bytes(4)])]))
    return s


class ImportAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / 'Audio Files').mkdir()
        self.wav = self.folder / 'Audio Files/new_é.wav'
        self.wav.write_bytes(wave_bytes(self.wav.stem))
        self.s = session(self.folder)

    def invoke(self, **kwargs):
        options = dict(track_name='TRACK', audio_path=self.wav, start_samples=200000,
                       prototype_clip_name='OLD')
        options.update(kwargs)
        return self.s.import_audio(**options)

    def unchanged(self, callback, exception=(ValueError, TypeError, OverflowError, NotImplementedError)):
        before = snapshot(self.s)
        objects = [self.s._root_blocks(x)[0].items for x in (0x262a, 0x1054, 0x1004)]
        wav = self.wav.read_bytes()
        with self.assertRaises(exception):
            callback()
        self.assertEqual(snapshot(self.s), before)
        for content, items in zip((0x262a, 0x1054, 0x1004), objects):
            self.assertIs(self.s._root_blocks(content)[0].items, items)
        self.assertEqual(self.wav.read_bytes(), wav)

    def test_append_unicode_name_placement_preserves_old_objects_wav_and_metadata(self):
        old = self.s._root_blocks(0x262a)[0].items[1]
        old_event = self.s._validated_main_playlists()[0][2][0]
        before_wave = self.wav.read_bytes()
        self.assertEqual(self.invoke(clip_name='Prise_été_LONG'), 1)
        clips = self.s.get_timeline_clips()
        self.assertEqual((clips[1]['start_samples'], clips[1]['end_samples'], clips[1]['src_offset_samples']),
                         (200000, 200100, 0))
        self.assertEqual(clips[1]['physical_filename'], self.wav.name)
        self.assertEqual(clips[1]['clip_name'], 'Prise_été_LONG')
        self.assertIs(self.s._root_blocks(0x262a)[0].items[1], old)
        self.assertIs(self.s._validated_main_playlists()[0][2][0], old_event)
        self.assertEqual(self.wav.read_bytes(), before_wave)
        self.assertEqual(self.s._removed_offsets, [])

    def test_successive_imports_indices_and_new_parent_as_explicit_prototype(self):
        self.invoke()
        next_wav = self.wav.parent / 'next.wav'
        next_wav.write_bytes(wave_bytes('next', token=0x71))
        self.assertEqual(self.invoke(audio_path=next_wav, prototype_clip_name='new_é', start_samples=300000), 2)
        d = self.s._root_blocks(0x262a)[0].items[-1]
        (_, _, identity), (_, _, link) = self.s._validated_2629_fixed_records(d)
        self.assertEqual(struct.unpack_from('<I', identity)[0], 2)
        self.assertEqual(struct.unpack_from('<I', link, 96)[0], 2)
        self.assertEqual(self.s._validated_physical_audio_catalog()[3], ['old.wav', 'new_é.wav', 'next.wav'])

    def test_arguments_names_and_sample_types_rejected_without_changes(self):
        for options in (dict(track_name=''), dict(clip_name='\x00'), dict(clip_name='\ud800'),
                        dict(prototype_clip_name=3), dict(start_samples=True), dict(start_samples=-1),
                        dict(start_samples=1 << 64), dict(audio_path=b'bytes'), dict(clip_name=3)):
            with self.subTest(options=options):
                self.unchanged(lambda: self.invoke(**options))

    def test_wrong_profile_rate_endian_and_track_rejected_without_changes(self):
        self.unchanged(lambda: self.invoke(track_name='MISSING'))
        for key, value in (('sample_rate', 44100), ('frame_rate_enum', 2), ('is_bigendian', True)):
            old = getattr(self.s, key)
            setattr(self.s, key, value)
            self.unchanged(self.invoke)
            setattr(self.s, key, old)

    def test_external_audio_path_no_implicit_copy_and_missing_file(self):
        outside = self.folder / self.wav.name
        shutil.copy2(self.wav, outside)
        self.unchanged(lambda: self.invoke(audio_path=outside))
        self.unchanged(lambda: self.invoke(audio_path=self.wav.parent / 'absent.wav'), FileNotFoundError)
        self.assertTrue(outside.exists())

    def test_duplicate_clip_filename_and_identity_rejected(self):
        self.unchanged(lambda: self.invoke(clip_name='old'))
        self.unchanged(lambda: self.invoke(prototype_clip_name='MISSING'))
        self.invoke()
        self.unchanged(self.invoke)
        duplicate = self.wav.parent / 'copy.wav'
        duplicate.write_bytes(wave_bytes('copy'))
        self.unchanged(lambda: self.invoke(audio_path=duplicate))

    def test_riff_size_format_duplicate_and_sample_alignment_refused(self):
        original = self.wav.read_bytes()
        variants = [b'RIFF' + struct.pack('<I', 1) + original[8:], b'NOPE' + original[4:]]
        wrong_fmt = bytearray(original)
        struct.pack_into('<H', wrong_fmt, 20, 3)
        variants.append(wrong_fmt)
        duplicate = original + b'fmt ' + struct.pack('<I', 40) + original[20:60]
        variants.append(duplicate[:4] + struct.pack('<I', len(duplicate) - 8) + duplicate[8:])
        broken_data = bytearray(original[:-2])
        struct.pack_into('<I', broken_data, 4, len(broken_data) - 8)
        variants.append(broken_data)
        for data in variants:
            self.wav.write_bytes(data)
            self.unchanged(self.invoke)
        self.wav.write_bytes(original)

    def test_bwf_regn_umid_and_stem_correspondence_required(self):
        original = self.wav.read_bytes()
        for needle, relative in ((b'bext', 8 + 338), (b'bext', 8 + 348), (b'regn', 8 + 28), (b'regn', 8 + 11),
                                 (b'umid', 8 + 7), (b'regn', 8 + 61)):
            data = bytearray(original)
            data[data.index(needle) + relative] ^= 1
            self.wav.write_bytes(data)
            self.unchanged(self.invoke)
        self.wav.write_bytes(original)

    def test_late_uuid_failure_is_atomic(self):
        with mock.patch('uuid.uuid4', side_effect=[uuid.UUID(int=40), RuntimeError('late UUID')]):
            self.unchanged(self.invoke, RuntimeError)

    def test_duplicate_or_existing_uuid_refused_without_retry_or_mutation(self):
        for sequence in ([uuid.UUID(int=40)] * 2, [uuid.UUID(int=10), uuid.UUID(int=40)]):
            with mock.patch('uuid.uuid4', side_effect=sequence):
                self.unchanged(self.invoke)

    def test_late_playlist_preparation_failure_is_atomic(self):
        with mock.patch.object(self.s, '_append_imported_audio_event', side_effect=RuntimeError('late event')):
            self.unchanged(self.invoke, RuntimeError)

    def test_duration_and_bwf_reference_overflow_no_truncation(self):
        metadata = _inspect_pcm24_import_wave(self.wav)
        with mock.patch('pt_api._inspect_pcm24_import_wave', return_value=dict(metadata, length_samples=0x1000000)):
            self.unchanged(self.invoke)
        data = bytearray(self.wav.read_bytes())
        struct.pack_into('<Q', data, data.index(b'bext') + 8 + 338, 0x100000000)
        self.wav.write_bytes(data)
        self.unchanged(self.invoke)

    def test_start_end_uint64_and_independent_definition_timestamp(self):
        self.unchanged(lambda: self.invoke(start_samples=0xFFFFFFFFFFFFFFFF - 50))
        self.assertEqual(self.invoke(start_samples=0x100000000), 1)
        clip = self.s.get_timeline_clips()[1]
        self.assertEqual(clip['start_samples'], 0x100000000)
        payload = one(self.s._root_blocks(0x262a)[0].items[-1], 0x2628).items[0]
        a = 4 + struct.unpack_from('<I', payload)[0]
        self.assertEqual(struct.unpack_from('<II', payload, a + 8), (3000, 3000))

    def test_malformed_parent_and_media_geometry_refused(self):
        for kind in ('selector', 'parent-length', 'media-index', 'media-size', 'detail-size'):
            self.s = session(self.folder)
            d = self.s._root_blocks(0x262a)[0].items[1]
            media = self.s._validated_physical_audio_catalog()[1][0]
            if kind == 'selector':
                d.items[0].items[0][9] = 0x20
            elif kind == 'parent-length':
                d.items[0].items[0].append(0)
            elif kind == 'media-index':
                struct.pack_into('<I', d.items[-1], 96, 9)
            elif kind == 'media-size':
                media.items[1].items[0].append(0)
            else:
                media.items[3].items[0].append(0)
            self.unchanged(self.invoke)

    def test_fade_and_group_event_variants_explicitly_refused(self):
        e = self.s._validated_main_playlists()[0][2][0]
        for tail in (b'\x00\x00\x01', b'\x01\x01\x01'):
            e.items[1] = tail
            self.unchanged(self.invoke, NotImplementedError)

    def test_catalog_counter_failure_and_no_bin_count_truncation(self):
        catalog = self.s._validated_physical_audio_catalog()[0]
        struct.pack_into('<I', catalog.items[1].items[0], 0, 0xFFFFFFFF)
        self.unchanged(self.invoke)
        self.s = session(self.folder)
        bin_root = self.s._root_blocks(0x262a)[0]
        struct.pack_into('<I', bin_root.items[0], 0, 9)
        self.unchanged(self.invoke)


@unittest.skipUnless(all(p.exists() for p in FILES), 'Local native PCM24 import fixtures required')
class NativeImportAudioTests(unittest.TestCase):
    def test_public_writer_reproduces_validated_candidate_byte_identically(self):
        candidate = ProToolsSession(str(FILES[2]))
        media = candidate._validated_physical_audio_catalog()[1][1]
        detail = one(media, 0x2106)
        d = candidate._root_blocks(0x262a)[0].items[-1]
        (_, _, identity), _ = candidate._validated_2629_fixed_records(d)
        uuids = [uuid.UUID(bytes=bytes(detail.items[0][135:151])), uuid.UUID(bytes=bytes(identity[23:39]))]
        filetime = struct.unpack_from('<Q', detail.items[0], 29)[0]
        with tempfile.TemporaryDirectory() as folder:
            s = ProToolsSession(str(FILES[0]))
            # Filesystem policy is tested independently; metadata is taken
            # from the immutable validated candidate, not the native after.
            s.file_path = str(Path(folder) / 'base.ptx')
            metadata = dict(length_samples=8680672, bwf_time_reference=1729728000,
                umid=bytes(detail.items[2][18:50]), file_identifier=bytes(one(media, 0x1001).items[0][22:31]))
            with mock.patch('pt_api._inspect_pcm24_import_wave', return_value=metadata), \
                 mock.patch('time.time', return_value=(filetime - 116444736000000000) / 10000000), \
                 mock.patch('uuid.uuid4', side_effect=uuids):
                self.assertEqual(s.import_audio('Audio 1', Path(folder) / 'Audio Files/Audio 2_01.wav',
                    1738456672, 'Audio 1_01'), 2)
            output = Path(folder) / 'out.ptx'
            s.save(str(output))
            self.assertEqual(output.read_bytes(), FILES[2].read_bytes())

    @unittest.skipUnless(RESAVED.exists(), 'Native resaved import required')
    def test_pro_tools_resaved_placements_and_two_exact_cycles(self):
        s = ProToolsSession(str(RESAVED))
        self.assertEqual(s.get_timeline_clips(), ProToolsSession(str(FILES[2])).get_timeline_clips())
        self.assertEqual(len(pointer_rows(s)), 167)
        with tempfile.TemporaryDirectory() as folder:
            for i in range(2):
                path = Path(folder) / ('cycle%d.ptx' % i)
                s.save(str(path))
                self.assertEqual(path.read_bytes(), RESAVED.read_bytes())
                s = ProToolsSession(str(path))


if __name__ == '__main__':
    unittest.main()
