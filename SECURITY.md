# ReproCase security information

The default `judge.py` entry point checks the application manifest before importing modules, loads pinned application sources, and blocks network connections. Model output is accepted only as a restricted data declaration. It cannot choose arbitrary Python code, shell commands, imports, or destinations.

The client source uses neutral module names and non-working public connection placeholders. Configuring a real connection is a separate development action. The existing client applies a host allowlist, normal certificate and hostname verification, explicit request confirmation, a masked memory-only key, and no automatic retry. It does not load keys from files or environment variables.

The single-property request code allows one attempt, at most 1024 completion tokens, at most 16000 serialized request bytes, and a 30-second timeout. These limits do not impose an account-wide spending cap. A timeout does not establish service-side cancellation or zero usage.

Saved evidence and the fixed research package retain separate provenance. Earlier incomplete runs are not reclassified as completed experiments. The application manifest and research checksums detect changes relative to their recorded contents; they do not authenticate authorship or defend against coordinated replacement of all checks and data. The Python environment and installed dependencies remain trusted components. This is not a general sandbox for hostile code.

Keep credentials out of source files, reports, recordings, and issue discussions. [Source and license scope](LICENSE_SCOPE.md).
