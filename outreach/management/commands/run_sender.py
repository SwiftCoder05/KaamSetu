"""Background sender: keeps confirmed campaigns moving even when nobody has the campaign page open.

    python manage.py run_sender            # loop forever
    python manage.py run_sender --once     # one pass (for cron / Task Scheduler)
"""

import time

from django.core.management.base import BaseCommand

from outreach.connection import active_connection, mailer_factory_for
from outreach.engine import NotConfirmedError, process_batch, runnable_campaigns


class Command(BaseCommand):
    help = "Send queued emails for confirmed campaigns, respecting each user's daily limit."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run a single pass and exit.")
        parser.add_argument("--interval", type=float, default=20.0, help="Seconds between passes.")

    def handle(self, *args, **opts):
        while True:
            for c in runnable_campaigns().select_related("owner"):
                conn = active_connection(c.owner)
                if not conn:
                    continue
                try:
                    r = process_batch(c, mailer_factory_for(conn), from_email=conn.email)
                    if r.processed or r.status != "sending":
                        self.stdout.write(f"[{c.pk}] {c.name}: +{r.accepted} accepted, {r.failed} failed, {r.remaining} left ({r.status})")
                except NotConfirmedError:
                    continue
                except Exception as e:  # noqa: BLE001 - one broken campaign must not stop the others
                    self.stderr.write(f"[{c.pk}] error: {e}")
            if opts["once"]:
                return
            time.sleep(opts["interval"])
