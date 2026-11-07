# ReproCase source build

Independent project by WERFESTT. The default entry point is `judge.py`, with local verification, replay, desktop, and historical-search modes.

The application uses neutral client names. Public connection hosts are non-working `.invalid` placeholders; offline use needs no connection setup or API key.

The active application derives from commit `c8c7edc0a92a81183f1f8670871892cf9a4120f7`. Its manifest is regenerated for the current files. Computational conditions and numerical conclusions are retained.

`research/2026-11-07/` contains the fixed, previously prepared research package, including its source snapshot, article, results, portable tests, transformation notes, and checksums. The package is kept byte-for-byte as prepared; it is independently verified by `verify_public.py`.

The two pinned dependency wheels remain in `app/historical_vendor/wheels/`. Their hashes are enforced by `requirements-pilot.txt`. The archived research source snapshot omits binary wheels, as recorded in its original inventory.

No credentials are included. [English instructions](README_EN.md) · [Русская инструкция](README_RU.md).
