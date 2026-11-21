"""The voices the add-on offers, and the languages VoxMel speaks.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

- `voxmel-clone` clones the speaker from the video: the host hands over a
  clip of their speech and its transcript. VoxMel needs the transcript.
- The presets are VoxMel's own voices: each is a short clip VoxMel made
  without a reference, from a fixed sentence and seed (tools/make_presets.py),
  shipped in presets/ with its sentence, and cloned by every line. Shipped,
  not made again on the user's machine: another processor rounds a little
  differently and the same seed would come out as another voice.

VoxMel clones best within a language. An English line from a Mandarin clip
works; a Mandarin line from an English clip may slur words. So the presets
come in both languages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CLONE = 'voxmel-clone'
PRESETS_DIR = Path(__file__).resolve().parent / 'presets'

# VoxMel's languages, as BCP-47 primary subtags. It reads the language from
# the text and takes no language parameter.
LANGUAGES = ('en', 'zh')


@dataclass(frozen=True)
class Voice:
    id: str
    display_name: str
    language: str = '*'
    clip: str = ''        # preset clip in PRESETS_DIR; empty for the clone
    text: str = ''        # what the clip says


def _presets() -> tuple[Voice, ...]:
    data = json.loads((PRESETS_DIR / 'presets.json').read_text(encoding='utf-8'))
    return tuple(Voice(p['id'], p['display_name'], p['language'], p['clip'], p['text']) for p in data['presets'])


VOICES = (Voice(CLONE, "Speaker's own voice (cloned from the video)"),) + _presets()
BY_ID = {voice.id: voice for voice in VOICES}


def preset_path(voice: Voice) -> Path:
    return PRESETS_DIR / voice.clip


def manifest_voices() -> list[dict]:
    """The voice list as manifest.json and the hello frame carry it."""
    out = []
    for voice in VOICES:
        entry = {'id': voice.id, 'language': voice.language, 'display_name': voice.display_name}
        if voice.id == CLONE:
            entry['requires'] = ['voice_ref_audio', 'voice_ref_text']
        out.append(entry)
    return out
