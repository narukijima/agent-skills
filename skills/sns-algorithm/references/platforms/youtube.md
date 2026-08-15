# YouTube

## Surfaces

- **Home:** personalized recommendation surface; official Help says watch history is a primary input.
- **Suggested / Up Next:** recommendation alongside the current video; the current video is a primary signal.
- **Shorts Feed:** personalized short-form sequence with format-specific performance and viewer personalization.
- **Search:** query-driven ranking using relevance, engagement for the query, and quality signals.
- **Subscriptions and destination/channel shelves:** separate inventory/presentation rules; do not treat chronological Subscriptions as Home ranking.

These distinctions are official [claims `youtube-distinct-surfaces`, `youtube-search-ranking`, `youtube-shorts-ranking`].

## Recommendation mechanics

Official Help describes two broad inputs: viewer personalization and how content performs when offered. Viewer history can include watch/skip/dismiss behavior, amount watched, searches, likes, shares, comments, negative feedback, surveys, subscriptions, language, device, and context. Similar-viewer patterns and topic/format affinity can also inform recommendation [claim `youtube-recommendation-signals`].

The stated goals are helping a viewer find videos they want to watch and maximizing long-term viewer satisfaction. Satisfaction surveys, likes/dislikes, sharing, watch behavior, and other signals do not form one fixed public formula; their importance is personalized and surface-dependent [claim `youtube-satisfaction`].

## Search

YouTube Search is not Home recommendation. Official Help identifies relevance, engagement, and quality. Relevance can use title, tags, description, and video-content match to the query. Engagement includes query-specific behavior such as watch time for that query. For some topics, quality includes signals of expertise, authoritativeness, and trustworthiness [claim `youtube-search-ranking`].

## Shorts

Official creator documentation says Shorts are ranked by performance and viewer personalization. Search discovery for Shorts considers query/metadata match and whether viewers click and watch. Topic demand, competition, and seasonality can limit distribution even when a Short's own metrics are good [claim `youtube-shorts-ranking`]. Do not move TikTok For You rules into Shorts.

## Metric classification

Keep three categories separate:

- **Officially described recommendation evidence:** viewer watch/skip behavior, history, positive/negative feedback, survey-derived satisfaction, and performance when offered.
- **Creator analytics:** impressions, CTR, views, retention, average view duration, traffic source, unique viewers, and returning viewers.
- **Analysis proxies:** hook quality inferred from an early retention drop, audience match inferred from traffic-source cohorts, or satisfaction inferred from return behavior.

CTR and retention are useful, but one aggregate number does not reveal a rank score. High CTR and watch duration can coexist with low impressions because of audience size, competition, topic interest, context, or satisfaction/quality factors [claims `youtube-performance-framework`, `youtube-external-factors`].

## Diagnostic use

Segment by Home, Suggested, Shorts Feed, Search, Browse, External, and Subscriptions before diagnosing. Compare the same format and similar topic/age window. For a distribution stop, consider exhausted audience match, changing viewer response, topic demand, competition, seasonality, policy/eligibility, and metric delay before claiming an algorithmic penalty.
