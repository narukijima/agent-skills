# X

## Surface boundary

The current official code source in this Skill is only the **For You Home Timeline**. X also documents Search, Explore, Notifications, Conversations, Trends, Communities, and account recommendations as separate recommender systems. Do not transfer For You weights or filters to those surfaces. Following is primarily followed-account inventory and is not established here as the same ranked pipeline.

Current code snapshot: `xai-org/x-algorithm` commit `c65aa179db7bdd61e2c2821eac87f208a105c053`, committed 2026-08-14 and verified 2026-08-15. Claim IDs below resolve through `source-registry.json`.

## For You published pipeline

At the recorded commit, the repository README and pipeline code describe [claim `x-foryou-pipeline`]:

1. query hydration: recent action sequence, following, blocks/mutes, muted keywords, previously seen/served posts, topics, and other viewer context;
2. candidate sources in parallel;
3. candidate hydration: post/media, author/account labels, quote, language, counts, subscription state, and other features;
4. pre-scoring filters;
5. Phoenix prediction and RankingScorer adjustments;
6. top-K selection;
7. visibility and conversation post-selection filters;
8. blending with non-post modules and side effects that record served posts.

This sequence is `confirmed_code` for the published snapshot, not a generic template.

### Candidate sources and retrieval

- **In-network:** Thunder holds recent posts from followed accounts [claim `x-foryou-candidates`].
- **Out-of-network:** Phoenix retrieval and SimClusters find posts outside the followed network [claim `x-foryou-candidates`].
- Phoenix documentation describes two-tower retrieval and transformer ranking. Published architecture/model artifacts are representative snapshots; production scale, continuously trained checkpoints, and experiments can differ [claim `x-phoenix-model`].

### Scoring

Phoenix predicts viewer-specific action probabilities or continuous values. `RankingScorer` combines these terms, then applies additional adjustments such as author diversity and out-of-network handling; VMRanker can perform list-level re-ranking [claims `x-foryou-scoring`, `x-foryou-reranking`].

The following values are a **commit-pinned snapshot**, not timeless recommendations and not raw engagement-count points. They come from `home-mixer/params/param.rs`, whose header records a configuration-default sync at `2026-08-12T04:09:22Z`; arithmetic is in `home-mixer/scorers/ranking_scorer.rs` [claim `x-foryou-weight-snapshot`].

| Prediction/value term | Recorded default |
| --- | ---: |
| favorite | 0.5 |
| reply | 5.0 |
| retweet | 1.0 |
| photo expand | 0.05 |
| video open | 0.05 |
| post click | 0.4 |
| open link | 0.2 |
| profile click | 0.0 |
| video quality view | 0.05 |
| generic share | 2.0 |
| share via DM | 5.0 |
| share via copy link | 20.0 |
| dwell | 0.0 |
| continuous dwell time | 0.004 |
| continuous click dwell time | 0.0 |
| quote | 5.0 |
| quoted-post click | 0.05 |
| quoted video quality view | 0.0 |
| follow author | 4.0 |
| not interested | -43.2 |
| block author | -31.2 |
| mute author | -58.8 |
| report | -234.0 |
| not dwelled | -0.02 |

These coefficients scale predicted probabilities or continuous model outputs. They do **not** mean one report cancels 468 likes. Predictions are personalized, base rates differ greatly, some terms are conditional, experiments/configuration can vary, and later scoring/re-ranking/filtering still applies. The same snapshot also records author-diversity decay/floor, out-of-network factors, cold-start/new-author logic, and conditional mutual-follow boosts; consult the pinned code before making a numerical claim.

### Filtering, diversity, and served state

The published pipeline includes duplicate, hydration, age, self-post, some out-of-network reply/repost, subscription, previously seen/served, muted keyword, block/mute, media/topic, and other pre-scoring filters. Post-selection calls visibility filtering and conversation deduplication [claim `x-foryou-filtering`].

Do not translate a filter into a creator penalty. A post may be absent because it was never retrieved, was ineligible for that viewer/surface, lost ranking competition, was already served, was removed in list construction, or was subject to visibility rules. These require different evidence.

## Other X surfaces

- **Search Top:** X officially says relevance can include query keywords, popularity, and interactions such as reposts/replies; Safe Search and viewer blocks/mutes filter results [claim `x-search-top`]. No For You coefficient should be applied.
- **Conversations/replies:** X Help describes factors for reply ordering such as original-author replies, followed relationships, and Premium status. This is a separate surface [claim `x-conversation-ranking`].
- **Explore, Notifications, account recommendations, Trends, Communities:** officially listed as distinct systems, but detailed claims must be checked in their own recommender pages [claim `x-distinct-surfaces`].

## Diagnostic use

For a For You decline, request source-segmented impressions, in-network/out-of-network split if available, eligibility/label evidence, served/repeat context, engagement and negative-feedback rates, audience mix, and a matched historical baseline. Code can propose mechanisms; public analytics generally cannot identify the exact candidate source, experiment bucket, or per-viewer model prediction. Keep those `unknown`.

Treat `twitter/the-algorithm` as historical architecture. Never use its old constants as current `xai-org/x-algorithm` values.
