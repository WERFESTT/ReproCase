# Source review build: offline by default

This is an experimental, limited source build, not a final contest product.
It derives from an independently checked local allowlist; no pre-existing project Git history is included in the file export.
Default: judge.py verify/demo/gui. This route rejects network connections and disables the three live GUI buttons.
Optional: the existing app/desktop.py live-capable application, launched only after the documented preflight verification and local dependency setup. See README_EN.md / README_RU.md. Opening it sends nothing; sending requires separate consent and masked memory-only key entry.
This derivative renames API client modules and replaces connection hosts with non-working .invalid values. Replay integrity metadata was regenerated. The historical implementations and test conditions are unchanged.
No credentials or video files are bundled. The published 1:02 demonstration is https://youtu.be/WmZ8xFPvyf8 and shows saved evidence/offline replay, not a fresh API call.
Free offline review is available. Free live access for judges and contest acceptance of offline replay plus BYOK remain unresolved with the organizers. No contest submission is made by this build.
