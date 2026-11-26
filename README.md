# VoxMel add-on for Subtitld

Text-to-speech for [Subtitld](https://subtitld.org) dubbing with VoxDance
Lab's [VoxMel](https://github.com/VoxDance-Lab/VoxMel) 0.6B: English and
Mandarin at 24 kHz, in the speaker's own voice cloned from the video, or in
one of eight preset voices. The add-on, the model and the preset voices are
Apache-2.0, so dubs can be used commercially.

## Voices

- **Speaker's own voice (cloned from the video).** Subtitld cuts a clip of the
  speaker from the video and sends it with the subtitle text spoken in it.
  VoxMel clones the voice from the clip and its words, very closely within a
  language. It needs those words: *Clone the timbre only* doesn't work with
  this add-on. An English line from a Mandarin clip works well; a Mandarin
  line from an English clip may slur words.
- **Preset voices**: English woman 1 and 2, English man 1 and 2, Mandarin
  woman 1 and 2, Mandarin man 1 and 2. Each is a short clip VoxMel made with
  no reference recording, from a fixed sentence and seed
  (`tools/make_presets.py`), shipped in `voxmel_addon/presets/` and cloned by
  every line, so the voice stays the same through a video. No person's voice
  was recorded for them.

The voice is chosen per speaker in Subtitld's dubbing panel.

## Languages

English and Mandarin Chinese. VoxMel reads the language from the text.

## What it needs

- **Disk:** about 1.2 GB. The model, 1.04 GB, downloads from Hugging Face on
  the first synthesis, at a pinned revision, checked by sha256, into
  Subtitld's model cache. An interrupted download resumes. `HF_ENDPOINT`
  points it at a mirror; `VOXMEL_MODEL_DIR` at a folder that already holds
  the file.
- **Memory:** about 2 GB free.
- **Time:** on an Intel Core i7-13700T (8 performance cores), a cloned line
  takes two to four seconds per second of speech, and a line with no
  reference about one and a half. On an Apple Silicon Mac it runs on the GPU.

After the download nothing leaves the machine.

## Settings

| Setting | Default | |
|---|---|---|
| Quality steps | 32 | flow steps for each moment of speech; 8 to 32 |
| Text guidance | 3.5 | how closely speech follows the text |
| Voice guidance | 3.0 | how closely a cloned voice follows the clip |
| CPU threads | 0 | the performance cores on a CPU that has efficiency cores too, else one per core |
| Fallback reference clip / transcript | | for the cloned voice when Subtitld has no clip to send |

VoxMel has no control of pace or length; Subtitld's own *Fit dub to subtitle
duration* stretches a line when needed.

## How it runs the model

Speech is made by [audio.cpp](https://github.com/0xShug0/audio.cpp)
(Apache-2.0), a C++ runtime on ggml, which the add-on calls through its C API
with ctypes. `tools/build_native.py` builds it from source at a pinned commit
(on main after v0.9.5: VoxMel arrived after that release) with only VoxMel
compiled in, and applies one patch, `tools/patches/audiocpp-step-callback.patch`:
a callback before each generated Mel frame, so a line reports its progress
and stops between two frames when it is cancelled. ggml picks the build for
the processor it runs on, from SSE4.2 to AVX-512. On an Apple Silicon Mac it
runs on the GPU (Metal), once a short test line in a separate process has
shown that works there; `VOXMEL_DEVICE=cpu` or `metal` skips the test.

The model is VoxMel 0.6B with its matrices in 8-bit (Q8_0) and its Vocos
vocoder (MIT), as audio.cpp's project converted it. In our tests, English
lines cloned from an English clip were transcribed word for word with a
speaker similarity of 0.85–0.93, and Mandarin lines from a Mandarin clip 0.90.

The add-on itself needs nothing but Python's standard library. It is built
the same way as the VoxCPM and OmniVoice add-ons, which run on audio.cpp too.

## Building

Linux, in the AlmaLinux 8 container, so the archive runs on glibc 2.28 and
newer (needs Docker):

```bash
tools/build_linux.sh                      # audio.cpp, tests, the bundle, the archive
tools/test_archive.sh almalinux:8         # the archive, installed as Subtitld does, on AlmaLinux 8
```

`VOXMEL_TEST_MODELS_DIR` (a folder holding `voxmel/VoxMel-TTS-GGUF/`, or an
empty one to download into) also has the archive speak and cancel for real.
For the engine tests with the model, from source, set `VOXMEL_TEST_MODEL` to
the GGUF file:

```bash
python tools/build_native.py              # Linux: tools/build_native_linux.sh
pip install -e '.[build,dev]'
VOXMEL_TEST_MODEL=/path/to/voxmel-tts-0.6b-q8_0.gguf pytest
pyinstaller pyinstaller.spec --noconfirm
dist/voxmel-addon/voxmel-addon --self-test
```

The release workflow builds, proves and publishes every platform on a tag
`v<version>` matching `manifest.json`; a manual run can build only some
platforms (`targets`). `tools/assemble_licences.py` fails the build when a
native file in the bundle has no notice.

## Haiku

audio.cpp is built on Haiku, so the Haiku archive is built on a Haiku machine and added to a release by hand. Haiku has no PyInstaller: the add-on runs on HaikuPorts' Python 3.14, and the archive carries audio.cpp where a run from source looks for it.

```bash
python3.14 tools/build_native.py
python3.14 tools/build_haiku.py
```

## Licence

The add-on and its preset voices are Apache-2.0. audio.cpp is Apache-2.0,
with ggml, cJSON, libyaml, utf8proc and llama.cpp's tokenizer (MIT) compiled
in. The VoxMel weights are Apache-2.0 (VoxDance Lab), and the Vocos vocoder
MIT. Each archive carries `THIRD_PARTY_LICENSES/` with the licence of
everything frozen into it, and our patch to audio.cpp.

Voice cloning makes speech that sounds like a real person. Clone voices you
have the right to use, and say that a dub is synthetic where it matters.
