from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model


class CustomUserCreationForm(UserCreationForm):
    """Extends default form to require unique email (case-insensitive)."""

    email = forms.EmailField(required=True, help_text="Obrigatório. Use um endereço de e-mail válido.")

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = UserCreationForm.Meta.fields + ("email",)

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email:
            UserModel = get_user_model()
            # check case-insensitively; exclude current instance if editing
            qs = UserModel.objects.filter(email__iexact=email)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("Já existe uma conta com este e-mail.")
        return email
