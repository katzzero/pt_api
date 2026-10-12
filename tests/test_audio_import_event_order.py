"""Physical playlist order is distinct from caller/catalog order."""
import itertools
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import (
    PTBlock, ProToolsSession, _chronological_audio_import_placements,
    _prepare_template_audio_clips, _validate_generated_audio_session,
)
from tests.test_audio_session_builder import make_audio_import_template_session, write_float_bwf_wave


def event_records(session):
    return {name: [(struct.unpack_from("<I", event.items[0].items[0], 2)[0],
                    struct.unpack_from("<Q", event.items[0].items[0], 7)[0])
                   for event in events]
            for _, name, events in session._validated_main_playlists()}


def prepare(root, placements):
    descriptors = []
    for i, (start, length, track) in enumerate(placements):
        path = root / ("source_%d.wav" % i)
        write_float_bwf_wave(path, length, 1000 + i)
        descriptors.append({"audio_path": path, "track_name": track,
                            "placement_start_samples": start})
    return _prepare_template_audio_clips(descriptors)[0]


class AudioImportEventOrderTests(unittest.TestCase):
    def test_131_lexicographic_names_write_chronological_events_not_catalogs(self):
        numbers = sorted(range(1, 132), key=lambda n: "media_%02d_suffix.wav" % n)
        self.assertEqual(sum(a > b for a, b in zip(numbers, numbers[1:])), 4)
        self.assertEqual(numbers[-1], 99)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare(root, [(n * 100, 100, "Production 1") for n in numbers])
            for item, n in zip(prepared, numbers):
                item["physical_filename"] = "media_%02d_suffix.wav" % n
                item["clip_name"] = "media_%02d_suffix.A1" % n
            session = make_audio_import_template_session()
            manifest = session._populate_audio_import_template(prepared)
            self.assertEqual([m["start_samples"] for m in manifest], [n * 100 for n in numbers])
            self.assertEqual([c["name"] for c in session.get_clips()], [m["clip_name"] for m in prepared])
            self.assertEqual(session._validated_physical_audio_catalog()[3],
                             [m["physical_filename"] for m in prepared])
            self.assertEqual(event_records(session)["Production 1"],
                             [(numbers.index(n), n * 100) for n in range(1, 132)])
            self.assertEqual(event_records(session)["Production 2"], [])
            self.assertEqual(bytes(session._validated_main_playlists()[0][0].items[-1]), b"\x01\x00")
            _validate_generated_audio_session(session, prepared, ["Production 1", "Production 2"])

    def test_validator_detects_raw_disorder_hidden_by_sorted_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare(Path(directory), [(200, 100, "Production 1"), (100, 100, "Production 1")])
            session = make_audio_import_template_session()
            session._populate_audio_import_template(prepared)
            before = session.get_timeline_clips()
            playlist = session._validated_main_playlists()[0][0]
            slots = [i for i, item in enumerate(playlist.items) if isinstance(item, PTBlock)]
            playlist.items[slots[0]], playlist.items[slots[1]] = playlist.items[slots[1]], playlist.items[slots[0]]
            self.assertEqual(session.get_timeline_clips(), before)
            with self.assertRaisesRegex(ValueError, "playlist event order"):
                _validate_generated_audio_session(session, prepared, ["Production 1", "Production 2"])

    def test_stable_equal_starts_and_overlap_priority_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare(Path(directory), [(100, 101, "Production 1"),
                                                 (100, 100, "Production 1"),
                                                 (200, 100, "Production 1")])
            session = make_audio_import_template_session()
            session._populate_audio_import_template(prepared)
            self.assertEqual(event_records(session)["Production 1"], [(0, 100), (1, 100), (2, 200)])

    def test_disjoint_events_can_move_around_preserved_overlapping_pair(self):
        prepared = [{"track": "T", "start_samples": start, "end_samples": end}
                    for start, end in ((0, 10), (30, 40), (5, 15))]
        self.assertEqual([i for i, _ in _chronological_audio_import_placements(prepared)], [0, 2, 1])

    def test_reversed_overlap_refused_before_any_template_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare(Path(directory), [(200, 100, "Production 1"), (100, 101, "Production 1")])
            session = make_audio_import_template_session()
            before = [root.to_bytes()[0] for root in session.root_items]
            with self.assertRaisesRegex(NotImplementedError, "reverse caller priority"):
                session._populate_audio_import_template(prepared)
            self.assertEqual([root.to_bytes()[0] for root in session.root_items], before)

    def test_overlap_check_matches_independent_pairwise_oracle(self):
        intervals = ((0, 10), (5, 12), (12, 20), (30, 40))
        for permutation in itertools.permutations(intervals):
            prepared = [{"track": "T", "start_samples": start, "end_samples": end}
                        for start, end in permutation]
            conflicts = any(a[0] > b[0] and max(a[0], b[0]) < min(a[1], b[1])
                            for i, a in enumerate(permutation) for b in permutation[i + 1:])
            if conflicts:
                with self.assertRaises(NotImplementedError):
                    _chronological_audio_import_placements(prepared)
            else:
                actual = _chronological_audio_import_placements(prepared)
                self.assertEqual([m["start_samples"] for _, m in actual], sorted(m["start_samples"] for m in prepared))

    def test_tracks_are_independent_even_when_their_ranges_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare(Path(directory), [(200, 100, "Production 1"),
                                                 (100, 300, "Production 2"),
                                                 (100, 100, "Production 1")])
            session = make_audio_import_template_session()
            session._populate_audio_import_template(prepared)
            self.assertEqual(event_records(session), {"Production 1": [(2, 100), (0, 200)],
                                                      "Production 2": [(1, 100)]})


