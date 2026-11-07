# ReproCase security information

The default `judge.py` entry point checks the application manifest before importing modules, loads pinned application sources, and blocks network connections. Model output is accepted only as a restricted data declaration. It cannot choose arbitrary Python code, shell commands, imports, or destinations.

The network workflow is enabled explicitly with `judge.py gui --allow-network`. This mode still verifies pinned sources. The user selects the endpoint, model, authentication mode and output-token parameter through the connection dialog; only these nonsecret settings are saved in a Git-ignored file. Every run requires confirmation showing its destination and limits. A key, if needed, is entered privately and kept in memory only. The application does not load keys from files or environment variables.

Remote connections require HTTPS with normal certificate and hostname verification; HTTP is limited to loopback addresses and `localhost`. URLs containing credentials, query parameters or fragments, and `.invalid` placeholders, are rejected. There is no vendor-specific host or model list. Each run receives an immutable connection-settings object; model responses cannot change it. Redirects and automatic retries are disabled. Remote HTTPS uses the standard Python proxy and certificate-trust configuration; loopback requests stay local. No certificate-verification bypass is provided.

The server must support the documented function-calling response contract. Model identifiers, function names, response count, size, usage fields and proposed actions are checked locally. No compatibility probe or model call occurs when saving settings. Actual provider/model compatibility is separate from the local mock-server checks.

The single-property request code allows one attempt, at most 1024 completion tokens, at most 16000 serialized request bytes, and a 30-second timeout. These limits do not impose an account-wide spending cap. A timeout does not establish service-side cancellation or zero usage.

Saved evidence and the fixed research package retain separate provenance. Earlier incomplete runs are not reclassified as completed experiments. The application manifest and research checksums detect changes relative to their recorded contents; they do not authenticate authorship or defend against coordinated replacement of all checks and data. The Python environment and installed dependencies remain trusted components. This is not a general sandbox for hostile code.

Keep credentials out of source files, reports, recordings, and issue discussions. [Source and license scope](LICENSE_SCOPE.md).
