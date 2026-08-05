# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the canonical product glossary. Also mirrored at `docs/CONTEXT.md` (identical copy); treat the two as one source.
- **`docs/city_soul_AR_document_index.md`** — reading-order map and "supersedes" table across all the spec documents in `docs/`. Consult it before trusting any individual spec file's content, since later documents override earlier ones.
- **`SDD_v2.1_Unity_3D.md`** at the repo root — **the single authority.** Anything conflicting with it loses, including `docs/SDD.md` (v1), which is superseded: v1 describes a Flutter + 2.5D Billboard client that no longer exists. v2.1's §1 carries the full overturn table; read it before trusting any older spec.
- **`docs/SDD.md`** — v1, **superseded**. Kept only for historical context on decisions that v2.1 explicitly says to carry over unchanged (business rules, API contracts, DB schema).
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

When a spec conflict is between two individual `city_soul_AR_*.md` files, defer to `city_soul_AR_document_index.md`'s override table (later document wins). But `SDD_v2.1_Unity_3D.md` outranks all of them and `docs/SDD.md` alike — check it first, since the client stack and visual layer were fully replaced there.
