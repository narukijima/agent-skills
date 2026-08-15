# Analysis and experiment framework

## Observed evidence first

Separate directly observed data from interpretation. Useful inputs include impressions/reach, traffic or recommendation source, views, unique viewers, click/choose-to-view rate, retention/average duration/completion, likes, replies/comments, shares/sends, follows, negative feedback, eligibility or restriction notices, timestamps, content attributes, and a comparable baseline.

Record unavailable fields as missing. Do not infer zero, eligibility, or absence from a missing metric.

## Driver taxonomy

Rank multiple candidates, not one story:

- **Creator-controllable:** topic framing, audience match, packaging, opening/hook, clarity, duration, structure, originality, metadata relevant to Search, reply/conversation design, and policy-safe execution.
- **Viewer/context:** personal history, format preference, device, time/context, network relation, prior exposure, feedback, and audience composition.
- **Platform/system:** eligibility, candidate inventory, retrieval, ranking, re-ranking, diversity, filtering, experiments, latency, and measurement definitions.
- **External:** topic demand, competition, seasonality, news cycle, language/region, and off-platform acquisition.

Use `high` only when observed data discriminates this driver from credible alternatives. A metric moving in the expected direction is correlation, not causal proof.

## Mechanism-to-lever translation

Label each bridge:

```text
confirmed mechanism -> observed proxy -> inference -> controllable lever
```

Example: an official source says a surface predicts whether a viewer will continue watching. A retention curve may be a relevant proxy. Improving an opening is a creator-controllable intervention, but the claim that a specific edit will raise distribution remains a hypothesis until tested.

Keep these distinct:

- a disclosed ranking/recommendation signal;
- an official creator recommendation;
- an analytics metric used to understand response;
- an analyst-created proxy;
- a project KPI.

## Minimum experiment

Prefer one changed variable at a time where practical:

- hypothesis and mechanism being tested;
- platform, surface, audience, content class, and eligibility scope;
- one intervention and explicit fixed conditions;
- primary metric closest to the mechanism;
- guardrail metrics for quality/negative effects;
- comparison design (randomized when possible, otherwise matched cohort or repeated baseline);
- minimum sample/time window and stop rule decided before results;
- interpretation for positive, null, and adverse results.

Do not call two ordinary posts an A/B test when audience assignment, timing, topic, or creative differs materially. Call it an observational comparison and list confounders.

## Shadowban triage

Treat `shadowban` as a user-supplied label, not a diagnosis. Check in this order:

1. metric definition, delay, window, and source attribution;
2. recommendation/search eligibility and visible notices;
3. account or content policy restriction;
4. surface mismatch and following-vs-recommended distribution;
5. demand, competition, audience fit, creative response, and repeated exposure;
6. reproducibility across posts, viewers, regions, and surfaces.

Return `unknown` when the platform provides no evidence to distinguish enforcement from ordinary ranking or demand variation.

## Suggested diagnostic shape

```yaml
platform: instagram
surface: reels
observed_evidence: []
confirmed_algorithm_mechanics: []
likely_drivers:
  - driver: "..."
    confidence: medium
    reasoning: "observed proxy -> bounded inference"
alternative_explanations: []
unknowns: []
recommended_experiment_or_action: {}
source_freshness: "verified/snapshot date and limitations"
```

Use natural language when it is clearer; retain these fields internally.
