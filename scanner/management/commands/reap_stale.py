from django.core.management.base import BaseCommand

from scanner.maintenance import reap_stale


class Command(BaseCommand):
    help = "Mark resolved/expired markets closed and archive their pairs."

    def handle(self, *args, **opts):
        result = reap_stale()
        self.stdout.write(self.style.SUCCESS(str(result)))
