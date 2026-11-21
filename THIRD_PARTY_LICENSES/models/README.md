# The model

The add-on downloads VoxMel the first time a line is spoken; none of it is in
this archive. The eight preset voices are.

VoxMel 0.6B is by VoxDance Lab, published as
[VoxDance-Lab/VoxMel-0.6B](https://huggingface.co/VoxDance-Lab/VoxMel-0.6B):
its code and weights are under the Apache License 2.0
([Apache-2.0.txt](Apache-2.0.txt)). It renders speech with the
[Vocos](https://github.com/gemelo-ai/vocos) vocoder (24 kHz Mel model), under
the MIT licence ([Vocos-MIT-LICENSE.txt](Vocos-MIT-LICENSE.txt)).

The add-on uses both as converted to one GGUF file, with 8-bit (Q8_0)
matrices (the vocoder in float32), by the audio.cpp project, published in
[audio-cpp/audio.cpp-gguf](https://huggingface.co/audio-cpp/audio.cpp-gguf)
under the same licences. It is fetched at the revision `engine.MODEL_REVISION`
names (in the add-on's source, `voxmel_addon/engine.py`) and checked against
its pinned sha256.

| File | What it is | When it downloads |
|------|------------|-------------------|
| `VoxMel-TTS-GGUF/voxmel-tts-0.6b-q8_0.gguf` (1.04 GB) | VoxMel 0.6B with its text tokenizer and the Vocos vocoder | The first line |

## The preset voices

`bin/_internal/voxmel_addon/presets/` holds eight short clips, each a voice
VoxMel itself made from a fixed sentence and seed, with no reference
recording (the add-on's `tools/make_presets.py`). They are part of the add-on
and under its licence, Apache-2.0; no person's voice was recorded for them.