FIXTURES = Path(__file__).parent / "fixtures" / "audio_import_event_order"


@unittest.skipUnless(all((FIXTURES / name).is_file() for name in
                        ("original.ptx", "chronological.ptx", "native_resaved.ptx")),
                     "Local native event-order fixtures absent (not distributed).")
class NativeAudioImportEventOrderTests(unittest.TestCase):
    @unittest.skipUnless((FIXTURES / "builder_native_resaved.ptx").is_file(),
                         "Local corrected-builder Pro Tools resave absent (not distributed).")
    def test_corrected_builder_native_save_preserves_131_placements_and_noop(self):
        path = FIXTURES / "builder_native_resaved.ptx"
        raw = path.read_bytes()
        session = ProToolsSession(path)
        reference = ProToolsSession(FIXTURES / "chronological.ptx")
        self.assertEqual(session.get_timeline_clips(), reference.get_timeline_clips())
        self.assertEqual(event_records(session), event_records(reference))
        with tempfile.TemporaryDirectory() as directory:
            for cycle in range(2):
                output = Path(directory) / ("noop_%d.ptx" % cycle)
                session.save(output)
                self.assertEqual(output.read_bytes(), raw)
                session = ProToolsSession(output)
                self.assertEqual(session.get_timeline_clips(), reference.get_timeline_clips())
        self.assertEqual(path.read_bytes(), raw)

    def test_native_131_event_oracle_only_reorders_playlist_and_preserves_semantics(self):
        original = ProToolsSession(FIXTURES / "original.ptx")
        chronological = ProToolsSession(FIXTURES / "chronological.ptx")
        native = ProToolsSession(FIXTURES / "native_resaved.ptx")
        rows = original.get_timeline_clips()
        self.assertEqual(len(rows), 131)
        self.assertEqual(chronological.get_timeline_clips(), rows)
        self.assertEqual(native.get_timeline_clips(), rows)
        differences = [a.content_type for a, b in zip(original.root_items, chronological.root_items)
                       if a.to_bytes()[0] != b.to_bytes()[0]]
        self.assertEqual(differences, [0x1054])
        by_name = {row["clip_name"]: row for row in rows}
        prepared = [dict(by_name[clip["name"]]) for clip in original.get_clips()]
        expected = [(i, row["start_samples"]) for i, row in _chronological_audio_import_placements(prepared)]
        self.assertEqual(next(iter(event_records(chronological).values())), expected)
        self.assertEqual(next(iter(event_records(native).values())), expected)
        # Legacy validator used the already sorted reader and missed this case.
        with self.assertRaisesRegex(ValueError, "playlist event order"):
            _validate_generated_audio_session(original, prepared, original.get_tracks())
        _validate_generated_audio_session(chronological, prepared, chronological.get_tracks())
        for name in ("chronological.ptx", "native_resaved.ptx"):
            raw = (FIXTURES / name).read_bytes()
            session = ProToolsSession(FIXTURES / name)
            with tempfile.TemporaryDirectory() as directory:
                for cycle in range(2):
                    output = Path(directory) / ("noop_%d.ptx" % cycle)
                    session.save(output)
                    self.assertEqual(output.read_bytes(), raw)
                    session = ProToolsSession(output)


if __name__ == "__main__":
    unittest.main()
