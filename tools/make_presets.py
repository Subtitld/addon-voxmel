#!/usr/bin/env python3
"""Make the preset voices in voxmel_addon/presets/ with VoxMel itself.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Without a reference clip VoxMel speaks in a voice of its own, a different one
for each seed. Each preset is one such voice: a fixed sentence in its
language, spoken with a fixed seed, kept as a clip with its exact words, so
that every line can clone it. The seeds were chosen by listening for a
spread of voices, and by pitch (f0_hz, the median, recorded for reference).

The clips are made once, on the maintainer's machine, and committed:

    python tools/build_native.py
    python tools/make_presets.py MODEL.gguf

Running it again writes the same clips only on the same kind of processor.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voxmel_addon import audio, engine  # noqa: E402

SENTENCES = {
    'en': 'Good morning. Today we have a long road ahead of us, so it would be wise to set off early, '
          'before the rain begins.',
    'zh': '早上好。今天我们还有很长的路要走，所以最好早点出发，趁着还没下雨。',
}
# (id, display name, language, seed, median f0 in Hz as measured)
PRESETS = (
    ('voxmel-en-woman-1', 'English woman 1', 'en', 3, 183),
    ('voxmel-en-woman-2', 'English woman 2', 'en', 4, 174),
    ('voxmel-en-man-1', 'English man 1', 'en', 8, 94),
    ('voxmel-en-man-2', 'English man 2', 'en', 5, 108),
    ('voxmel-zh-woman-1', 'Mandarin woman 1', 'zh', 6, 260),
    ('voxmel-zh-woman-2', 'Mandarin woman 2', 'zh', 4, 223),
    ('voxmel-zh-man-1', 'Mandarin man 1', 'zh', 8, 133),
    ('voxmel-zh-man-2', 'Mandarin man 2', 'zh', 7, 141),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('model')
    args = parser.parse_args()
    out = ROOT / 'voxmel_addon' / 'presets'
    out.mkdir(parents=True, exist_ok=True)
    loaded = engine.load(Path(args.model))
    entries = []
    try:
        for voice_id, name, language, seed, f0 in PRESETS:
            samples, rate = engine.synthesize(loaded, SENTENCES[language], seed=seed)
            clip = voice_id.removeprefix('voxmel-') + '.wav'
            seconds = audio.write_wav(out / clip, samples, rate)
            entries.append({'id': voice_id, 'display_name': name, 'language': language, 'clip': clip,
                            'text': SENTENCES[language], 'seed': seed, 'f0_hz': f0})
            print(f'{voice_id}: {seconds:.1f} s')
    finally:
        engine.close(loaded)
    (out / 'presets.json').write_text(json.dumps({
        'model': f'{engine.MODEL_REPO}@{engine.MODEL_REVISION}/{engine.MODEL_FILES[0][0]}',
        'presets': entries}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
