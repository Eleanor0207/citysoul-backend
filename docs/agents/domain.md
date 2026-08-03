# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the canonical product glossary. Also mirrored at `docs/CONTEXT.md` (identical copy); treat the two as one source.
- **`docs/city_soul_AR_document_index.md`** — reading-order map and "supersedes" table across all the spec documents in `docs/`. Consult it before trusting any individual spec file's content, since later documents override earlier ones.
- **`docs/SDD.md`** — the consolidated System Design Document, already merged from all current-authority spec files per the index's override table. Prefer this over re-deriving from the individual spec files.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/
│   ├── SDD.md                              ← consolidated system design doc
│   ├── city_soul_AR_document_index.md      ← reading order / override map
│   ├── city_soul_AR_*.md                   ← individual spec documents (decision-chain snapshots)
│   └── adr/
│       └── 0001-gcp-vertex-ai-for-mvp-dialogue.md
└── app/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md` (e.g. 城市靈魂, 共鳴值, 相遇憑證, 感應範圍/召喚範圍, 可驗證微任務). Don't drift to synonyms the glossary explicitly avoids — each glossary entry lists an `_Avoid_` line for this reason.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR / spec conflicts

If your output contradicts an existing ADR or a "current authority" spec document, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0001 (GCP Vertex AI for MVP dialogue) — but worth reopening because…_

When a spec conflict is between two individual `city_soul_AR_*.md` files, defer to `city_soul_AR_document_index.md`'s override table (later document wins) and to `docs/SDD.md` as the merged result.
