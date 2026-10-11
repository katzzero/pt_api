"""Native-matched region retention, public contract and atomic refusals."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import uuid

from pt_api import ProToolsSession
from tests.test_timeline_read import block, playlist, event, make_session
from tests.test_trim_inplace import definition, TAIL
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_trim_inplace_before.ptx'
CANDIDATE = FIXTURES / 'native_keep_clip_regions_candidate.ptx'
RESAVED = FIXTURES / 'native_keep_clip_regions_pt_resaved.ptx'
REGIONS = [(1729728100, 1729776100), (1729872100, 1729920100)]
LINK_PREFIX = bytes.fromhex('000001007f4d644368756e01004f0000004d644e4c4203001600000000000000000000000b00000000000000010001000000000000004d64455301000f0000004d6454454c01000400000000000000000000002a000000')


def session():
    d = definition(start=1000000, offset=100, length=8000000)
    identity = bytes.fromhex('0000000000000000803f0000803f0000000000000000ff') + bytes(range(16)) + bytes(9)
    opaque = block(1, 0x4403, [block(0, 0x1000, [bytes(18)]), b'\x00'])
    d.items.extend([identity, opaque, LINK_PREFIX + bytes(8) + b'\x01' + bytes(8)])
    e = event(0, 1000000)
    e.items[0].block_type = 10
    s = make_session([d], [playlist('TRACK', [e])])
    return s


class KeepClipRegionsTests(unittest.TestCase):
    def test_unsorted_regions_use_original_source_origin_and_preserve_first_id(self):
        s = session()
        original = s._root_blocks(0x262a)[0].items[1]
        identity, link, opaque = original.items[1:]
        e = s._validated_main_timeline_events()[0][1]
        self.assertEqual(s.keep_clip_regions('TAKE', [(1144000, 1192000), (1000000, 1048000)]), 2)
        self.assertIs(s._root_blocks(0x262a)[0].items[1], original)
        self.assertIs(s._validated_main_timeline_events()[0][1], e)
        clips = s.get_timeline_clips()
        self.assertEqual([(x['start_samples'], x['end_samples'], x['src_offset_samples']) for x in clips],
                         [(1000000, 1048000, 100), (1144000, 1192000, 144100)])
        self.assertEqual([s._audio_clip_info_by_id(i)['name'] for i in range(2)], ['TAKE', 'TAKE#1144000'])
        self.assertEqual(original.items[1:], [identity, link, opaque])
        self.assertEqual(struct.unpack_from('<I', s._root_blocks(0x262a)[0].items[0])[0], 2)

    def test_three_fragments_widths_long_duration_unicode_and_exact_timestamps(self):
        s = session()
        p = s._root_blocks(0x262a)[0].items[1].items[0].items[0]
        n = 'Prise_é'.encode('utf-8')
        s._root_blocks(0x262a)[0].items[1].items[0].items[0] = struct.pack('<I', len(n)) + n + bytes(p[8:])
        self.assertEqual(s.keep_clip_regions('Prise_é', [(1000100, 1000200), (1100000, 1200000), (1300000, 1300010)]), 3)
        for i, (start, end, source, sw, lw) in enumerate(((1000100,1000200,200,1,2),
                    (1100000,1200000,100100,3,3), (1300000,1300010,300100,3,2))):
            d = s._root_blocks(0x262a)[0].items[i+1]
            pl = bytes(d.items[0].items[0]); a = 4 + struct.unpack_from('<I', pl)[0]
            self.assertEqual(pl[a:a+5], bytes((1, sw<<4, lw<<4, 0x44, 8)))
            self.assertEqual(pl[a+5:a+5+sw], source.to_bytes(sw,'little'))
            self.assertEqual(pl[a+5+sw:a+5+sw+lw], (end-start).to_bytes(lw,'little'))
            self.assertEqual(pl[a+5+sw+lw:], struct.pack('<II', start,start)+TAIL)

    def test_whole_clip_is_noop_and_preserves_all_container_objects(self):
        s = session(); before = snapshot(s)
        roots, items = s.root_items, s._root_blocks(0x262a)[0].items
        self.assertEqual(s.keep_clip_regions('TAKE', [(1000000,9000000)]), 1)
        self.assertEqual(snapshot(s), before)
        self.assertIs(s.root_items, roots); self.assertIs(s._root_blocks(0x262a)[0].items, items)

    def test_bad_inputs_and_bounds_never_mutate(self):
        cases = [(None,[(1000000,1048000)]),('',[(1000000,1048000)]),('T\x00',[]),('\ud800',[]),
                 ('TAKE',None),('TAKE','123'),('TAKE',{}),('TAKE',[]),('TAKE',[1]),('TAKE',['12']),
                 ('TAKE',[(1,2,3)]),('TAKE',[(True,2)]),('TAKE',[(1.5,2)]),('TAKE',[(1,1)]),
                 ('TAKE',[(-1,1)]),('TAKE',[(0,0x100000000)]),('TAKE',[(999999,1000001)]),
                 ('TAKE',[(1000000,9000001)]),('TAKE',[(1000000,1048000),(1000100,1049000)]),
                 ('missing',[(1000000,1048000)])]
        for name, regions in cases:
            s=session(); before=snapshot(s)
            with self.assertRaises((TypeError,ValueError)): s.keep_clip_regions(name,regions)
            self.assertEqual(snapshot(s),before)

    def test_profile_refusals_are_atomic(self):
        variants = ('endian','definition','identity','opaque','link','header','tail','gain','timestamp',
                    'zero','event','event_type','event_tuple','clip_tuple','playlist_tuple')
        for variant in variants:
            s=session(); d=s._root_blocks(0x262a)[0].items[1]; p=d.items[0].items[0]
            if variant=='endian': s.is_bigendian=True
            elif variant=='definition': d.block_type=1
            elif variant=='identity': d.items[1]=bytes(48)
            elif variant=='opaque': d.items[2].items[-1]=b'\x01'
            elif variant=='link': d.items[3]=bytes(104)
            elif variant=='header': p[11]=0x14
            elif variant=='tail': p[-1]=1
            elif variant=='gain': p[-6:-2]=bytes(4)
            elif variant=='timestamp': p[17:21]=bytes(4)
            elif variant=='zero': p[13]=0
            elif variant=='event': s._validated_main_timeline_events()[0][3][33]=1
            elif variant=='event_type': s._validated_main_timeline_events()[0][2].block_type=3
            elif variant=='event_tuple':
                e=s._validated_main_timeline_events()[0][2]; e.items=tuple(e.items)
            elif variant=='clip_tuple': s._root_blocks(0x262a)[0].items=tuple(s._root_blocks(0x262a)[0].items)
            elif variant=='playlist_tuple':
                pl=s._validated_main_timeline_events()[0][0]; pl.items=tuple(pl.items)
            before=snapshot(s)
            with self.assertRaises((ValueError,NotImplementedError)): s.keep_clip_regions('TAKE',[(1000000,1048000)])
            self.assertEqual(snapshot(s),before)

    def test_hidden_repeated_and_overlapping_placements_are_refused(self):
        for variant in ('hidden','repeated','overlap'):
            s=session(); e=s._validated_main_timeline_events()[0][1]; pl=s._validated_main_timeline_events()[0][0]
            if variant=='hidden': s.root_items.append(block(1,0x8888,[copy.deepcopy(e)]))
            elif variant=='repeated':
                pl.items.insert(2,copy.deepcopy(e)); struct.pack_into('<I',pl.items[0],9,2)
            else:
                other=copy.deepcopy(s._root_blocks(0x262a)[0].items[1])
                other.items[0].items[0][4:8]=b'ELSE'
                s._root_blocks(0x262a)[0].items.append(other)
                struct.pack_into('<I',s._root_blocks(0x262a)[0].items[0],0,2)
                other_event=copy.deepcopy(e); struct.pack_into('<I',other_event.items[0].items[0],2,1)
                pl.items.insert(2,other_event); struct.pack_into('<I',pl.items[0],9,2)
            before=snapshot(s)
            with self.assertRaises(ValueError): s.keep_clip_regions('TAKE',[(1000000,1048000)])
            self.assertEqual(snapshot(s),before)

    def test_late_uuid_error_does_not_replace_objects_or_bookkeeping(self):
        s=session(); before=snapshot(s); roots=s.root_items; target=s._root_blocks(0x262a)[0].items[1]
        payload=target.items[0].items[0]; removed=s._removed_offsets
        with mock.patch('uuid.uuid4',side_effect=RuntimeError('uuid failure')):
            with self.assertRaises(RuntimeError): s.keep_clip_regions('TAKE',[(1000000,1048000),(1144000,1192000)])
        self.assertEqual(snapshot(s),before); self.assertIs(s.root_items,roots)
        self.assertIs(target.items[0].items[0],payload); self.assertIs(s._removed_offsets,removed)

    def test_uuid_collision_is_refused_without_mutation(self):
        s=session(); before=snapshot(s)
        with mock.patch('uuid.uuid4',return_value=uuid.UUID(bytes=bytes(range(16)))):
            with self.assertRaises(ValueError): s.keep_clip_regions('TAKE',[(1000000,1048000),(1144000,1192000)])
        self.assertEqual(snapshot(s),before)

    def test_generated_name_collision_is_refused(self):
        s=session(); root=s._root_blocks(0x262a)[0]
        other=copy.deepcopy(root.items[1]); name=b'TAKE#1144000'
        other.items[0].items[0]=struct.pack('<I',len(name))+name+bytes(other.items[0].items[0][8:])
        root.items.append(other); struct.pack_into('<I',root.items[0],0,2)
        before=snapshot(s)
        with self.assertRaises(ValueError): s.keep_clip_regions('TAKE',[(1000000,1048000),(1144000,1192000)])
        self.assertEqual(snapshot(s),before)

    def test_late_uint24_source_overflow_is_atomic(self):
        s=session(); d=s._root_blocks(0x262a)[0].items[1]; p=bytes(d.items[0].items[0])
        d.items[0].items[0]=p[:8]+bytes.fromhex('0130304408')+(16000000).to_bytes(3,'little')+p[14:]
        before=snapshot(s)
        with self.assertRaises(ValueError): s.keep_clip_regions('TAKE',[(1000000,1048000),(2000000,2048000)])
        self.assertEqual(snapshot(s),before)

    def test_region_iterator_failure_is_atomic(self):
        s=session(); before=snapshot(s)
        def broken():
            yield (1000000,1048000)
            raise RuntimeError('iterator error')
        with self.assertRaises(RuntimeError): s.keep_clip_regions('TAKE',broken())
        self.assertEqual(snapshot(s),before)

    def test_orphan_fade_and_valid_attached_fade_are_refused(self):
        from tests.test_timeline_read import geometry
        for orphan in (True,False):
            s=session(); geo=geometry(bytes.fromhex('0000000000330000c07701c07701020200000000000000000000000000'))
            g=s._root_blocks(0x2630)[0]; g.items=[struct.pack('<I',1),geo]
            if not orphan:
                pl=s._validated_main_timeline_events()[0][0]
                pl.items.insert(2,event(0,9000000,event_type=1)); struct.pack_into('<I',pl.items[0],9,2)
            before=snapshot(s)
            with self.assertRaises((ValueError,NotImplementedError)):
                s.keep_clip_regions('TAKE',[(1000000,1048000),(1144000,1192000)])
            self.assertEqual(snapshot(s),before)

    def test_group_macro_id_collision_and_removal_metadata_are_preserved(self):
        s=session(); pl=s._validated_main_timeline_events()[0][0]
        macro=event(0,500000,event_type=3); macro.items[-1]=b'\x00\x00\x01'
        pl.items.insert(2,macro); struct.pack_into('<I',pl.items[0],9,2)
        opaque=s._root_blocks(0x2630)[0]; raw=opaque.to_bytes()[0]
        s._removed_offsets=[99]; s._removed_block_types={99:0x7777}
        removed=s._removed_offsets; types=s._removed_block_types
        self.assertEqual(s.keep_clip_regions('TAKE',[(1000000,1048000),(1144000,1192000)]),2)
        self.assertIs(pl.items[3],macro)
        self.assertEqual(opaque.to_bytes()[0],raw)
        self.assertIs(s._removed_offsets,removed); self.assertIs(s._removed_block_types,types)
        self.assertEqual(removed,[99]); self.assertEqual(types,{99:0x7777})

    def test_further_retention_uses_new_source_without_changing_other_fragment(self):
        s=session(); s.keep_clip_regions('TAKE',[(1000000,1048000),(1144000,1244000)])
        first=s._root_blocks(0x262a)[0].items[1].to_bytes()[0]
        self.assertEqual(s.keep_clip_regions('TAKE#1144000',[(1144100,1243900)]),1)
        self.assertEqual(s._audio_clip_info_by_id(1)['src_offset'],144200)
        self.assertEqual(s._audio_clip_info_by_id(1)['length'],99800)
        self.assertEqual(s._root_blocks(0x262a)[0].items[1].to_bytes()[0],first)

    def test_rates_do_not_round_sample_regions(self):
        for rate,enum in ((48000,9),(48000,2),(44100,9),(96000,1)):
            s=session(); s.sample_rate=rate; s.frame_rate_enum=enum
            self.assertEqual(s.keep_clip_regions('TAKE',[(1000100,1000200),(1144000,1192000)]),2)
            self.assertEqual(s.get_timeline_clips()[0]['start_samples'],1000100)


@unittest.skipUnless(all(p.exists() for p in (BEFORE,CANDIDATE,RESAVED)), 'Local native retention fixtures absent')
class NativeKeepClipRegionsWriterTests(unittest.TestCase):
    def test_public_writer_reproduces_validated_candidate_byte_identically(self):
        s=ProToolsSession(str(BEFORE)); candidate=ProToolsSession(str(CANDIDATE))
        ident=candidate._root_blocks(0x262a)[0].get_all_blocks(0x2629)[2].items[1]
        with mock.patch('uuid.uuid4',return_value=uuid.UUID(bytes=bytes(ident[23:39]))):
            self.assertEqual(s.keep_clip_regions('Audio 1_01-01',REGIONS),2)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'candidate.ptx'; s.save(str(out))
            self.assertEqual(out.read_bytes(),CANDIDATE.read_bytes())

    def test_resaved_geometry_cycle_and_further_retention(self):
        s=ProToolsSession(str(RESAVED)); base=ProToolsSession(str(BEFORE))
        self.assertEqual(s.get_timeline_clips()[0]['src_offset_samples'],100)
        self.assertEqual(s.get_timeline_clips()[1]['src_offset_samples'],144100)
        self.assertEqual(pointer_rows(s),pointer_rows(base))
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'cycle.ptx'
            for _ in range(2):
                s.save(str(out)); self.assertEqual(out.read_bytes(),RESAVED.read_bytes())
                s=ProToolsSession(str(out))
        self.assertEqual(s.keep_clip_regions('Audio 1_01-01',[(1729728100,1729776100)]),1)
        self.assertEqual(s.keep_clip_regions('Audio 1_01-01#1729872100',[(1729872125,1729920075)]),1)
        self.assertEqual(len(s.get_clips()),3)
        self.assertEqual(s.get_timeline_clips()[1]['src_offset_samples'],144125)
