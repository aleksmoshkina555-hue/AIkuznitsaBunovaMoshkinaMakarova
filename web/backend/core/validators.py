"""Правила пароля — те же, что в прежней версии (users.password_problems): от 8 символов, буквы и цифры,
не равен логину."""
from django.core.exceptions import ValidationError

from users import password_problems


class PolicyValidator:
    def validate(self, password, user=None):
        problems = password_problems(password, getattr(user, "username", "") or "")
        if problems:
            raise ValidationError("; ".join(problems))

    def get_help_text(self):
        return "Не короче 8 символов, буквы и цифры, не совпадает с логином."
