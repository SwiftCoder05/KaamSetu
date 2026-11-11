from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def default_daily_limit() -> int:
    return settings.DEFAULT_DAILY_LIMIT


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    full_name = models.CharField(max_length=120, blank=True)
    daily_limit = models.PositiveSmallIntegerField(
        default=default_daily_limit,
        validators=[MinValueValidator(1), MaxValueValidator(200)],
        help_text="Maximum emails accepted by Gmail per rolling 24 hours.",
    )
    # AI is off until the user switches it on; tailoring also needs an explicit button press each time.
    ai_enabled = models.BooleanField(default=False)

    def __str__(self):
        return self.full_name or self.user.get_username()

    @property
    def sender_name(self) -> str:
        return self.full_name or self.user.get_username().split("@")[0]
