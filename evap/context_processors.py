from django.conf import settings
from django.utils.translation import get_language
from evap.evaluation.models import TextanswerVisibility as tv

import random


def slogan(request):
    if get_language() == "de":
        return {'SLOGAN': random.choice(settings.SLOGANS_DE)}
    return {'SLOGAN': random.choice(settings.SLOGANS_EN)}

def TextanswerVisibility(request):
    return {visibility.name: visibility.value for visibility in tv}
