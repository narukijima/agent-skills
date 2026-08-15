# Instagram

## Surfaces

Treat these separately: connected **Feed**, **Feed Recommendations** from accounts not followed, **Stories**, **Explore**, **Reels**, **Search**, and suggested accounts/notifications. Meta's official system-card release explicitly lists separate systems [claim `instagram-distinct-surfaces`].

## Feed

The Instagram Feed system card describes sourcing unseen connected content, filtering, scoring action probabilities, merging/normalizing scores across content types, integrity demotions, sorting, media diversity, and author diversity [claim `instagram-feed-ranking`]. This does not establish the same pipeline or weights for Reels, Explore, Search, or Feed Recommendations.

## Feed Recommendations

Meta distinguishes unconnected recommendations from content a person chose to follow. Official material describes content understanding, cold-start matching, interest/graph methods, discovery, and list-level selection at a high level [claim `instagram-unconnected-recommendations`]. Recommendation eligibility is a gate: allowed content can still be ineligible for recommendation [claim `meta-recommendation-eligibility`].

## Explore

Meta Engineering documents a multi-stage Explore system: retrieval, first-stage ranking, second-stage ranking, and final re-ranking. Two-tower models, caching, and precomputation are described for this surface [claim `instagram-explore-stages`]. Do not infer that every Instagram surface uses this exact stack.

## Reels

Meta publishes a separate Reels/Reels Chaining system card, which is enough to reject the idea of one universal Instagram algorithm. Use current card details when making a fresh Reels claim; the registry deliberately does not invent a stable list of weights [claim `instagram-distinct-surfaces`]. Viewer watch behavior, feedback, post properties, and prior author/content interaction may be useful evidence where the current official card says so, but analytics such as completion rate remain observed proxies unless the claim is explicit.

## Stories and Search

Stories and Search have separate system cards. Relationship/interaction evidence relevant to Stories must not be used to explain Search. Search analysis should start with the query, result type, metadata/content match, account/content eligibility, and observed search traffic rather than Feed engagement.

## Diagnostic use

Require the distribution source: follower Feed, recommended Feed, Explore, Reels tab/feed, profile, Stories, or Search. If only total reach is available, lower confidence. Separate recommendation eligibility from account restriction and from ordinary ranking competition. A low-Reels result does not prove the connected Feed audience rejected the post, and vice versa.
