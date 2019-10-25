from django.core.management.base import BaseCommand

from aira.models import Agrifield


class Command(BaseCommand):
    help = "Initiates a recalculation of the model for all fields"

    def handle(self, *args, **options):
        for agrifield in Agrifield.objects.all():
            if agrifield.in_covered_area:
                agrifield.execute_model()
