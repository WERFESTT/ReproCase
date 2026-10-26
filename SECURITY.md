# Security review and limits

The selected publication bytes were checked for common credential patterns, personal paths, unexpected files, Git history, symlinks and archive traversal. No credential or owner-specific path candidates were found. The unmodified official Hypothesis wheel contains three public CI runner build paths in its SBOM; these are upstream build metadata, not local user paths or runtime dependencies. The review included the text inside the two pinned wheels, preserved third-party notices, and checked file hashes. This is a bounded review, not a guarantee that no defect or secret can ever exist.

The documented entry point checks the manifest before loading application modules. Saved replay tests use pinned sources and isolated Python. Model JSON is validated as a restricted declaration; it does not select arbitrary code, shell commands, destinations or imports. The fixed templates compile/import reviewed sources, not model-supplied Python.

The offline review route disables model actions and rejects network connections. The underlying opt-in client uses a fixed HTTPS host allowlist, normal certificate/hostname verification, explicit user confirmation and a hidden memory-only key. It does not follow redirects or automatically retry failures. No API key is loaded from the environment or a file by the application.

Hashes detect changes relative to a trusted loader/manifest. They do not authenticate authorship or protect against coordinated replacement of the loader, manifest and sources. This is not a general sandbox for hostile Python or a comprehensive concurrent-file-tampering defense. Python, Tk and installed dependencies remain part of the trusted environment. Do not put credentials in issues or saved examples.

The existing saved real response, incomplete series and separate offline repair retain their distinct provenance and outcomes. No failed harness run is counted as a numerical regression.
