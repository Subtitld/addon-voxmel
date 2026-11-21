"""manifest.json agrees with the code: voices, languages, settings, version, model.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from voxmel_addon import __version__, engine, voices
from voxmel_addon import __main__ as addon

REPO = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((REPO / 'manifest.json').read_text(encoding='utf-8'))
FIELDS = {field['key']: field for field in MANIFEST['config_schema']['fields']}


def test_identity_and_version():
    assert MANIFEST['id'] == 'voxmel'
    assert MANIFEST['executable'] == 'bin/voxmel-addon'
    assert MANIFEST['tasks'] == ['tts.synthesize']
    pyproject = (REPO / 'pyproject.toml').read_text(encoding='utf-8')
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == __version__
    assert MANIFEST['version'] == __version__ == addon.ADDON_VERSION


def test_voices_and_languages_match_the_code():
    assert MANIFEST['voices'] == voices.manifest_voices()
    assert MANIFEST['languages'] == list(voices.LANGUAGES) == ['en', 'zh']
    clone = [v for v in MANIFEST['voices'] if v.get('requires')]
    assert [v['id'] for v in clone] == [voices.CLONE]
    presets = [v for v in voices.VOICES if v.id != voices.CLONE]
    assert len(presets) == 8
    assert {v.language for v in presets} == {'en', 'zh'}


def test_every_setting_the_code_reads_is_declared():
    read = set(addon._options())
    assert read <= set(FIELDS)
    # VoxMel cannot clone without the clip's transcript, so the host's
    # timbre-only switch is not offered.
    assert 'skip_ref_text' not in FIELDS


def test_setting_defaults_are_valid():
    for key, field in FIELDS.items():
        if field['type'] == 'select':
            assert field['default'] in [o['value'] for o in field['options']], key
        if field['type'] in ('int', 'number'):
            assert field['min'] <= field['default'] <= field['max'], key


def test_the_code_clamps_numbers_to_the_declared_range(monkeypatch):
    for key in ('steps', 'cfg', 'speaker_cfg', 'threads'):
        field = FIELDS[key]
        monkeypatch.setenv(f'VOXMEL_{key.upper()}', str(field['max'] * 10 + 1))
        assert addon._options()[key] == field['max'], key
        monkeypatch.setenv(f'VOXMEL_{key.upper()}', str(field['min'] - 1))
        assert addon._options()[key] == field['min'], key


def test_the_model_entry_is_the_pinned_revision():
    model = MANIFEST['models'][0]
    assert model['repo'] == engine.MODEL_REPO
    assert model['revision'] == engine.MODEL_REVISION
    assert model['size_mb'] == round(engine.MODEL_BYTES / 2**20)
    assert model['bundled'] is False
    assert MANIFEST['disk_required_mb'] > model['size_mb']


def test_cpu_lists_parse():
    assert engine._cpu_list('0-3,8,10-11\n') == [0, 1, 2, 3, 8, 10, 11]


def test_the_settings_are_the_ones_this_runtime_has():
    assert MANIFEST['compute'] == 'cpu' and MANIFEST['accelerators'] == ['mps']
    assert FIELDS['steps']['default'] == engine.DEFAULT_STEPS
    assert 'Apache-2.0' in MANIFEST['license'] and 'Vocos' in MANIFEST['license']
    assert MANIFEST['ram_required_mb'] >= 2048
