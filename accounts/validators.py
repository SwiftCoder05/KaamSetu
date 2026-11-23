import re

from django.core.exceptions import ValidationError


class ComplexityValidator:
    """Requires at least one uppercase letter, one digit, and one symbol, and caps the length at 16."""

    max_length = 16

    def validate(self, password, user=None):
        if len(password) > self.max_length:
            raise ValidationError(
                f"This password is too long. It must be at most {self.max_length} characters.",
                code="password_too_long",
                params={"max_length": self.max_length},
            )
        if not re.search(r"[A-Z]", password):
            raise ValidationError("This password must contain at least one uppercase letter.", code="password_no_upper")
        if not re.search(r"\d", password):
            raise ValidationError("This password must contain at least one number.", code="password_no_number")
        if not re.search(r"[^A-Za-z0-9]", password):
            raise ValidationError("This password must contain at least one symbol.", code="password_no_symbol")

    def get_help_text(self):
        return f"Your password must be at most {self.max_length} characters and include an uppercase letter, a number, and a symbol."
