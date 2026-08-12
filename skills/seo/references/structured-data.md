# Structured data protocol

## Keep three questions separate

1. **Schema.org validity**: Is the JSON-LD / Microdata / RDFa parseable, and are terms valid in the current Schema.org release?
2. **Consumer eligibility**: Does the current search product support a feature for this content, with its own required properties and policies?
3. **Display and ranking**: Did the consumer choose to show a feature? Valid markup enables interpretation or eligibility; it does not guarantee display or higher ranking.

Schema.org itself generally does not define mandatory properties. A search engine's feature guide may define required and recommended properties for that consumer. Do not copy a consumer's property table into the Skill as permanent truth.

## Source order

- Actual visible page content and authoritative Project data
- Current Schema.org term definitions / release
- Current consumer search gallery and feature guide
- General structured-data policies
- Schema.org validator, consumer validator and provider reports

Entry points:

- Schema.org releases: <https://schema.org/docs/releases.html>
- Schema.org validator: <https://validator.schema.org/>
- Google Search gallery: <https://developers.google.com/search/docs/appearance/structured-data/search-gallery>
- Google general policies: <https://developers.google.com/search/docs/appearance/structured-data/sd-policies>
- Google Rich Results Test: <https://search.google.com/test/rich-results>

Recheck feature availability for FAQ, HowTo, Product, Review and every other type at implementation time. A type can remain valid Schema.org vocabulary after a particular search appearance is restricted or removed.

## Investigation

For the target page and template:

1. Identify the main visible entity and data owner.
2. Inspect existing static markup and rendered DOM. Parse every JSON-LD block and any Microdata / RDFa.
3. Resolve duplicate nodes, inconsistent `@id`, conflicting values and plugin / application ownership.
4. Compare marked values with visible content and current backend data.
5. Validate vocabulary separately from each desired consumer feature.
6. Inspect Search Console or equivalent enhancement / issue data when available.

If static HTML lacks JSON-LD but client code may inject it, report `not observed in static HTML` and render the page. `scripts/seo_evidence.py extract-html` intentionally preserves this distinction.

## Implementation gates

- **Relevance**: the type describes the page's actual main content.
- **Visibility**: marked claims are visible to users when the consumer requires it.
- **Accuracy**: prices, availability, ratings, dates, authors and identifiers come from authoritative data.
- **Completeness**: current consumer-required properties are populated; recommended properties are added only when accurate.
- **Consistency**: canonical duplicates, feeds and page markup do not contradict each other.
- **Security**: serialization cannot break out of the script element or expose secrets; untrusted content is encoded safely.

Prefer one data model shared by visible UI and JSON-LD. Avoid hand-maintained markup that silently drifts. JSON-LD is often operationally convenient and is recommended by Google, but use a supported format compatible with the Project.

## Validation matrix

| Surface | Proves | Does not prove |
| --- | --- | --- |
| JSON parser | valid JSON | Schema.org semantics |
| Schema.org validator | extractable vocabulary graph / syntax diagnostics | rich-result eligibility |
| consumer validator | current supported feature checks in that tool | guaranteed display or ranking |
| rendered DOM | markup exists after rendering | provider indexed the latest version |
| provider report | provider processed examples / issues | every URL or permanent display |

Warnings are not automatically failures; explain whether they affect eligibility, quality or optional enhancement. Conversely, a green validator cannot detect every misleading or stale claim.

## Fix and verify

Implement at the owning template / component / data layer. Test missing, empty, zero, unavailable, multi-currency, locale, pagination and stale-data cases. After build and deploy:

- inspect static and rendered markup
- parse the exact deployed JSON-LD
- compare with visible content and canonical URL
- run Schema.org and relevant consumer validation
- check representative templates and regression tests
- mark provider recrawl / enhancement reporting as pending when applicable

Do not add unrelated types merely to maximize markup volume. Do not fabricate reviews, ratings, authors, prices or entities. Do not claim schema is an AI citation or ranking lever without direct current provider evidence.
