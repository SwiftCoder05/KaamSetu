import time

from .models import Profile

SESSION_KEY = "presence_at"
INTERVAL = 60


class PresenceMiddleware:
    """Stamps Profile.last_seen for signed-in users, at most once per INTERVAL seconds per session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not request.path.startswith(("/static/", "/live-users/")):
            now = time.time()
            if now - request.session.get(SESSION_KEY, 0) >= INTERVAL:
                from django.utils import timezone

                Profile.objects.filter(user=user).update(last_seen=timezone.now())
                request.session[SESSION_KEY] = now
        return self.get_response(request)
