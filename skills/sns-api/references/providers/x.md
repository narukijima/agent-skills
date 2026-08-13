# X Provider

## Contract

- Host: `https://api.x.com`
- Auth: OAuth 2.0 app bearer for supported public reads/usage where applicable; OAuth 1.0a User Context or OAuth 2.0 Authorization Code + PKCE User Context for identity/write.
- OAuth 2.0 scopes for this surface: minimize to `tweet.read`, `users.read`, and `tweet.write`; add `offline.access` only when the private refresh-store flow is used.
- Account binding: numeric stable X user ID from `GET /2/users/me`.

Reads cover user lookup, post lookup, user posts, recent search, and usage. Write covers one ordinary standalone text post at `POST /2/tweets`. Reply, quote (including status-URL quote-card intent), media, delete, like, follow, DM, and browser posting are unsupported.

`prepare` applies NFC normalization, twitter-text v3-style weighting, 23-character URLs, emoji clusters, the 280 weighted limit, control/cashtag checks, and `UNDECLARED_QUOTE_TARGET` rejection.

X publish is immediate only when the response contains a post ID. Reconciliation reads the authenticated user's recent posts and matches only the attempt window plus canonical text hash. It compares raw, HTML-unescaped, and t.co-expanded text. It may prove absence only when a complete returned timeline brackets the attempt and the approved text has no URL. Otherwise remain unresolved. X alone supports audited manual resolve.

Upgrades preserve canonical `x-api` v2 post and usage ledger state through the Common Core migration guard. Retire the old runtime first; an invalid or subsequently changed post snapshot blocks X write/recovery, and an unsafe usage snapshot blocks budgeted X calls, before credentials or Provider requests.

Official sources:

- [X API overview](https://docs.x.com/overview)
- [Authenticated user lookup](https://docs.x.com/x-api/users/lookup/quickstart/authenticated-lookup)
- [Manage Posts authentication mapping](https://docs.x.com/fundamentals/authentication/guides/v2-authentication-mapping)
- [Counting characters](https://docs.x.com/fundamentals/counting-characters)
- [Rate limits](https://docs.x.com/x-api/fundamentals/rate-limits)
- [Usage and billing](https://docs.x.com/x-api/fundamentals/usage-and-billing)
- [OAuth 2.0 Authorization Code with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)

Pricing, plan availability, post constraints, and limits drift. Recheck official docs/Developer Console/headers at execution time.
