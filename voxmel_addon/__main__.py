"""VoxMel add-on for Subtitld — JSON-line protocol entry point.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License"); you may not
use this file except in compliance with the License. You may obtain a copy of
the License at http://www.apache.org/licenses/LICENSE-2.0. Unless required by
applicable law or agreed to in writing, software distributed under the License
is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.

What this add-on is
-------------------
Text-to-speech with VoxDance Lab's VoxMel 0.6B (Apache-2.0), in English and
Mandarin, at 24 kHz, run by audio.cpp. Serves `tts.synthesize`. A line is
spoken in the speaker's own voice, cloned from the video and its words, or in
one of eight preset voices (see voices.py). The model, 1.04 GB, downloads on
the first synthesis (see engine.py); after that nothing leaves the machine.

Wire format
-----------
One JSON object per line on stdin/stdout. Stderr is free-form logging, which
the host prefixes with `[addon:voxmel]` and surfaces.

Options (the host sets them from Configure as VOXMEL_<KEY>)
-----------------------------------------------------------
steps, cfg, speaker_cfg, threads; voice_ref_audio and voice_ref_text, a clone
reference to fall back on when the host sends none.

Limits
------
One synthesis at a time: the session holds one model state. A cancel stops a
line between two Mel frames, or stops the download (which resumes on the next
request).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from voxmel_addon import ADDON_ID, PROTOCOL_VERSION, __version__
from voxmel_addon import audio, engine, tls, voices

ADDON_VERSION = __version__

ERR_MODEL_MISSING = 'model_missing'
ERR_UNSUPPORTED_VOICE = 'unsupported_voice'
ERR_BAD_PARAMS = 'bad_params'
ERR_INTERNAL = 'internal'
ERR_CANCELLED = 'cancelled'
ERR_OOM = 'oom'
ERR_DISK_FULL = 'disk_full'
ERR_NETWORK = 'network_unavailable'

log = logging.getLogger('voxmel_addon')


# ---------------------------------------------------------------------------
# stdio ownership + protocol frames
# ---------------------------------------------------------------------------
_PROTO_IN = None   # binary reader for host frames
_PROTO_OUT = None  # binary writer for our frames
_proto_lock = threading.Lock()


def _claim_protocol_stdio() -> None:
    """Give the protocol exclusive use of the real stdin/stdout.

    fd-level, not just `sys.stdout = sys.stderr`: native code (audio.cpp,
    ggml) may write to fd 1 directly. Falls back to the
    Python-level swap if the fd dance fails.
    """
    global _PROTO_IN, _PROTO_OUT
    try:
        sys.stdout.flush()
        in_fd = os.dup(0)
        out_fd = os.dup(1)
        devnull = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(devnull, 0)
        finally:
            os.close(devnull)
        os.dup2(2, 1)
        _PROTO_IN = os.fdopen(in_fd, 'rb')
        _PROTO_OUT = os.fdopen(out_fd, 'wb')
    except (OSError, ValueError, AttributeError):
        _PROTO_IN = sys.stdin.buffer
        _PROTO_OUT = sys.stdout.buffer
    sys.stdin = open(os.devnull, encoding='utf-8')  # stray input() -> EOFError
    sys.stdout = sys.stderr                        # stray print() -> the log


def _emit(frame: dict) -> None:
    data = (json.dumps(frame, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    out = _PROTO_OUT if _PROTO_OUT is not None else sys.stdout.buffer
    with _proto_lock:
        try:
            out.write(data)
            out.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # host went away; the frame loop exits on the next empty read


def _emit_progress(req_id: str, value: float, message: str = '') -> None:
    _emit({'id': req_id, 'type': 'progress',
           'value': max(0.0, min(1.0, float(value))), 'message': message})


def _emit_result(req_id: str, data: dict) -> None:
    _emit({'id': req_id, 'type': 'result', 'data': data})


def _emit_error(req_id: str, code: str, message: str, retryable: bool = False) -> None:
    _emit({'id': req_id, 'type': 'error', 'code': code,
           'message': message, 'retryable': retryable})


class _AddonError(Exception):
    """An expected failure that already knows its protocol code."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
def _env(*names: str) -> str:
    """First non-empty value among `names`, stripped. '' when none are set."""
    for name in names:
        value = (os.environ.get(name) or '').strip()
        if value:
            return value
    return ''


