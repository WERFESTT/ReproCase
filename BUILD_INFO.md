# ReproCase source build

Independent project by WERFESTT. The default entry point is `judge.py`, with local verification, replay, desktop, and historical-search modes.

The application uses neutral client names and user-configured connections. `judge.py gui --allow-network` enables the confirmed model workflow with a user-selected endpoint and model. Settings contain no key and do not send a probe. Default offline use needs no connection setup or API key.

The active application derives from commit `c8c7edc0a92a81183f1f8670871892cf9a4120f7`. Its manifest is regenerated for the current files. Computational conditions and numerical conclusions are retained.

`research/2026-11-07/` contains the fixed, previously prepared research package, including its source snapshot, article, results, portable tests, transformation notes, and checksums. The package is kept byte-for-byte as prepared; it is independently verified by `verify_public.py`.

The two pinned dependency wheels remain in `app/historical_vendor/wheels/`. Their hashes are enforced by `requirements-pilot.txt`. The archived research source snapshot omits binary wheels, as recorded in its original inventory.

No credentials are included. [English instructions](README_EN.md) · [Русская инструкция](README_RU.md).

## Connection update validation

The configurable client is covered by 28 checks on Python 3.12.14: actual loopback HTTP with a mock server, authentication and token parameters, strict responses, consent/cancellation in the desktop controller, series limits, a new portable export replay, and the default offline audit hook. The original four demonstration outcomes also remain reproducible. The research archive and earlier evidence are unchanged.

No real model or paid service was called. Remote HTTPS settings are checked without contacting a provider. This environment has no graphical display; controller behavior is checked without making a rendered-interface claim. Full new historical searches still require the pinned dependency environment described in the instructions.
