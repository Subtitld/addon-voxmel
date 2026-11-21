"""The preset voices shipped in voxmel_addon/presets/.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json

from voxmel_addon import audio, voices


def test_every_preset_has_its_clip_and_words():
    data = json.loads((voices.PRESETS_DIR / 'presets.json').read_text(encoding='utf-8'))
    assert len(data['presets']) == 8
    for preset in data['presets']:
        clip = audio.read_wav(voices.PRESETS_DIR / preset['clip'])
        assert clip.rate == 24000 and clip.channels == 1
        assert 3.0 < clip.frames / clip.rate < 12.0, preset['id']
        assert preset['text'].strip() and preset['language'] in voices.LANGUAGES
        assert voices.BY_ID[preset['id']].text == preset['text']


def test_each_language_has_women_and_men():
    for language in voices.LANGUAGES:
        names = [v.display_name for v in voices.VOICES if v.language == language]
        assert sum('woman' in n for n in names) == 2 and sum(' man' in n for n in names) == 2, language
