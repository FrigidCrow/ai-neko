# Independently installed local voice runtime

ai-neko's worker.py is original project code. The main frozen application ships
this source, its dependency lock, and source notices; it does **not** contain the
following native voice libraries or model weights. Explicit local voice setup
installs them into the user's application-owned assets/voice directory.

- sherpa-onnx 1.13.8: Apache-2.0, https://github.com/k2-fsa/sherpa-onnx/tree/v1.13.8 .
  Its native TTS distribution includes eSpeak NG (GPL-3.0), ONNX Runtime (MIT),
  and other upstream components; consult the wheel's licenses and upstream
  sources. This entire runtime must not be represented as Apache-only.
  https://github.com/espeak-ng/espeak-ng
- NumPy 2.4.4: BSD-3-Clause and bundled-library notices in the installed wheel.
  https://github.com/numpy/numpy/tree/v2.4.4
- SenseVoice Small model: FunASR Model License v1.1 (not Apache-2.0 for weights).
  https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE
- Kokoro v1.1 model: Apache-2.0.
  https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh
- uv 0.11.8: MIT OR Apache-2.0. Installer downloads the official pinned binary;
  original MIT and Apache license texts are included in licenses/.
  https://github.com/astral-sh/uv/tree/0.11.8
- CPython 3.11.15: Python Software Foundation License; uv's pinned release
  contains a checksummed catalog for the managed python-build-standalone
  distribution, including additional licenses retained in its install.
  https://github.com/astral-sh/python-build-standalone

This source file records provenance rather than granting a replacement license.
Models and wheels are fetched from the original publishers, never borrowed from
another application's environment, data, credentials or model directories.

Full upstream texts included here: licenses/SENSEVOICE-MODEL-LICENSE.txt,
licenses/KOKORO-APACHE-2.0.txt, licenses/SHERPA-APACHE-2.0.txt,
licenses/ESPEAK-GPL-3.0.txt, licenses/NUMPY-LICENSE.txt,
licenses/UV-LICENSE-APACHE.txt, and licenses/UV-LICENSE-MIT.txt.
Original wheel notices remain in each private site-packages *.dist-info directory.
The NumPy text is from the macOS arm64 2.4.4 wheel; other platforms' bundled-library
notices are preserved in their independently downloaded wheels.