def _env_number(name: str, default: float, low: float, high: float) -> float:
    raw = _env(name)
    try:
        value = float(raw) if raw else default
    except ValueError:
        log.warning('%s=%r is not a number; using %s', name, raw, default)
        value = default
    return max(low, min(high, value))


def _options() -> dict:
    return {
        'steps': int(_env_number('VOXMEL_STEPS', engine.DEFAULT_STEPS, 8, 32)),
        'cfg': _env_number('VOXMEL_CFG', 3.5, 0.0, 8.0),
        'speaker_cfg': _env_number('VOXMEL_SPEAKER_CFG', 3.0, 0.0, 8.0),
        'threads': int(_env_number('VOXMEL_THREADS', 0, 0, 256)),
        'voice_ref_audio': _env('VOXMEL_VOICE_REF_AUDIO'),
        'voice_ref_text': _env('VOXMEL_VOICE_REF_TEXT'),
    }


# ---------------------------------------------------------------------------
# ffmpeg, for references that are not PCM WAV (see audio.py)
# ---------------------------------------------------------------------------

def _find_ffmpeg() -> str | None:
    for candidate in (_env('SUBTITLD_FFMPEG_EXECUTABLE'), _env('FFMPEG_EXECUTABLE')):
        if candidate and (os.path.isfile(candidate) or shutil.which(candidate)):
            return candidate
    return shutil.which('ffmpeg')


def _no_window_startupinfo():
    """Keep ffmpeg from flashing a console window on Windows GUI hosts."""
    if sys.platform != 'win32':
        return None
    si = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    si.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
    return si


def _child_env() -> dict | None:
    """Environment for ffmpeg, or None to inherit ours unchanged.

    The frozen Linux bootloader prepends the bundle's `_internal/` to
    LD_LIBRARY_PATH, and a system ffmpeg that inherits it loads the bundle's
    libraries instead of its own. PyInstaller saves the host value in
    LD_LIBRARY_PATH_ORIG, so restore it for the child.
    """
    if not (getattr(sys, 'frozen', False) and sys.platform.startswith('linux')):
        return None
    env = dict(os.environ)
    original = env.get('LD_LIBRARY_PATH_ORIG')
    if original is not None:
        env['LD_LIBRARY_PATH'] = original
    else:
        env.pop('LD_LIBRARY_PATH', None)
    return env


def _readable_reference(path: str, scratch: Path) -> str:
    """`path` itself if it is PCM WAV, else a 16 kHz WAV of it made by ffmpeg.

    Subtitld's own clips are 16 kHz mono 16-bit WAV, read as they are.
    """
    try:
        audio.read_wav(path)
        return path
    except audio.UnreadableAudio:
        pass
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise _AddonError(ERR_BAD_PARAMS,
                          f'The reference {Path(path).name} is not PCM WAV, and no ffmpeg was '
                          f'found to convert it. Use a WAV file.')
    out = scratch / 'reference.wav'
    cmd = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', path,
           '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(out)]
    done = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, env=_child_env(),
                          startupinfo=_no_window_startupinfo())
    if done.returncode != 0 or not out.is_file():
        tail = ' / '.join(done.stderr.decode('utf-8', 'replace').strip().splitlines()[-3:])
        raise _AddonError(ERR_BAD_PARAMS, f'Could not read the reference {Path(path).name}: {tail}')
    return str(out)


# ---------------------------------------------------------------------------
# The model: loaded on first use, shared by every request
# ---------------------------------------------------------------------------
_loaded: engine.Loaded | None = None
_model_lock = threading.Lock()   # held for the whole synthesis: one KV cache


