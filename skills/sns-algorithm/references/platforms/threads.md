# Threads

## Evidence boundary

Threads has less public end-to-end ranking detail than X, Meta's Facebook/Instagram system cards, YouTube, or TikTok. Do not import Instagram Feed, Instagram Reels, or Facebook Feed mechanisms simply because Meta owns the products.

Known surfaces include a recommended/For You feed, Following, Search, Trending, custom/community feeds, and notification/discovery experiences. The public source set does not establish a complete candidate-generation, scoring, filtering, or weight specification for Threads [claim `threads-ranking-unknown`].

## Confirmed and guidance-level claims

- Threads launched with feed content from followed accounts plus recommended content from creators not yet followed [claim `threads-feed-inventory`].
- Meta's creator education reports that posts driving conversations are more likely to be recommended and that replies account for a large share of observed views. Other statements about frequency, weekends, format combinations, humor, and originality are first-party guidance/observations, not disclosed ranking weights [claim `threads-creator-guidance`].
- Threads Trending selection has been described using post volume and engagement before product review; this is Trending evidence, not proof of feed ranking [claim `threads-trending`].
- Dear Algo/Your Algo lets users temporarily express topic preferences for their own feed. This establishes a control/input, not the rest of the ranking pipeline [claim `threads-topic-controls`].

## Diagnostic use

Use Threads-native insights: views, replies, reposts, quotes, follows, link clicks if available, source/context, content topic/format, and comparison window. Creator guidance can motivate a conversation-design experiment, but label the bridge as inference. If a requested mechanism is not in the registry, return `unknown` and propose the minimum observation or experiment instead of filling it with an Instagram analogy.
