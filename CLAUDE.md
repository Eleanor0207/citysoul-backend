# CLAUDE.md

## Agent skills

### Issue tracker

Issues live as GitHub issues on `city-soul-taipei/citysoul-backend` (private repo), managed via the `gh` CLI, and are also mirrored to local-only files under `docs/issues/` (GitHub is authoritative). See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the five default canonical labels as-is: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: `CONTEXT.md` at repo root + `docs/adr/`, plus **the SDD, which lives in [`citysoul-doc`](https://github.com/city-soul-taipei/citysoul-doc)** (see `SDD.md` at this repo root for the pointer), and a spec-document reading-order index at `docs/city_soul_AR_document_index.md`. See `docs/agents/domain.md`.

This repo is backend-only since the v2.1 split; the Unity client lives in `citysoul-client`.
