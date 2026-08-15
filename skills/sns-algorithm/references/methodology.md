# Algorithm modeling methodology

## Unit of analysis

The unit is `platform × surface × audience/context × time window`, not a platform-wide algorithm. A claim about Instagram Explore does not establish the same mechanism for Feed, Reels, Stories, or Search. A claim about YouTube Home does not establish Search or Shorts behavior.

Use this analysis schema even when a platform does not publish every stage:

1. `eligibility` — content allowed into this recommendation surface.
2. `candidate_generation` / `retrieval` — how possible items are found.
3. `hydration` / `feature_construction` — information attached to viewer and candidates.
4. `ranking` / `scoring` — predictions or scores used to order candidates.
5. `re_ranking` — list-level adjustment after item scoring.
6. `diversity` — author, format, topic, or sequence variety.
7. `filtering` — removal or demotion before/after selection.
8. `distribution` — delivery, inventory, competition, and repeated serving.
9. `feedback_loop` — explicit and implicit viewer feedback that may affect later recommendations.

This is an analysis schema, not a claim that every platform implements every stage. Record unpublished stages as `unknown`.

## Claim construction

For each important claim preserve:

- `platform`, `surface`, and analysis `stage`.
- exact claim, not a broad paraphrase.
- `evidence_class` and independent `confidence`.
- source ID and source type.
- published/updated date and `last_verified`.
- version/commit and code path when applicable.
- scope and limitations.

The machine-readable records live in `source-registry.json`. Platform references explain how to use them; they do not supersede registry provenance.

## Evidence to action chain

Do not skip a link:

```text
source claim
  -> surface-specific mechanism
  -> observed metric consistent/inconsistent with mechanism
  -> inference with alternatives
  -> controllable lever
  -> falsifiable experiment
```

If a link is missing, lower confidence or return `unknown`. An analytics metric can be a useful proxy without being a disclosed ranking signal. A creator recommendation can be useful guidance without proving the implementation.

## Comparison rule

Compare posts only after normalizing or accounting for surface, audience size/mix, measurement window, traffic source, content format/duration, topic demand, and eligibility. If these differ, state that the comparison is observational and identify the confounders rather than producing a false winner.
