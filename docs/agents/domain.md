# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the canonical product glossary. Also mirrored at `docs/CONTEXT.md` (identical copy); treat the two as one source.
- **`docs/city_soul_AR_document_index.md`** — reading-order map and "supersedes" table across all the spec documents in `docs/`. Consult it before trusting any individual spec file's content, since later documents override earlier ones.
- **`SDD_v2.2_Unity_3D.md` — in the [`citysoul-doc`](https://github.com/city-soul-taipei/citysoul-doc) repo, not here.** **The single authority.** Anything conflicting with it loses, including `docs/SDD.md` (v1), which is superseded: v1 describes a Flutter + 2.5D Billboard client that no longer exists. v2.1's §1 carries the full overturn table; read it before trusting any older spec. `SDD.md` at this repo root is only a pointer.
- **`docs/SDD.md`** — v1, **superseded**. Kept only for historical context on decisions that v2.1 explicitly says to carry over unchanged (business rules, API contracts, DB schema).
- **`docs/adr/`** — read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront.

## File structure

Single-context repo:

```text
citysoul-doc/                               ← the authority, separate repo
└── SDD_v2.2_Unity_3D.md                    ← single source of truth

citysoul-backend/                           ← this repo
├── CONTEXT.md                              product glossary (complements the SDD)
├── SDD.md                                  pointer to citysoul-doc, not the spec itself
├── contracts/openapi.json                  API contract, generated from this repo's code
├── app/  tests/  scripts/
└── docs/                                   local-only except agents/
    ├── SDD.md                              v1, SUPERSEDED — kept for decision history
    ├── city_soul_AR_document_index.md      reading order / override map (pre-v2.1)
    ├── city_soul_AR_*.md                   individual spec documents (snapshots)
    ├── issues/                             local mirrors of GitHub issues
    ├── agents/                             this config (tracked)
    └── adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md` (e.g. 城市靈魂, 共鳴值, 相遇憑證, 感應範圍/召喚範圍, 可驗證微任務). Don't drift to synonyms the glossary explicitly avoids — each glossary entry lists an `_Avoid_` line for this reason.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR / spec conflicts

If your output contradicts an existing ADR or a "current authority" spec document, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0001 (GCP Vertex AI for MVP dialogue) — but worth reopening because…_

When a spec conflict is between two individual `city_soul_AR_*.md` files, defer to `city_soul_AR_document_index.md`'s override table (later document wins). But the SDD in `citysoul-doc` outranks all of them and `docs/SDD.md` alike — check it first, since the client stack and visual layer were fully replaced there.