def _load_model(state: '_Request', options: dict) -> engine.Loaded:
    """Download (once) and load. The caller holds _model_lock."""
    global _loaded
    if _loaded is not None:
        return _loaded
    folder = engine.model_dir()
    if not engine.weights_present(folder):
        last = [0.0]

        def progress(done: int, total: int) -> None:
            now = time.monotonic()
            if now - last[0] < 1.0 and done < total:
                return
            last[0] = now
            share = done / total if total else 1.0
            _emit_progress(state.req_id, 0.2 * share,
                           f'Downloading VoxMel, once: {done / 1e9:.2f} of {total / 1e9:.2f} GB')

        log.info('weights missing in %s; downloading', folder)
        try:
            engine.ensure_weights(folder, progress, state.cancel.is_set)
        except engine.Cancelled:
            raise
        except engine.DownloadError as exc:
            raise _AddonError(ERR_NETWORK, f'Could not download VoxMel: {exc}', retryable=True) from exc
        except OSError as exc:
            if getattr(exc, 'errno', None) == 28:
                raise _AddonError(ERR_DISK_FULL,
                                  f'Out of disk space downloading VoxMel to {folder} '
                                  f'(it needs {engine.MODEL_BYTES / 1e9:.1f} GB).') from exc
            raise
    _emit_progress(state.req_id, 0.2, 'Loading VoxMel...')
    path = folder / engine.MODEL_FILE
    backend = engine.choose_backend(path)
    started = time.monotonic()
    try:
        try:
            _loaded = engine.load(path, options['threads'], backend)
        except engine.EngineError as exc:
            if backend == 'cpu':
                raise
            log.warning('VoxMel did not load on %s (%s); using the CPU', backend, exc)
            _loaded = engine.load(path, options['threads'], 'cpu')
    except engine.EngineError as exc:
        raise _AddonError(ERR_INTERNAL, f'VoxMel could not be loaded: {exc}') from exc
    log.info('VoxMel loaded in %.1fs on the %s, %d threads%s', time.monotonic() - started,
             'GPU (Metal)' if _loaded.backend == 'metal' else 'CPU', _loaded.threads,
             f' on CPUs {_loaded.cpus[0]}-{_loaded.cpus[-1]} (the performance cores)' if _loaded.cpus else '')
    return _loaded


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------
class _Request:
    def __init__(self, req_id: str):
        self.req_id = req_id
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None

    def check(self, _step: int = 0) -> None:
        if self.cancel.is_set():
            raise engine.Cancelled()


_active: dict[str, _Request] = {}
_active_lock = threading.Lock()


def _expected_frames(text: str) -> float:
    """Mel frames a line will take, roughly: about 14 letters, or 4.5 Chinese characters, a second."""
    han = sum(1 for ch in text if '\u3400' <= ch <= '\u9fff' or '\uf900' <= ch <= '\ufaff')
    seconds = han / 4.5 + (len(text) - han) / 14.0
    return max(20.0, seconds * engine.FRAMES_PER_SECOND)


def _seed(params: dict) -> int:
    try:
        return int(params.get('seed', 0)) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return 0


