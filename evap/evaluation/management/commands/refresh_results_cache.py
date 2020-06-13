from django.core.management.base import BaseCommand
from django.core.serializers.base import ProgressBar
from django.core.cache import caches

from evap.evaluation.models import Evaluation, Course
from evap.results.tools import cache_results, STATES_WITH_RESULTS_CACHING
from evap.results.views import update_results_template_cache_of_evaluations, update_results_template_cache_of_courses


class Command(BaseCommand):
    args = ''
    help = 'Clears the cache and pre-warms it with the results of all evaluations'
    requires_migrations_checks = True

    def handle(self, *args, **options):
        self.stdout.write("Clearing results cache...")
        caches['results'].clear()
        total_count = Evaluation.objects.count()

        self.stdout.write("Calculating results for all evaluations...")

        self.stdout.ending = None
        progress_bar = ProgressBar(self.stdout, total_count)

        # perhaps better progress bars? or, well, make it fast...

        evaluations = Evaluation.objects.filter(state__in=STATES_WITH_RESULTS_CACHING)
        for counter, evaluation in enumerate(evaluations):
            progress_bar.update(counter + 1)
            cache_results(evaluation)

        self.stdout.write("Prerendering result index page...\n")

        update_results_template_cache_of_evaluations(Evaluation.objects.all())
        update_results_template_cache_of_courses(Course.objects.all())

        self.stdout.write("Results cache has been refreshed.\n")
