# Changelog

## 0.2.0a1 — 2026-10-15

- Introduce the `nabu-delivery` distribution and MIT licence.
- Add `Reviewer`, `Viewer`, `WorkItem`, typed reports and JSON/Markdown export.
- Add atomic, validated imports with tenant-collision and reference checks.
- Expose saved historical runs, pause/resume, scoped report retrieval and evaluation.
- Add a provider protocol, optional Anthropic provider, schema validation and bounded recovery.
- Normalize timestamps for library imports and support explicit timezone-aware cutoffs.
- Ship the `nabu` CLI, `py.typed`, adapter mappings, source documentation and examples.
- Add installed-package checks and distribution builds to CI.

This is an experimental library release. It has not been published to PyPI.
The API may change before 1.0. Source history is not fully versioned, and viewer
contexts require authentication by the embedding application.
