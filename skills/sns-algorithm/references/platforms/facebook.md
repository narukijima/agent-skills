# Facebook

## Surfaces

Meta publishes separate system cards for Feed, Feed Recommendations, ranked comments, Reels, Stories, Video, Search, Groups feeds, Marketplace, notifications, account/Page suggestions, and others [claim `facebook-distinct-surfaces`]. Do not use one News Feed explanation as a universal Facebook algorithm.

## Connected Feed

Official engineering/product material describes a multilayer process that starts with eligible candidate posts from connected friends, Pages, and Groups, uses thousands of signals to make multiple predictions, combines them into ranking values, and applies integrity and diversity rules [claim `facebook-feed-ranking`]. Survey-derived value and negative feedback can complement engagement; high engagement alone is not the stated objective.

## Home and Feed Recommendations

Meta distinguishes discovery in Home and unconnected recommendations from connected inventory. Official material describes content understanding, cold start, interest/graph learning, and discovery methods at a high level [claims `facebook-home-discovery`, `facebook-unconnected-recommendations`]. Do not assume the same candidate pool or causal interpretation as connected Feed.

Recommendation Guidelines are eligibility rules for content from accounts/Pages/Groups a viewer did not choose. Content may be permitted on Facebook yet not eligible for recommendations [claim `facebook-recommendation-eligibility`]. This is different from removal, an account-level restriction, and losing a ranking comparison.

## Reels, Stories, Search, and Groups

Each has a separate system card. Before using a signal, verify the current card for that surface. A Reel watch metric cannot establish Search relevance; Group Feed behavior cannot establish Home recommendation behavior.

## Diagnostic use

Segment connected Feed, recommended/Home, Reels, Search, Groups, Page/profile, and external traffic. Check audience relationship, eligibility, inventory, survey/negative feedback proxies, topic demand, competition, and measurement window. Treat old News Feed articles as historical unless the same mechanism is present in a current system card.
