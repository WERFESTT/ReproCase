# ReproCase

**From a numerical bug symptom to a locally checked regression test.**

Experimental, limited prototype. The default review route is offline and needs no API key. It supports two pinned historical regressions plus clearly labelled educational calculations. Model proposals use a manually designed declarative property vocabulary; arbitrary generated Python is not executed.

```bash
python3 -I -B judge.py demo
python3 -I -B judge.py gui
```

Run these from the repository root, or provide the full path to judge.py from another working directory. Replay requires CPython 3.14; GUI additionally requires Tkinter and a graphical session. The full historical comparison requires the pinned local dependencies. Tested on Linux x86_64 / CPython 3.14.4 only.

- [English setup and review instructions](README_EN.md)
- [Optional live client: preview, consent and memory-only key](README_EN.md#optional-live-client)
- [Published demonstration (1:02)](https://youtu.be/WmZ8xFPvyf8): English UI and titles, no audio; saved real response and offline replay, not a fresh API call
- [Русская инструкция](README_RU.md)
- [Saved evidence overview](index.html)
- [License scope](LICENSE_SCOPE.md) and [third-party sources](THIRD_PARTY.txt)
- [Security review and limitations](SECURITY.md)

The real single-request evidence, later incomplete series and offline repair are separate. A saved real model response is not a live API demonstration. There is no demonstrated AI advantage, measured time saving, blind benchmark or arbitrary-project support. The vocabulary, semantic validation and historical case selection were prepared manually. The incomplete six-position series retains its first unhelpful proposal, local export failure and four unrun positions.

Own code: MIT, Copyright (c) 2026 WERFESTT. Upstream code retains its original terms. The video is linked above; no contest submission is claimed. Free offline review needs no key. Optional live inference requires separately authorized Nebius access and may incur charges. Free live access for judges and acceptance of offline replay plus BYOK have not been confirmed with the organizers.

## RU / EN

Use **Language / Язык: RU / EN** next to Theme. English demo window: `python3 -I -B judge.py gui --language en`. Offline replay: `python3 -I -B judge.py demo --language en`. Switching preserves state and sends no request. [English guide](LOCALIZATION_EN.md) · [Русская инструкция](LOCALIZATION_RU.md).
