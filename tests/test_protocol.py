"""The tts.synthesize handler, with a stand-in for the model.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from voxmel_addon import engine, voices

REPO = Path(__file__).resolve().parents[1]


def _terminal(frames):
    terminal = [f for f in frames if f.get('type') in ('result', 'error')]
    assert len(terminal) == 1, frames
    return terminal[0]


def test_clone_with_transcript(run, reference, tmp_path):
    out = tmp_path / 'line.wav'
    frames, fake = run({'text': '  Hello,   world ', 'voice': voices.CLONE, 'output_path': str(out),
                        'language': 'en', 'voice_ref_audio': reference, 'voice_ref_text': 'Hello there',
                        'seed': 7, 'rate': 30})
    result = _terminal(frames)
    assert result['type'] == 'result', result
    text, kwargs = fake.calls[0]
    assert text == 'Hello, world'
    assert kwargs['reference'] == reference and kwargs['reference_text'] == 'Hello there'
    assert kwargs['seed'] == 7 and kwargs['steps'] == engine.DEFAULT_STEPS
    assert kwargs['cfg_value'] == 3.5 and kwargs['speaker_cfg'] == 3.0
    data = result['data']
    assert data['path'] == str(out) and data['sample_rate'] == 24000 and data['channels'] == 1
    with wave.open(str(out)) as clip:
        assert (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) == (1, 2, 24000)
        assert clip.getnframes() == 2400
    values = [f['value'] for f in frames if f['type'] == 'progress']
    assert values == sorted(values) and values[-1] == 1.0


def test_clone_without_transcript_is_refused(run, reference, tmp_path):
    frames, fake = run({'text': 'Hi', 'voice': voices.CLONE, 'output_path': str(tmp_path / 'a.wav'),
                        'voice_ref_audio': reference})
    error = _terminal(frames)
    assert error['code'] == 'bad_params' and 'words spoken in the clip' in error['message']
    assert not fake.calls


def test_clone_falls_back_to_the_configured_clip(run, reference, tmp_path, monkeypatch):
    monkeypatch.setenv('VOXMEL_VOICE_REF_AUDIO', reference)
    monkeypatch.setenv('VOXMEL_VOICE_REF_TEXT', 'configured words')
    frames, fake = run({'text': 'Hi', 'voice': voices.CLONE, 'output_path': str(tmp_path / 'a.wav')})
    assert _terminal(frames)['type'] == 'result'
    assert fake.calls[0][1]['reference_text'] == 'configured words'


def test_the_configured_transcript_never_pairs_with_the_hosts_clip(run, reference, tmp_path, monkeypatch):
    monkeypatch.setenv('VOXMEL_VOICE_REF_TEXT', 'words of another clip')
    frames, fake = run({'text': 'Hi', 'voice': voices.CLONE, 'output_path': str(tmp_path / 'a.wav'),
                        'voice_ref_audio': reference})
    assert _terminal(frames)['code'] == 'bad_params'
    assert not fake.calls


def test_clone_without_any_clip_is_refused(run, tmp_path):
    frames, fake = run({'text': 'Hi', 'voice': voices.CLONE, 'output_path': str(tmp_path / 'a.wav')})
    error = _terminal(frames)
    assert error['type'] == 'error' and error['code'] == 'bad_params'
    assert not fake.calls


def test_a_preset_clones_its_shipped_clip(run, tmp_path):
    frames, fake = run({'text': '你好', 'voice': 'voxmel-zh-woman-1', 'language': 'zh',
                        'output_path': str(tmp_path / 'a.wav')})
    assert _terminal(frames)['type'] == 'result'
    (text, kwargs), = fake.calls
    preset = voices.BY_ID['voxmel-zh-woman-1']
    assert text == '你好'
    assert kwargs['reference'] == str(voices.preset_path(preset)) and Path(kwargs['reference']).is_file()
    assert kwargs['reference_text'] == preset.text


def test_settings_reach_the_engine(run, tmp_path, monkeypatch):
    monkeypatch.setenv('VOXMEL_STEPS', '12')
    monkeypatch.setenv('VOXMEL_CFG', '2.5')
    monkeypatch.setenv('VOXMEL_SPEAKER_CFG', '4')
    _frames, fake = run({'text': 'Hi', 'voice': 'voxmel-en-man-1', 'output_path': str(tmp_path / 'a.wav')})
    kwargs = fake.calls[0][1]
    assert (kwargs['steps'], kwargs['cfg_value'], kwargs['speaker_cfg']) == (12, 2.5, 4.0)


def test_progress_estimate():
    from voxmel_addon import __main__ as addon
    assert addon._expected_frames('早上好今天') == pytest.approx(max(20.0, 5 / 4.5 * engine.FRAMES_PER_SECOND))
    assert addon._expected_frames('a' * 140) == pytest.approx(10 * engine.FRAMES_PER_SECOND)
    assert addon._expected_frames('') == 20.0


def test_unknown_voice_and_missing_fields(run, tmp_path):
    error = _terminal(run({'text': 'Hi', 'voice': 'qwen3-ryan', 'output_path': str(tmp_path / 'a.wav')})[0])
    assert error['code'] == 'unsupported_voice'
    assert _terminal(run({'text': '   ', 'voice': voices.CLONE, 'output_path': 'x.wav'})[0])['code'] == 'bad_params'
    assert _terminal(run({'text': 'Hi', 'voice': voices.CLONE})[0])['code'] == 'bad_params'


def test_cancel_stops_the_line_between_steps(run, reference, tmp_path):
    out = tmp_path / 'a.wav'
    frames, _fake = run({'text': 'Hi', 'voice': voices.CLONE, 'output_path': str(out),
                         'voice_ref_audio': reference, 'voice_ref_text': 'Hello'}, cancel_at_step=3)
    error = _terminal(frames)
    assert error['type'] == 'error' and error['code'] == 'cancelled'
    assert not out.exists()


def test_settings_are_held_to_their_range(monkeypatch):
    from voxmel_addon import __main__ as addon
    monkeypatch.setenv('VOXMEL_STEPS', '500')
    monkeypatch.setenv('VOXMEL_CFG', 'lots')
    options = addon._options()
    assert options['steps'] == 32 and options['cfg'] == 3.5


def test_the_process_says_hello_and_leaves_on_shutdown():
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.Popen([sys.executable, '-m', 'voxmel_addon'], cwd=REPO, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        hello = json.loads(proc.stdout.readline())
        proc.stdin.write(b'{"type":"ready","host":"test"}\n')
        proc.stdin.write(b'{"id":"x","type":"asr.transcribe","params":{}}\n')
        proc.stdin.flush()
        error = json.loads(proc.stdout.readline())
        proc.stdin.write(b'{"type":"shutdown"}\n')
        proc.stdin.flush()
        assert proc.wait(timeout=20) == 0
    finally:
        proc.kill()
    assert hello['type'] == 'hello' and hello['addon'] == 'voxmel'
    capability = hello['capabilities'][0]
    assert capability['task'] == 'tts.synthesize' and capability['voice_clone'] is True
    assert {v['id'] for v in capability['voices']} == set(voices.BY_ID)
    assert capability['languages'] == ['en', 'zh']
    assert error == {'id': 'x', 'type': 'error', 'code': 'bad_params',
                     'message': "unsupported frame type 'asr.transcribe'", 'retryable': False}


def test_a_model_that_fails_on_metal_loads_on_the_cpu(run, reference, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'choose_backend', lambda path: 'metal')
    backends = []

    def load(path, threads=0, backend='cpu'):
        backends.append(backend)
        if backend == 'metal':
            raise engine.EngineError('session: no Metal device')
        return engine.Loaded(lib=None, registry=None, model=None, session=None, threads=4, backend=backend)

    monkeypatch.setattr(engine, 'load', load)
    frames, _fake = run({'text': 'Hi', 'voice': voices.CLONE, 'output_path': str(tmp_path / 'a.wav'),
                         'voice_ref_audio': reference, 'voice_ref_text': 'Hello'})
    assert _terminal(frames)['type'] == 'result'
    assert backends == ['metal', 'cpu']
