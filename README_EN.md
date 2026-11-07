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

Start the verified desktop interface with network operations explicitly enabled:

```bash
python3 -I -B judge.py gui --allow-network
```

1. Open **Model connection…**.
2. Enter the full endpoint URL, including its path, and the exact model identifier. For example, `http://127.0.0.1:8000/v1/chat/completions` addresses a local server you have already started. ReproCase does not install or start a model server.
3. Select **No key** or **Access key (Bearer)** as required by the server.
4. Select its supported output-limit parameter: `max_tokens` or `max_completion_tokens`. Save.
5. Choose an operation and review its destination, model, transmitted data and request limits. Confirm that run. If authentication was selected, enter the key in the masked prompt; the key stays in memory for that run only.

Remote servers require HTTPS with certificate and hostname verification. HTTP is accepted only for `localhost` or a loopback IP address such as `127.0.0.1` or `[::1]`. URLs containing credentials, query parameters or fragments are rejected, as are `.invalid` placeholders. Redirects are never followed.

Nonsecret settings are saved to the Git-ignored `app/connection_settings.json`: endpoint, model, authentication mode and token-limit parameter. The key is not stored there. `app/connection_settings.example.json` shows the format and is not loaded automatically. You can create the working settings file manually before opening the application. Missing settings cause a local configuration message, not a request. Saving settings and previewing requests perform no network operations.

### Compatibility and limits

The server must support non-streaming `chat/completions` requests with `messages`, `tools`, a forced function `tool_choice` and `parallel_tool_calls=false`. A successful response is HTTP 200 JSON with the exact requested `model`, one `choices` item with `index=0`, `finish_reason="tool_calls"`, and exactly one call to the expected function. Function arguments must be a JSON string. Additional response text and refusals are rejected, and the proposed action then passes independent local validation. Plain text chat and other API protocols are not automatically converted. If the server returns its internal model name instead of a requested alias, use its supported exact identifier.

| Operation | Maximum requests |
| --- | --- |
| One educational search | 4, with up to 3 new probes |
| Five-case comparison | 20 total, up to 4 per case |
| Prepare one historical property | 1 |
| Two properties with three repetitions each | 6 total, 1 per session |

These modes retain limits of 1024 completion tokens, 16000 serialized request bytes, 65536 response bytes and a 30-second network-operation timeout. There are no automatic retries. An error or cancellation stops subsequent requests in a series. No tariff is assigned to a user-selected server: token usage is reported when known, while monetary cost remains unknown. The former model's prices are not reused. Timeouts do not establish service-side cancellation or zero usage, and application limits are not an account-wide spending cap.

**New historical test preparation** requires the pinned dependencies described above. Start the network-enabled window with that interpreter, for example `.venv/bin/python -I -B judge.py gui --allow-network`. Local readiness is checked before consent and key entry. The verified launcher does not silently switch interpreters.

Without `--allow-network`, the launcher still blocks networking. Both modes verify the manifest. Saved research results and portable bundles retain their own pinned sources and need no new connection.

Run the client integration checks against a local mock server, without a real model or payment:

```bash
python3 -B -m unittest discover -s tests -v
```

These checks cover configuration propagation, response validation, consent, cancellation, limits and local replay. Compatibility with a particular model server requires a separate confirmed run after configuring that server.

## Interpretation and integrity

The experiments cover two historical defects. They do not establish general model advantage, developer-time savings, statistical reliability across arbitrary projects, or autonomous repair. No counterexample within a search budget does not prove correctness.

`MANIFEST.json` pins the active application and its documentation. Dependency installation verifies the wheel hashes in `requirements-pilot.txt`; the fixed research package has its own `SHA256SUMS.json`. The default entry point blocks network operations and loads pinned application modules. These checks detect changes relative to the manifest; they do not authenticate authorship or provide a general sandbox for hostile code.

The [recorded demonstration](https://youtu.be/WmZ8xFPvyf8) shows saved evidence and local replay. [Security information](SECURITY.md) · [License scope](LICENSE_SCOPE.md).
