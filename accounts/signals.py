from allauth.account.signals import user_signed_up
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)


@receiver(user_signed_up)
def name_from_provider(sender, request, user, **kwargs):
    """Google sign-ups arrive with a name; use it as the default email sign-off."""
    profile, _ = Profile.objects.get_or_create(user=user)
    name = f"{user.first_name} {user.last_name}".strip()
    if name and not profile.full_name:
        profile.full_name = name[:120]
        profile.save(update_fields=["full_name"])
