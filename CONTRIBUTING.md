# Contributing

Nabu's public Python API is experimental. Keep agent evidence and access checks in
code; model output must never bypass those rules. New providers implement
`JsonProvider.generate` and must enforce their own timeout and retry behavior.

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests
python examples/library_quickstart.py
python -m build
python -m twine check dist/*
```

For behavior changes, add tests around meaningful failure modes, tenant/scope
boundaries and pause/resume compatibility. Offline tests must not require API keys
or send model requests. Never commit credentials or private tracker records.

Open an issue before introducing another framework or mandatory runtime dependency.
Contributions are submitted under the repository's MIT licence; only contribute
code and data you have permission to share under the applicable terms.
