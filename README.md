# ReproCase

**Turn a numerical failure into a locally checked regression test.**

An independent project by **WERFESTT**. ReproCase combines a restricted test-property format, local counterexample search, and portable tests checked against versions before and after a fix. It includes a desktop interface in Russian and English, educational examples, and reproducible research on two pinned historical regressions.

## Run locally

```bash
python3 -I -B judge.py verify
python3 -I -B judge.py demo
python3 -I -B judge.py gui
```

The default entry point verifies the application files and blocks network connections. Replay needs no API key. The desktop window additionally needs Tkinter and a graphical session.

- [Русская инструкция](README_RU.md)
- [English instructions](README_EN.md)
- [Results and saved evidence](index.html)
- [Research: 7 November 2026](research/README.md)
- [Recorded demonstration](https://youtu.be/WmZ8xFPvyf8)

## Research results

The research package contains six recorded model responses, twelve portable tests from those responses, and a separate local experiment with 120 searches and 36 exported tests. Two deliberately constructed outputs expose permissive assertions. The model-generated assertions did not outperform the corresponding manual controls on the studied cases.

```bash
python3 -B research/2026-11-07/verify_public.py --replay
```

This checks the research package and replays 100 recorded before/after outcomes: 24 from the six-response series, 72 from the local experiment, and four earlier examples. These are replay checks, not 100 new independent experiments. The earlier incomplete series is preserved with its original outcome and is separate from the later completed research.

## Scope

The property vocabulary, semantic validation, and case selection are manually designed. Model output is validated as data; arbitrary generated Python is not executed. The current evidence covers two historical defects and does not establish general model superiority or support for arbitrary repositories.

The optional client source is in `app/model_client.py`. Public connection addresses are non-working placeholders; a new model experiment requires explicit connection configuration. Offline use is complete without that configuration.

Own code: MIT, Copyright (c) 2026 WERFESTT. [License scope](LICENSE_SCOPE.md), [component notices](THIRD_PARTY.txt), and [security information](SECURITY.md).
