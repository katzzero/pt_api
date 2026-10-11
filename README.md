Thank you to **Damien Zammit and Robin Gareus**, creators of [ptformat](https://github.com/zamaudio/ptformat), for their foundational work on the Pro Tools session format and decryption. Their project was an important reference and inspiration when pt_api began.

# v1.6.0 — What's new

A heartfelt thank you to **[JF Viguie (@oudileoudi)](https://github.com/oudileoudi)** for his generous contributions, fixes and shared format research. His work helped identify edge cases and substantially expand the API. The contributions were reviewed, adapted to the supported native profiles and covered by regression tests and Pro Tools checks.

### New capabilities

- **Routing and automation:** inspect and change supported mono/stereo bus outputs on mono Audio tracks; read, replace and merge mono/stereo Track Volume envelopes; read and write static Pan and mono/stereo Pan automation.
- **Audio editing:** delete supported timeline placements, trim compact virtual clips in place, retain selected sample ranges and append native mono 24-bit / 48 kHz WAVs to existing sessions.
- **Markers:** delete supported point markers, create markers with UTF-8 comments and read their comments without changing the default reader output.
- **AAF-derived sessions:** resolve verified anonymous or mixed named/anonymous mono playlists and explicitly fill missing playlist names from corroborated track identities.
- **Format coverage:** 25 fps non-drop timecode conversion, the verified long fade-out reader and compact virtual-clip reading.

### Fixes and reliability

- Protect playlist counters from being mistaken for block headers, including the 90-playlist edge case.
- Keep Volume associated with the correct track in mixed mono/stereo sessions; preserve supported Clip Gain envelopes while editing independent static gains, including shared and newly allocated gain points.
- Update the legacy marker-ruler population flag so API-created markers can be imported directly through Pro Tools Import Session Data.
- Preserve supported Pan curves on other tracks when editing a static target; reject ambiguous references, unknown layouts and overflows rather than guessing.
- Add regression coverage and a Windows/Linux CI workflow. **638 tests pass with the local reference corpus**; private native fixtures are optional and explicitly skipped when absent. The configured remote CI matrix is not presented as already executed.

These changes do not add universal AAF support, MXF editing or the contributor's unverified modern marker layouts. Full change history: [changelog.md](changelog.md).

## About pt_api

**pt_api is a standalone Python API for reading, editing and saving Pro Tools `.ptx` sessions without running Pro Tools.** It requires Python 3.8 or later and has no third-party runtime dependencies.

It exposes tracks, audio clips, Clip Groups, markers, routing and supported automation as programmatic operations. Applications provide the names, sample positions and durations; the API validates the binary structure and references, then rebuilds and re-encrypts the session on save. Existing templates are required for authoring; the API does not render audio or create arbitrary tracks from scratch.

## API capabilities

The table covers the public session, builder, timecode and binary APIs. **Support is profile-based:** reading a session does not imply that every editing operation is supported on it. Names and positions are caller-defined within the documented bounds, not restricted to audit examples.

| Capability | Public API | Scope and main limits |
|---|---|---|
| Open and save sessions | `ProToolsSession(path)`, `save()` | Parse/decrypt existing PTX files; rebuild references and save encrypted output atomically. Supported little-endian profiles only. |
| Inspect tracks and audio | `get_tracks()`, `get_clips()`, `get_timeline_clips()` | Main-playlist names, Clip List definitions and timeline audio/fades with sample ranges, source offsets and media filenames where resolvable. Track names are playlist-based: stereo may expose two channel playlists. |
| Rename and show template tracks | `rename_track()`, `set_visible_tracks()` | Rename existing verified slots and show the requested selection/display order, hiding the others. No creation or structural reordering of tracks. |
| Delete template tracks | `delete_tracks()` | Any requested combination in the verified **empty Audio-track pool** profile; every main Audio playlist must be empty and at least one track must remain. Not general populated-track deletion. |
| Fill missing playlist names | `fill_anonymous_playlist_names()` | Corroborated mono/stereo Audio identities; preserve existing names and all events. Mono AAF-derived output validated in Pro Tools; anonymous stereo composition tested automatically, not manually. |
| Build an audio session | `build_audio_session()`, `validate_audio_import_template()` | Populate existing empty template tracks, create catalogs/placements and copy source WAVs unchanged. Two strict mono float32 / 48 kHz / 23.976 template profiles; valid BWF metadata required. |
| Import native audio | `import_audio()` | Append a full native mono PCM24 / 48 kHz WAV using an explicit compatible parent prototype. Session must be 48 kHz / 23.976; WAV already in its `Audio Files` directory, with required native metadata. No copy or conversion. |
| Preflight and relink media | `get_relink_write_status()`, `relink_clip()` | Check a specific placement, then clone compatible native WAV/media/clip identities and retarget only that placement. Optional compatible rendered PCM replacement; known unwritable inherited-media layouts are rejected. |
| Rename, mute, move and duplicate audio | `rename_clip()`, `mute_clip()`, `move_clip()`, `duplicate_clip()` | Exact, unambiguous audio targets. Mute affects all visible placements of that definition; move/duplicate require one placement and reject attached fades. Not Clip Group macro editing. |
| Split and define subclips | `split_clip()`, `create_subclip()` | Split one supported root placement, or create a Clip List definition without placement. Verified source-offset/length combinations only; late splits and some long virtual combinations remain unsupported. |
| Trim by amount | `trim_clip_start()`, `trim_clip_end()` | Non-destructive trims in samples via supported virtual subclips; one placement, no attached fades. |
| Trim in place | `trim_clip_head()`, `trim_clip_tail()` | Absolute session sample cuts; preserve name/definition ID on the verified compact virtual/no-gain profile. No field-width growth or unsupported fade layouts. |
| Retain selected ranges | `keep_clip_regions()` | Keep non-overlapping absolute sample ranges, preserving the first fragment's identity and creating named additional fragments. Supported historical virtual/no-gain/no-fade profiles only. |
| Delete an audio placement | `delete_clip()` | Timeline-only: one unique plain placement, or placement with the verified single long fade-out. Keep Clip List/media; other fades, crossfades and ambiguous placements rejected. |
| Read timeline Clip Groups | `get_timeline_clip_groups()` | Every main-timeline group occurrence, including repeated placements: ID, name, track, sample range and timecodes. Separate from audio timeline events. |
| Create and name Clip Groups | `create_empty_clip_group()`, `create_clip_group()` | Place named empty groups at requested start/duration in the verified 48 kHz / 23.976 profile; or group one exact audio placement using an existing compatible group prototype. Existing tracks required. |
| Dissolve a Clip Group | `delete_clip_group()` | Ungroup the supported simple single-track profile and restore its components. Not arbitrary nested/multi-group editing; no separate existing-group rename API. |
| Read markers | `get_markers()` | Read index, name and timecode; filter by exact marker-ruler name. Optional `include_comments=True` validates legacy comment copies; unsupported comment layouts raise explicitly. |
| Add and delete markers | `add_marker()`, `delete_marker()` | Add legacy point markers at absolute sample positions, with optional UTF-8 comments. Delete by exact index/name on one verified legacy ruler; comment-bearing deletion and rich Memory Locations are not extended. |
| Fades and crossfades | `add_fade()`, `add_crossfade()` | Native Linear/Equal Power fades and centered Equal Power crossfades on supported placements, with adequate source handles and field-width limits. Long-fade reading does not enable every long-fade write operation. |
| Static Clip Gain | `set_clip_gain()` | Edit static gains, allocate initial points and isolate shared points. Preserve supported neighboring envelopes; no envelope editing or unverified modern gain-index layouts. Gain is per definition, not per placement. |
| Track Volume | `get_volume_automation()`, `set_volume_automation()`, `add_volume_node()` | Read, replace or merge verified named mono/stereo envelopes, one lane per track. Historical individual-node writer has a separate narrower resolver. UInt32 samples / Int16 deci-dB; no empty replacement envelope. |
| Static Pan | `get_track_pan()`, `get_track_pan_lr()`, `set_track_pan()` | Mono reader or mono/stereo left-right reader/writer; integer −100…100, existing verified static target lanes. Supported neighboring curves retained; target curves never flattened. |
| Pan automation | `get_pan_automation()`, `set_pan_automation()` | Read/replace an existing mono or selected stereo-channel envelope. UInt32 samples, unique ordered positions starting at zero; no general multichannel or new-lane authoring. |
| Principal output routing | `get_track_outputs()`, `set_track_output()` | Mono Audio tracks to verified autonomous mono/stereo buses. Strict identity/code/pan checks; unused targets require consistent two-bus calibration. No bus creation, hardware-output or Send editor. |
| Timecode conversion | `TimecodeEngine`: `get_frame_rate()`, `samples_to_timecode()`, `timecode_to_samples()`, `duration_to_samples()` | 23.976, 24, 25 non-drop and 29.97 drop-frame. Authoring profiles may impose a narrower frame rate. |
| Low-level binary tools | `PTBlock` (`to_bytes()`, `get_all_blocks()`), `unxor_session()`, `xor_session()` | Query/serialize block trees and decrypt/encrypt PTX bytes. Advanced operations remain subject to bounds, tree validation and reference integrity. |

Editing methods normally change the loaded session in memory: call `save()` explicitly. The builder publishes its own output directory; relinking also creates a new WAV. Keep session/media together and preserve the originals.

There is **no single global audio-duration limit**. The historical float32 builder profile and PCM24 append-import use UInt24 lengths (about 5:49.525 at 48 kHz); the newer builder profile uses UInt32. Split/trim limits depend on the verified offset/length combination. Consult the method's documented profile rather than extrapolating from another operation.

ZIP/CSV processing, AAF import/export, MXF editing, arbitrary track creation/deletion, MIDI/video editing, plugins, Inserts/Sends and general multichannel authoring are outside this API. Unknown PTX revisions or layouts are not guaranteed compatible.

Technical contracts and limitations: [pt_format_specs.md](pt_format_specs.md). Architecture overview: [architecture.md](architecture.md). Development continuity and validation history: [handoff.md](handoff.md). The detailed documentation is primarily in French.

Run the tests with `python -W error -m unittest discover -s tests`. Native manual checks use Pro Tools Ultimate 2024.3.1; a passing automated suite does not certify every PTX variant or replace validation of a new writing profile.

## Reverse engineering, license and disclaimer

pt_api is an **unofficial reverse-engineering and interoperability project**, developed through analysis of session files and comparisons with native Pro Tools behavior, with ptformat acknowledged as an initial reference. **No official Avid PTX-format documentation was used.** It is not affiliated with, endorsed by or supported by Avid; Pro Tools is an Avid product.

The current source distribution is licensed under **GNU LGPL-2.1-or-later**. See [LICENSE](LICENSE), [COPYING](COPYING) and [NOTICE.md](NOTICE.md) for license texts, upstream attribution and modification notices. Acknowledgment does not imply upstream endorsement, and the ongoing provenance review is not represented as legally resolved.

Provided **without warranty**. Work on copies, keep backups and validate generated or edited sessions in Pro Tools before production use.
