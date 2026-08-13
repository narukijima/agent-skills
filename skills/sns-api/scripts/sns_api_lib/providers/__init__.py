"""Static provider registry. Dynamic code loading is intentionally forbidden."""


class PlannedTikTok:
    name = "tiktok"
    status = "planned"
    api_version = None
    capabilities = (
        "identity.read", "content.read", "publish.image", "publish.video", "publish.status",
    )

    def capability_document(self):
        return {
            "platform": self.name,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "runtime_supported": False,
        }


_PROVIDERS = None


def get_providers():
    global _PROVIDERS
    if _PROVIDERS is None:
        from .facebook import FacebookProvider
        from .instagram import InstagramProvider
        from .threads import ThreadsProvider
        from .x import XProvider
        from .youtube import YouTubeProvider
        _PROVIDERS = {
            "facebook": FacebookProvider(), "instagram": InstagramProvider(),
            "threads": ThreadsProvider(), "tiktok": PlannedTikTok(),
            "x": XProvider(), "youtube": YouTubeProvider(),
        }
    return _PROVIDERS
