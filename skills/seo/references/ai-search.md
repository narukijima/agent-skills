# AI search, crawler and citation protocol

This area is highly time-dependent. Never answer from a permanent bot list or an old platform-ranking table. Open each selected provider's current official documentation during the task.

## Separate the systems

For every provider, build a current control matrix:

| Role | Purpose | Discovery / fetch mode | Published identity verification | robots behavior | Publisher control | Evidence date |
| --- | --- | --- | --- | --- | --- | --- |
| search / indexing crawler | build or refresh search retrieval | automatic | current official method | provider-specific | provider-specific | date |
| training crawler | collect data potentially used for model development | automatic | current official method | provider-specific | provider-specific | date |
| user-triggered fetch | retrieve a URL because a user asked | on demand | current official method | may differ | provider-specific | date |
| other product crawler | ads, preview or another surface | product-specific | current official method | provider-specific | provider-specific | date |

Do not assume that blocking one role blocks all products from the company, or that allowing one role enables citations. Even when a provider reuses a crawl internally, preserve the separate published controls and purposes.

Current entry points are in `source-policy.md`. Resolve names, purposes, identity checks and controls from those pages at execution time; do not preserve an earlier bot matrix as current truth.

## Access diagnosis

Check:

- robots groups and product-specific controls
- provider-published IP ranges or verification procedures
- WAF / CDN / rate-limit behavior for verified traffic
- `401`, `403`, `429`, `5xx` and resource failures
- actual logs and fetch timestamps
- whether the desired content is in the provider's retrieval source, where officially disclosed

Allowlisting a User-Agent without verified source can weaken security. A crawler success test proves access at that time; it does not prove indexing, retrieval, citation or recommendation.

## Google AI features

Use current Google Search documentation. Confirm which normal Search eligibility and preview controls apply, whether any additional file or schema is required, and which controls cover other product uses. Do not substitute one Google crawler or product control for another. Recheck the current pages before changing controls:

- <https://developers.google.com/search/docs/appearance/ai-features>
- <https://developers.google.com/search/docs/fundamentals/ai-optimization-guide>

## `llms.txt` and agent-readable files

Keep four claims distinct:

1. A proposal / file format exists.
2. A tool or documentation site can read or publish it.
3. A specific search / AI provider officially consumes it for a named product.
4. Controlled evidence shows a citation or visibility effect.

The original `llms.txt` project describes a proposed Markdown convention: <https://llmstxt.org/>. Conformance to that proposal does not establish claims 3 or 4. A provider hosting its own `llms.txt` for documentation discovery does not prove its search product uses publisher files. Check the selected provider's current documentation before classifying support or effect.

Classify emerging files as:

- `experimental`: defined proposal or early implementation with unresolved adoption.
- `provider-supported`: current official documentation names the product and behavior.
- `unsupported-effect`: no official or strong causal evidence for ranking / citation impact.

Implement an experimental file only when it has a concrete consumer or low-cost documentation benefit, clear ownership, no conflict with canonical content, and measurement / removal criteria. Do not displace sitemap, crawl access, content quality or standard metadata work.

## Content and citation claims

Do not prescribe a fixed paragraph length, chunk size, heading count, freshness cadence, schema set, PDF format, author-bio pattern or writing style as a universal citation factor. These may be experiment candidates when a provider or reproducible study supports the selected context.

Prioritize fundamentals that can be independently justified:

- accurate, accessible and indexable information
- clear entity identity and source provenance
- claims backed by primary evidence
- visible content consistent with structured data
- useful internal discovery and stable URLs
- current product / price / availability data where relevant

Separate being cited, mentioned and recommended. Offsite evidence may affect answers, but do not manufacture mentions, reviews or consensus.

## Visibility measurement

Use `measurement.md`. Define a query / prompt panel from real audience tasks and record product surface, model if exposed, locale, date, account / personalization, repetitions and result capture method. Score separately:

- cited URL and passage support
- brand mention
- recommendation
- factual accuracy
- referral and conversion

AI responses are stochastic and providers change retrieval. A single prompt or vendor score is not a ranking position. Respect provider terms and avoid unapproved automated querying.

## Decision examples

- Search crawler blocked, training crawler allowed: diagnose search access; do not claim training access restores search visibility.
- Training crawler blocked, search crawler allowed: report the two choices separately; do not claim search exclusion.
- User fetch succeeds while automatic crawler is blocked: report on-demand accessibility only.
- `llms.txt` requested as a ranking fix: state the selected provider's current support and evidence level; do not promise lift.
- citation drop without crawler errors: examine query panel, cited sources, content / entity changes and provider variability before editing robots.

## Verification

After a control change, verify robots output, WAF policy, authentic provider fetch where observable, and no accidental exposure of private paths. Mark search reprocessing and citation behavior pending until measured. Do not say a crawler change "will" produce citation.