def _handle_synthesize(state: _Request, params: dict) -> None:
    req_id = state.req_id
    text = ' '.join(str(params.get('text') or '').split())
    voice_id = str(params.get('voice') or '')
    output_path = params.get('output_path')
    if not text:
        _emit_error(req_id, ERR_BAD_PARAMS, 'text is empty')
        return
    if not isinstance(output_path, str) or not output_path.strip():
        _emit_error(req_id, ERR_BAD_PARAMS, 'output_path is required')
        return
    voice = voices.BY_ID.get(voice_id)
    if voice is None:
        _emit_error(req_id, ERR_UNSUPPORTED_VOICE, f'unknown voice {voice_id!r}')
        return
    options = _options()
    seed = _seed(params)

    # What the line clones: the reference the host sent, else the one in
    # Configure; or a preset's clip.
    reference = transcript = ''
    if voice.id == voices.CLONE:
        sent = str(params.get('voice_ref_audio') or '')
        if sent:
            reference, transcript = sent, ' '.join(str(params.get('voice_ref_text') or '').split())
        else:
            reference, transcript = options['voice_ref_audio'], options['voice_ref_text']
        if not reference:
            _emit_error(req_id, ERR_BAD_PARAMS,
                        "The speaker's own voice needs a clip of it, and none came with the request. "
                        'Pick a reference clip in Configure, or another voice.')
            return
        if not os.path.isfile(reference):
            _emit_error(req_id, ERR_BAD_PARAMS, f'The reference clip is missing: {reference}')
            return
        if not transcript:
            _emit_error(req_id, ERR_BAD_PARAMS,
                        "VoxMel clones a voice only with the words spoken in the clip, and none came "
                        "with the request. Turn off 'Clone the timbre only' for this speaker, or give the "
                        'fallback clip its transcript in Configure.')
            return
    else:
        reference, transcript = str(voices.preset_path(voice)), voice.text

    outcome: tuple = ('error', ERR_INTERNAL, 'no outcome', False)
    scratch = Path(tempfile.mkdtemp(prefix='voxmel-', dir=_env('SUBTITLD_TEMP_DIR') or None))
    try:
        state.check()
        _emit_progress(req_id, 0.0, 'Waiting for VoxMel...')
        with _model_lock:
            state.check()
            loaded = _load_model(state, options)
            state.check()
            clip = _readable_reference(reference, scratch) if voice.id == voices.CLONE else reference
            mode = 'clone' if voice.id == voices.CLONE else 'preset'

            # Progress: 0.3 -> 0.95 against the line's expected length. A long
            # line is made in chunks, each counting from 0 again; the bar does
            # not go back.
            expected = _expected_frames(text)
            shown = [0.3]

            def on_step(index: int) -> None:
                state.check()
                value = 0.3 + 0.65 * min(1.0, (index + 1) / expected)
                if index % 8 == 0 and value > shown[0]:
                    shown[0] = value
                    _emit_progress(req_id, value, 'Speaking...')

            log.info('synthesize %s: voice=%s mode=%s seed=%d steps=%d chars=%d',
                     req_id, voice.id, mode, seed, options['steps'], len(text))
            started = time.monotonic()
            samples, rate = engine.synthesize(loaded, text, reference=clip, reference_text=transcript,
                                              cfg_value=options['cfg'], speaker_cfg=options['speaker_cfg'],
                                              steps=options['steps'], seed=seed, on_step=on_step)
            took = time.monotonic() - started
        state.check()
        duration = audio.write_wav(Path(output_path), samples, rate)
        log.info('synthesize %s: %.2fs of audio in %.1fs', req_id, duration, took)
        outcome = ('result', {
            'path': output_path,
            'duration_sec': round(duration, 3),
            'sample_rate': rate,
            'channels': 1,
            'voice': voice.id,
        })
    except engine.Cancelled:
        log.info('synthesize %s: cancelled', req_id)
        outcome = ('error', ERR_CANCELLED, 'cancelled', False)
    except _AddonError as exc:
        log.warning('synthesize %s failed: [%s] %s', req_id, exc.code, exc.message)
        outcome = ('error', exc.code, exc.message, exc.retryable)
    except MemoryError:
        outcome = ('error', ERR_OOM, 'Out of memory. VoxMel needs about 2 GB free.', False)
    except Exception as exc:  # noqa: BLE001 — every path must end in one terminal frame
        log.exception('synthesize %s failed', req_id)
        if 'out of memory' in str(exc).lower():
            outcome = ('error', ERR_OOM, f'Out of memory: {exc}', False)
        else:
            outcome = ('error', ERR_INTERNAL, f'{type(exc).__name__}: {exc}', False)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if outcome[0] == 'result':
        _emit_progress(req_id, 1.0, '')
        _emit_result(req_id, outcome[1])
    else:
        _emit_error(req_id, *outcome[1:])


def _spawn(req_id: str, target, *args) -> None:
    state = _Request(req_id)
    with _active_lock:
        _active[req_id] = state

    def _run():
        try:
            target(state, *args)
        except BaseException:  # noqa: BLE001
            log.exception('worker crashed')
            tail = traceback.format_exc().strip().splitlines()[-1]
            _emit_error(req_id, ERR_INTERNAL, f'worker crashed: {tail}')
        finally:
            with _active_lock:
                _active.pop(req_id, None)

    thread = threading.Thread(target=_run, name=f'voxmel-{req_id}', daemon=True)
    state.thread = thread
    thread.start()


# ---------------------------------------------------------------------------
# Handshake + frame loop
# ---------------------------------------------------------------------------
def _send_hello() -> None:
    _emit({
        'type': 'hello',
        'protocol': PROTOCOL_VERSION,
        'addon': ADDON_ID,
        'version': ADDON_VERSION,
        'capabilities': [{
            'task': 'tts.synthesize',
            'languages': list(voices.LANGUAGES),
            'voices': voices.manifest_voices(),
            'voice_clone': True,
        }],
    })


def _read_frame() -> dict | None:
    source = _PROTO_IN if _PROTO_IN is not None else sys.stdin.buffer
    line = source.readline()
    if not line:
        return None  # stdin closed: the host went away
    text = line.decode('utf-8', errors='replace').strip().lstrip('﻿')
    if not text:
        return {}
    try:
        frame = json.loads(text)
    except ValueError as exc:
        log.warning('malformed frame %r: %s', line[:200], exc)
        return {}
    return frame if isinstance(frame, dict) else {}


