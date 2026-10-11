"""Native ruler population flag, without adopting a modern marker layout."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession, PTBlock
from tests.test_markers import make_session, marker_blocks
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_marker_delete import BEFORE, ONE, ALL

IMPORTED = Path(__file__).parent / 'fixtures' / 'native_marker_direct_import.ptx'


def catalog(session, present=0):
    entries = []
    for index in range(5):
        entry = PTBlock(2, 0x251c)
        entry.items = [struct.pack('<HI', index, 0)
                      + bytes((present if index == 0 else 0,))
                      + struct.pack('<I', index)]
        entries.append(entry)
    container = PTBlock(1, 0x251b)
    container.items = [struct.pack('<I', len(entries)), *entries]
    root = PTBlock(8, 0x2519)
    root.items = [container]
    session.root_items.append(root)
    return root, container, entries


def population_entry(session):
    return session._root_blocks(0x2519)[0].get_all_blocks(0x251c)[0]


class MarkerPopulationTests(unittest.TestCase):
    def test_add_changes_only_population_byte_in_catalogue(self):
        s, _ = make_session()
        root, _, entries = catalog(s)
        expected = copy.deepcopy(root)
        raw = bytearray(expected.items[0].items[1].items[0])
        raw[6] = 1
        expected.items[0].items[1].items[0] = bytes(raw)
        untouched = [e.items[0] for e in entries[1:]]
        s.add_marker('Nom libre é', 12345)
        self.assertEqual(root.to_bytes()[0], expected.to_bytes()[0])
        for entry, original in zip(entries[1:], untouched):
            self.assertIs(entry.items[0], original)

    def test_partial_and_last_deletion_then_add_again(self):
        s, _ = make_session()
        _, _, entries = catalog(s)
        s.add_marker('A', 0)
        s.add_marker('B', 48048)
        s.delete_marker(index=1)
        self.assertEqual(entries[0].items[0][6], 1)
        s.delete_marker(index=2)
        self.assertEqual(entries[0].items[0][6], 0)
        s.add_marker('C', 96096)
        self.assertEqual(entries[0].items[0][6], 1)

    def test_no_match_never_repairs_or_replaces_catalogue(self):
        s, _ = make_session()
        _, _, entries = catalog(s, present=1)
        original = entries[0].items[0]
        before = snapshot(s)
        self.assertEqual(s.delete_marker(index=99), 0)
        self.assertEqual(snapshot(s), before)
        self.assertIs(entries[0].items[0], original)

    def test_unknown_or_ambiguous_catalogue_rejects_add_and_delete_atomically(self):
        for variant in ('root', 'catalogue', 'counter', 'entry_type', 'entry_shape',
                        'target_length', 'target_name', 'target_flag', 'target_id',
                        'duplicate_zero', 'missing_zero'):
            for operation in ('add', 'delete'):
                s, _ = make_session()
                s.add_marker('Existing', 0)
                root, container, entries = catalog(s, present=1)
                if variant == 'root': s.root_items.append(copy.deepcopy(root))
                elif variant == 'catalogue': root.items.append(copy.deepcopy(container))
                elif variant == 'counter': container.items[0] = struct.pack('<I', 999)
                elif variant == 'entry_type': entries[-1].block_type = 1
                elif variant == 'entry_shape': entries[-1].items = [b'']
                elif variant == 'target_length': entries[0].items[0] += b'opaque'
                elif variant == 'target_name': entries[0].items[0] = b'\x00\x00\x01' + bytes(8)
                elif variant == 'target_flag': entries[0].items[0] = bytes(6) + b'\x02' + bytes(4)
                elif variant == 'target_id': entries[0].items[0] = bytes(6) + b'\x01\x01' + bytes(3)
                elif variant == 'duplicate_zero': entries[1].items[0] = entries[0].items[0]
                else: entries[0].items[0] = struct.pack('<HI', 5, 0) + b'\x01' + struct.pack('<I', 5)
                before = snapshot(s)
                offsets, types = s._removed_offsets, getattr(s, '_removed_block_types', None)
                with self.subTest(variant=variant, operation=operation):
                    with self.assertRaises(ValueError):
                        if operation == 'add': s.add_marker('New', 48048)
                        else: s.delete_marker(index=1)
                    self.assertEqual(snapshot(s), before)
                    self.assertIs(s._removed_offsets, offsets)
                    self.assertIs(getattr(s, '_removed_block_types', None), types)

    def test_catalogue_absent_retains_historical_contract(self):
        s, _ = make_session()
        s.add_marker('A', 0)
        self.assertEqual(s.delete_marker(index=1), 1)
        root = PTBlock(8, 0x2519)
        root.items = [b'opaque unrelated catalogue without ruler entries']
        s.root_items.append(root)
        before = root.to_bytes()[0]
        s.add_marker('B', 1)
        self.assertEqual(root.to_bytes()[0], before)


@unittest.skipUnless(all(p.exists() for p in (BEFORE, ONE, ALL)),
                     'Local native marker population oracles absent')
class NativeMarkerPopulationTests(unittest.TestCase):
    def test_population_transition_matches_native_deletion_oracles(self):
        s = ProToolsSession(str(BEFORE))
        s.delete_marker(index=2)
        self.assertEqual(population_entry(s).items[0], population_entry(ProToolsSession(str(ONE))).items[0])
        for row in s.get_markers():
            s.delete_marker(index=row['index'])
        self.assertEqual(population_entry(s).items[0], population_entry(ProToolsSession(str(ALL))).items[0])

    def test_add_to_native_empty_catalogue_and_reversible_api_cycles(self):
        s = ProToolsSession(str(ALL))
        expected = bytearray(population_entry(s).items[0])
        expected[6] = 1
        # This deletion oracle is 25 fps; the population invariant does not
        # depend on frame rate, and samples must not be interpreted as 23.976.
        s.add_marker('API_IMPORT_TEST', 1728480000)
        self.assertEqual(population_entry(s).items[0], bytes(expected))
        ruler = s._root_blocks(0x2030)[0]
        self.assertEqual(len(marker_blocks(ruler)[0].get_all_blocks(0x2506)), 1)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            s.save(str(out))
            candidate = out.read_bytes()
            for _ in range(2):
                s = ProToolsSession(str(out))
                self.assertEqual(s.get_markers()[0]['name'], 'API_IMPORT_TEST')
                self.assertEqual(population_entry(s).items[0], bytes(expected))
                s.save(str(out))
                self.assertEqual(out.read_bytes(), candidate)
            s.delete_marker(index=1)
            self.assertEqual(population_entry(s).items[0], population_entry(ProToolsSession(str(ALL))).items[0])
            s.save(str(out))
            self.assertEqual(out.read_bytes(), ALL.read_bytes())


@unittest.skipUnless(IMPORTED.exists(), 'Local native direct marker import absent')
class NativeDirectMarkerImportTests(unittest.TestCase):
    def test_imported_name_time_population_and_exact_cycles(self):
        s = ProToolsSession(str(IMPORTED))
        original = IMPORTED.read_bytes()
        self.assertEqual((s.sample_rate, s.frame_rate_enum), (48000, 9))
        self.assertEqual(s.get_markers(), [
            {'index': 1, 'name': 'API_IMPORT_TEST', 'timecode': '10:00:10:00'}])
        self.assertEqual(population_entry(s).items[0], bytes(6) + b'\x01' + bytes(4))
        # Native import normalized away the source's single property block;
        # neither property presence nor absence is a universal marker profile.
        marker = marker_blocks(s._root_blocks(0x2030)[0])[0]
        self.assertEqual(len(marker.get_all_blocks(0x2506)), 0)
        name_length = struct.unpack_from('<I', marker.items[0], 6)[0]
        self.assertEqual(struct.unpack_from('<qq', marker.items[0], 10 + name_length),
                         (1730208480, 1730208480))
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for _ in range(2):
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
                self.assertEqual(s.get_markers()[0]['timecode'], '10:00:10:00')
        self.assertEqual(IMPORTED.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
