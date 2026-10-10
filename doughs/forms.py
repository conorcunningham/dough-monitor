from django import forms

from .models import Dough


class DoughForm(forms.ModelForm):
    # Bakers weigh yeast in grams; it is stored as whole milligrams.
    yeast_g = forms.DecimalField(
        label='Yeast (g)',
        required=False,
        min_value=0,
        max_digits=7,
        decimal_places=3,
    )

    class Meta:
        model = Dough
        fields = [
            'dough_sensor',
            'ambient_sensor',
            'flour_g',
            'water_g',
            'water_temp_c',
            'salt_g',
            'baking_temp_c',
            'lid_on_minutes',
            'lid_off_minutes',
            'rating',
            'rise',
            'notes',
        ]
        labels = {
            'flour_g': 'Flour (g)',
            'water_g': 'Water (g)',
            'water_temp_c': 'Water temp (°C)',
            'salt_g': 'Salt (g)',
            'baking_temp_c': 'Baking temp (°C)',
            'lid_on_minutes': 'Lid on (min)',
            'lid_off_minutes': 'Lid off (min)',
        }
        widgets = {
            'rating': forms.Select(choices=[('', '—')] + [(i, i) for i in range(1, 6)]),
            'notes': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.yeast_mg is not None:
            self.initial['yeast_g'] = self.instance.yeast_g

    def save(self, commit=True):
        yeast_g = self.cleaned_data['yeast_g']
        self.instance.yeast_mg = None if yeast_g is None else int((yeast_g * 1000).to_integral_value())
        return super().save(commit=commit)

    # Section groupings for the template.
    def ingredients(self):
        return self._fields(['flour_g', 'water_g', 'water_temp_c', 'yeast_g', 'salt_g'])

    def bake(self):
        return self._fields(['baking_temp_c', 'lid_on_minutes', 'lid_off_minutes'])

    def outcome(self):
        return self._fields(['rating', 'rise'])

    def sensors(self):
        return self._fields(['dough_sensor', 'ambient_sensor'])

    def _fields(self, names):
        return [self[name] for name in names]
