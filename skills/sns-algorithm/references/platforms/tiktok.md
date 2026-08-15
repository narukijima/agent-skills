# TikTok

## Surfaces

Official Support distinguishes For You, Following, Friends, LIVE, Search, comments, notifications, and account recommendations. The same factor category can have different relative importance by surface [claim `tiktok-distinct-surfaces`].

## For You

Current official Support groups inputs as user interactions, content information, and user information. Examples include like/share/comment, full watch or skip, follow relationships, sounds, hashtags, view count, publication country, language, location/context, and device. For most users, interactions including time spent watching are generally weighted more heavily than other categories [claim `tiktok-foryou-factors`]. This is qualitative; do not invent numeric weights.

TikTok also describes diversity behavior: avoiding already-seen/repetitive recommendations and deliberately introducing different content/categories. A sequence constraint is not proof that a creator or format is globally penalized [claim `tiktok-diversity`].

## Search

Search is query-led. Official Support says past search/interaction behavior, query/content match, hashtags/sound, language/location/device can influence results; content information including match to the entered term is generally weighted more heavily for most users [claim `tiktok-search-factors`]. Do not apply For You watch-time priority as the sole Search explanation.

## Eligibility

For You eligibility rules are separate from ranking. Content can remain on TikTok while being unsuitable for broad recommendation, and some categories may be interrupted when repeated [claim `tiktok-fyf-eligibility`]. Check notices and content/account status before treating low distribution as ranking competition.

## Historical claims

The 2020 Newsroom article says follower count and an account's previous high-performing videos were not direct For You recommendation factors. This is valuable myth correction but is old; current validity must be rechecked before using it as a current specification [claim `tiktok-follower-count-historical`]. Current Support does list follower/view information for **account recommendations**, illustrating why surface matters.

## Diagnostic use

Segment For You, Following/Friends, Search, profile, LIVE, and external sources. Compare watch/full-watch/skip behavior within similar duration and audience cohorts; consider topic demand, competition, freshness, region/language, diversity, prior exposure, and eligibility. A completion metric is an observed proxy, not proof of a fixed distribution threshold.
