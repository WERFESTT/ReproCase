# ReproCase — OFFLINE_REVIEW: review without an API key

This is an experimental offline review build of ReproCase. Its own code is MIT licensed, Copyright (c) 2026 WERFESTT; third-party components retain their licenses. See LICENSE_SCOPE.md and THIRD_PARTY.txt. It is not a completed contest submission.

## Quick review

Clone or download the entire repository into a new directory. Keep the directory structure and all manifest-listed files. Run the verified entry point below; it checks hashes before loading application modules. Do not merge an archive over an existing installation.

From the extracted directory:

```bash
python3 -I -B judge.py demo
```

This replays two selected saved witnesses: one from the earlier successful real response and one from the separate offline repair of a later series. Each test must fail its assertion on before and pass on after, with zero harness errors. The combined command exits 0 when all four expected outcomes match. It writes review-output/demo.json without overwriting the supplied evidence. No new model request is sent.

Open index.html locally for the evidence overview. No server or internet connection is needed. Saved reports are explicitly labelled DERIVED_REDACTED_COPY: original project-root path strings have been replaced with SOURCE_PROJECT, and a wrapper records the original hash and transformation. Commands inside those historical records are data, not runnable instructions. Pinned replay sources and witness.json files are unchanged.

## Desktop window

```bash
python3 -I -B judge.py gui
```

This is the existing desktop UI: three educational cases, local checks, input search, theme selection, copying and request preview. Its title identifies OFFLINE_REVIEW/OFFLINE. The three network buttons are disabled; no key is requested. In EN mode, click “Check both versions” for the educational calculation, or “Test preparation preview” to inspect the public context without sending it. The saved real response is viewed separately through index.html; the window does not load it as a new session. Current educational values are not historical model results.

The commands also work from another working directory: supply the full path to judge.py in the NEW location. The original project location is unnecessary. The GUI requires Tkinter and a graphical session; no OS configuration is changed. Without Tk, use demo and HTML. The gui --hidden-check option tests widgets, not visual appearance. The application supports RU/EN. Select Language / Язык beside Theme, or pass --language en to the command above. Switching updates the display without another calculation or API request; recorded raw JSON retains its original language.

## Dependencies

| Mode | Requirements |
| --- | --- |
| Read HTML/JSON | Browser or text editor; no Python needed |
| demo, verify, pinned replay | CPython 3.14 and its standard library; no Hypothesis, Tk, key or network |
| gui | Above plus installed Tkinter and a graphical session; educational checks do not need Hypothesis |
| pilot, synthetic | CPython plus Hypothesis 6.170.9 and sortedcontainers 2.4.0 in a local venv; no display required |
| Optional live test preparation | Same pinned dependencies plus Tk, a display and separately authorized Nebius access; provider charges may apply |

Tested platform: CPython 3.14.4, Linux x86_64. The included Hypothesis wheel targets CPython 3.14/Linux x86_64. Python and system Tk are not bundled; this is not a standalone binary or a cross-platform guarantee.

For the full existing historical comparison, from the kit root:

```bash
python3 -m venv .venv
.venv/bin/python -I -m pip --isolated --disable-pip-version-check install --no-index --no-deps --no-cache-dir --require-hashes -r requirements-pilot.txt
.venv/bin/python -I -B judge.py pilot
```

Pinned wheels are already in app/historical_vendor/wheels. Installation uses no package index and changes no global environment. The pilot compares boundary search and Hypothesis on two known historical defects, without a model. `.venv/bin/python -I -B judge.py synthetic` runs the existing route with explicitly artificial responses; it tests the software, not NVIDIA quality. These modes write new reports under app/outputs.

## Optional live client

The default `judge.py gui` remains offline. The complete repository also contains the existing live-capable desktop application. **BYOK (bring your own key) does not mean free inference:** a judge is not assumed to have a free key. Free live access for judges through the required review period, and whether offline replay plus optional BYOK meets the contest requirements, remain unresolved with the organizers. Do not publish or borrow an owner's main key. No server, tunnel or new subscription is required by this launch path.

Use a fresh complete copy on the tested Linux x86_64 / CPython 3.14.4 platform with Tk and a display. From its repository root, verify the allowlist and install only the bundled, pinned wheels into a project-local environment (skip creation/installation if this exact environment is already prepared):

```bash
python3 -I -B judge.py verify --language en
python3 -m venv .venv
.venv/bin/python -I -m pip --isolated --disable-pip-version-check install --no-index --no-deps --no-cache-dir --require-hashes -r requirements-pilot.txt
.venv/bin/python -I -B judge.py verify --language en
.venv/bin/python -E -s -B app/desktop.py --language en
```