def _main_loop() -> int:
    while True:
        frame = _read_frame()
        if frame is None:
            log.info('stdin closed; exiting')
            return 0
        if not frame:
            continue
        ftype = frame.get('type')

        if ftype == 'shutdown':
            log.info('shutdown requested')
            return 0

        if ftype == 'ready':
            log.info('handshake complete; host=%s host_version=%s',
                     frame.get('host'), frame.get('host_version'))
            continue

        if ftype == 'cancel':
            target = frame.get('target')
            with _active_lock:
                state = _active.get(target) if isinstance(target, str) else None
            if state is not None:
                log.info('cancel %s', target)
                state.cancel.set()
            continue

        if ftype == 'tts.synthesize':
            req_id = frame.get('id')
            params = frame.get('params')
            if not isinstance(req_id, str) or not req_id:
                log.warning('tts.synthesize without id')
                continue
            if params is None:
                params = {}
            if not isinstance(params, dict):
                _emit_error(req_id, ERR_BAD_PARAMS, 'params must be an object')
                continue
            _spawn(req_id, _handle_synthesize, params)
            continue

        # Unknown task: still answer, so the host's future resolves instead of
        # hanging until its timeout.
        req_id = frame.get('id')
        if isinstance(req_id, str) and req_id:
            _emit_error(req_id, ERR_BAD_PARAMS, f'unsupported frame type {ftype!r}')


def _stop_workers(budget_sec: float = 3.5) -> bool:
    """Cancel and join every worker. True when none is left running.

    The host allows 5 s between `shutdown` and SIGTERM, so stay under it.
    """
    with _active_lock:
        states = list(_active.values())
    for state in states:
        state.cancel.set()
    deadline = time.monotonic() + budget_sec
    for state in states:
        if state.thread is not None:
            state.thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return not any(s.thread is not None and s.thread.is_alive() for s in states)


def _self_test() -> int:
    """Exercise what the frozen bundle needs to synthesize, without the model.

    Loads audio.cpp and finds VoxMel in it, reads and writes a clip, and
    makes the TLS context the download uses, so a library or module left out
    of the bundle fails the build instead of the user's first line.
    """
    import math
    import ssl
    from array import array

    families = engine.families()
    if 'voxmel_tts' not in families:
        raise SystemExit(f'audio.cpp has no voxmel_tts family, only {families}')
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / 'tone.wav'
        audio.write_wav(clip, array('f', (0.3 * math.sin(2 * math.pi * 220 * i / 16000) for i in range(16000))),
                        16000)
        frames = audio.read_wav(clip).frames
    engine._ssl_context()
    report = {
        'ok': True,
        'version': ADDON_VERSION,
        'runtime': engine.runtime_version(),
        'families': families,
        'threads': engine.default_threads(),
        'performance_cpus': engine.performance_cpus(),
        'apple_silicon': engine.apple_silicon(),
        'frames': frames,
        'openssl': ssl.OPENSSL_VERSION,
    }
    print(json.dumps(report), file=sys.__stdout__, flush=True)
    return 0


def main() -> int:
    if '--version' in sys.argv[1:]:
        print(f'voxmel-addon {ADDON_VERSION}')
        return 0
    logging.basicConfig(
        level=(_env('VOXMEL_ADDON_LOG_LEVEL') or 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        stream=sys.stderr,
    )
    tls.configure_ca_bundle()
    if '--self-test' in sys.argv[1:]:
        return _self_test()
    if '--probe-metal' in sys.argv[1:]:
        # engine.metal_works() runs this in a child: Metal may crash the process.
        engine.probe_metal(Path(sys.argv[sys.argv.index('--probe-metal') + 1]))
        print('metal works', file=sys.__stdout__, flush=True)
        return 0
    _claim_protocol_stdio()
    log.info('voxmel-addon %s on %s %s, python %s',
             ADDON_VERSION, platform.system(), platform.machine(), platform.python_version())

    _send_hello()
    rc = 1
    try:
        rc = _main_loop()
    except KeyboardInterrupt:
        rc = 130
    except Exception:  # noqa: BLE001
        log.exception('main loop crashed')
        rc = 1
    finally:
        stopped = _stop_workers()
    if not stopped:
        # A worker is still inside audio.cpp (a step, or loading the model).
        # Normal teardown could free what it is using, so leave without it.
        log.warning('a synthesis is still running; exiting without cleanup')
        try:
            sys.stderr.flush()
        finally:
            os._exit(rc)
    return rc


if __name__ == '__main__':
    sys.exit(main())
