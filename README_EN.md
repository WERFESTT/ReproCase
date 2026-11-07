# ReproCase: installation and use

ReproCase is an independent project by WERFESTT for preparing, searching for, and replaying numerical regression tests. The default route runs locally without an API key.

## Local verification and replay

Clone or download the complete repository. From its root:

```bash
python3 -I -B judge.py verify
python3 -I -B judge.py demo --language en
```

Verification checks the application manifest before importing modules. The demo replays two earlier saved witnesses against both historical versions: an assertion failure before the fix and a passing test after it, with zero harness errors. The command succeeds when all four expected outcomes match. Its output is written to `review-output/demo.json`.

Open [index.html](index.html) to inspect the recorded results. The demo is a replay of saved evidence; it sends no new model request. Recorded source paths and service identifiers were sanitized in the published derivative. Numerical inputs, test conditions, and recorded outcomes retain their meaning. Historical commands inside JSON records are data, not installation instructions.

## Desktop interface

```bash
python3 -I -B judge.py gui --language en
```

The window provides three educational cases, local checks, input search, request preview, theme selection, and RU/EN language switching. Network buttons are disabled in this entry point. The window requires Tkinter and a graphical session; replay and HTML inspection do not.

Use the Language selector beside Theme to switch languages. Switching preserves the current state without another calculation or model request. Raw historical records keep their original language. [Language guide](LOCALIZATION_EN.md).

## Research package

The [research overview](research/README.md) links the article, protocol, records, and exported tests from 7 November 2026.

```bash
python3 -B research/2026-11-07/verify_public.py --replay
python3 -B research/2026-11-07/test_research.py
```

The first command verifies checksums and 100 expected replay outcomes. The second runs nine existing checks of the experiment setup and reporting logic. Neither command generates a new model response. The research package contains its own fixed source snapshot and integrity manifests, independently of subsequent changes to the application.

The older successful single response, incomplete series, and offline repair remain in `evidence/`. Their original status is preserved. The later completed six-response study is stored separately under `research/2026-11-07/`.

## Dependencies

| Operation | Requirements |
| --- | --- |
| Read HTML, article, or JSON | Browser, document viewer, or text editor |
| Application and research replay | Python and the standard library; no key or network |
| Desktop interface | Python, Tkinter, and a graphical session |
| Repeat full historical search | The pinned dependencies and a compatible Python environment |

The original model-response experiments used Python 3.14.4 and Hypothesis 6.170.9 on Linux x86_64. The portable research replay was also checked with Python 3.12.3. The bundled dependency wheels target Python 3.14/Linux x86_64 and must not be installed into a different interpreter.

For the full historical pilot on that compatible platform:

```bash
python3 -m venv .venv
.venv/bin/python -I -m pip --isolated --disable-pip-version-check install --no-index --no-deps --no-cache-dir --require-hashes -r requirements-pilot.txt
.venv/bin/python -I -B judge.py pilot
```

The installation uses the pinned wheels in `app/historical_vendor/wheels/` and no package index. The pilot compares search methods on two historical defects without a model. The existing `synthetic` mode tests the software using explicitly artificial responses. New local reports go to `app/outputs/`.

## Model connection

The client source is `app/model_client.py`. The published copy uses non-working `.invalid` hostnames. It is not a configured live service: entering a key does not make those addresses usable. A new experiment requires explicit configuration of an authorized endpoint and compatible model, followed by validation of that configuration.

The existing client code limits a single-property request to one attempt, at most 1024 completion tokens, at most 16000 serialized request bytes, and a 30-second timeout. It uses a host allowlist, certificate checks, a memory-only key, and no automatic retries. Service-side usage after a timeout can remain unknown. The application limits are not an account-wide spending limit. No credentials are required for the documented offline commands.

## Interpretation and integrity

The experiments cover two historical defects. They do not establish general model advantage, developer-time savings, statistical reliability across arbitrary projects, or autonomous repair. No counterexample within a search budget does not prove correctness.

`MANIFEST.json` pins the active application and its documentation. Dependency installation verifies the wheel hashes in `requirements-pilot.txt`; the fixed research package has its own `SHA256SUMS.json`. The default entry point blocks network operations and loads pinned application modules. These checks detect changes relative to the manifest; they do not authenticate authorship or provide a general sandbox for hostile code.

The [recorded demonstration](https://youtu.be/WmZ8xFPvyf8) shows saved evidence and local replay. [Security information](SECURITY.md) · [License scope](LICENSE_SCOPE.md).
