# Evidence and freshness policy

## Evidence classes

Use exactly these classes:

- `confirmed_code` — directly observed in official source code at a recorded commit and code path.
- `confirmed_official` — explicitly stated in an official system card, transparency document, engineering publication, help/support document, or product documentation.
- `official_guidance` — official creator/product guidance or an official empirical observation; useful, but not proof of an implementation detail.
- `inference` — a bounded conclusion derived from multiple primary claims or a primary claim plus observed data. Show the reasoning.
- `hypothesis` — a falsifiable explanation to test against data.
- `unknown` — public evidence is absent or does not resolve the question.
- `stale` — evidence is historical and current validity has not been reverified.

`confidence` describes support for this use of the claim; it does not replace `evidence_class`. Official guidance can be high-confidence guidance and still not be code confirmation.

## Source priority

1. Official published source code.
2. Official system card or transparency material.
3. Official engineering blog or technical paper.
4. Official help, support, or creator documentation.
5. Official newsroom or product blog.
6. First-party statement from an official account or responsible leader.
7. Peer-reviewed or reproducible external research, only as secondary evidence.
8. Other third-party analysis, only to generate a hypothesis or locate a primary source.

Never use an SEO blog, consultant post, influencer thread, or algorithm playbook as primary evidence. When primary and secondary evidence conflict, prefer the current primary source, explain the conflict, and do not blend them into a compromise claim.

## Freshness rules

- `last_verified` means the URL and summarized claim were checked on that date; it does not promise future validity.
- A source without published/updated metadata is not automatically stale, but analyses must disclose that its age is unknown.
- Recheck when the user says latest/current/now, when a consequential decision depends on it, when a platform changed the product surface, or when a recorded code repository has moved beyond the pinned commit.
- When offline, say `knowledge snapshot verified YYYY-MM-DD`; do not imply live verification.
- Downgrade a claim to `stale` when the only source is old and current validity cannot be established. Do not silently delete useful historical context.

## Code evidence

Numeric parameters require all of: repository, full commit SHA, code path, recorded value, interpretation, and limitation. A branch URL such as `main` is a discovery link, not stable provenance.

Public code can establish what the published snapshot does. It cannot by itself establish that every production request uses that exact code, configuration, experiment assignment, model checkpoint, or unredacted component. Preserve those limits with every code-derived operational conclusion.

## Quotation and storage

Store concise summaries and provenance, not copied pages. Quote only when wording is essential. Keep third-party material out of the registry unless it is clearly labeled `secondary`; it may not be the sole source for a confirmed claim.