Opening the window sends no request. Use an authorized account only after the following explicit steps:

1. Select **Correlation — one request** in the test-preparation selector.
2. Click **Test preparation preview**. Inspect the public symptom, API contract, generator description, input domains and property schema. Preview needs no key and sends nothing. The patch, historical source code, prepared counterexamples, reference answers and local search results are not sent.
3. If you have separately authorized test access and accept potential charges, click **Prepare a test with a model…**, read the destination/data/limits, confirm **one request**, then enter the key in the masked field. Cancel to send nothing before dispatch. Never put the key in a command, environment variable, file, screenshot or report; the application uses it only in memory.
4. Inspect the accepted/rejected property, local search and before/after export result. A new report is saved under `app/outputs/property-live-…/`; saved historical evidence is not replaced. Copy the printed replay commands to a separate terminal: they repeat the exported test locally without another model call. An open GUI occupies its terminal until closed.

This one-request route uses the existing `nvidia/nemotron-3-super-120b-a12b` client at `https://api.tokenfactory.us-central1.nebius.com/v1/chat/completions`: at most **1 request**, **1024 completion tokens** including reasoning, **16000 serialized request bytes**, **30 seconds** client timeout, **no automatic retry or answer repair**. Other existing modes have separate confirmations; selecting a six-position series is not part of this route. Unknown usage stays unknown. Cancellation or timeout does not prove provider-side cancellation or zero charges. These application limits are not a credential-wide monetary cap, and model/endpoint availability is not guaranteed by the saved results.

`-E -s` ignores Python environment configuration and user-site packages while retaining `app/` for sibling imports. Do not add `-I` to direct `app/desktop.py`: this existing route requires its script directory. For another working directory, first verify using the new full path to `judge.py`, then use the new full paths to `.venv/bin/python` and `app/desktop.py`. No original development folder is required. The direct route trusts the verified copy and local Python environment; it does not inherit judge.py's checked loader or network block. Do not change sources between verification and launch. See [Security limits](SECURITY.md).

## Evidence and limits

- Earlier successful single request: 1,551 input and 195 output tokens; two exported files/four executions; 767 calculation executions for both the model property and manual control. This kit selects its Hypothesis test.
- Separate partial series: two requests out of six scheduled. The first valid narrow-domain property found no counterexample. The second exposed a regression but hit a local exporter/runtime version mismatch. Four positions were not run; the original series remains 0/6 confirmed exports. Usage: 3,102 input and 513 output tokens. Failures remain in the record.
- After repairing the harness, the second saved proposal was replayed offline. The selected boundary-search test confirms that repair. This is not a new model response or a successful completion of the original series.

Recorded cost estimates are not billing confirmations or monetary caps. The property vocabulary, semantic acceptance and historical case selection were prepared manually. Arbitrary-project support, autonomy, superiority over Hypothesis, developer-time savings and statistical reliability have not been demonstrated. No counterexample within budget does not establish correctness.

## Contents and integrity

MANIFEST.json lists every included file, size, hash and selected-copy provenance. app contains an explicit allowlist of application sources, plans and examples; historical_vendor contains pinned upstream sources/licenses and two wheels; evidence contains two replay kits and three derived records. The remaining files are the offline entry point, overview, RU/EN guides and notices. Full outputs, venvs, keys, user theme preferences and intermediate logs are excluded.

Run `python3 -I -B judge.py verify` to check hashes without calculations. The offline judge.py entry point validates files before imports, loads only pinned application modules and disables network actions in that route. -I is mandatory for judge.py; cwd/PYTHONPATH cannot choose application code there. The optional direct application launch above uses a separate preflight verification, not the same checked loader. This is not a general sandbox for untrusted Python. Matching hashes do not prove authorship or prevent coordinated replacement of the manifest, loader and contents.

The repository is an experimental source release, not a claim of general model usefulness. The [published 1:02 video](https://youtu.be/WmZ8xFPvyf8) uses English UI/titles without audio and clearly distinguishes the saved real response from offline replay. No contest submission is claimed. The owner separately controls any future model requests or submission.

## RU / EN

Use **Language / Язык: RU / EN** next to Theme. English demo window: `python3 -I -B judge.py gui --language en`. Offline replay: `python3 -I -B judge.py demo --language en`. Switching preserves state and sends no request. [English guide](LOCALIZATION_EN.md) · [Русская инструкция](LOCALIZATION_RU.md).
