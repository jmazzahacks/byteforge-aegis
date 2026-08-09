"""
Outcome of one webhook retry sweep.

A model rather than a dict because the sweep runs unattended on a
schedule: the endpoint serializes these counts and an operator alerts on
them, so a mistyped key would be a runtime KeyError in the one code path
nobody is watching. Mirrors TokenCleanupResult, which the sibling cron
endpoint already uses for the same reason.
"""
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class WebhookSweepResult:
    """
    Attributes:
        attempted: Deliveries claimed and attempted in this sweep.
        delivered: Attempts the tenant accepted.
        retrying: Attempts that failed and remain owed.
        exhausted: Deliveries given up on. These events are permanently
            lost to their tenant, so this is the number worth alerting on.
        still_due: Deliveries due but not reached, because the sweep is
            bounded. Persistently non-zero means the backlog is growing
            faster than the schedule drains it.
        pruned: Old delivered rows removed to bound the table's size.
    """
    attempted: int
    delivered: int
    retrying: int
    exhausted: int
    still_due: int
    pruned: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert sweep result to dictionary."""
        return {
            'attempted': self.attempted,
            'delivered': self.delivered,
            'retrying': self.retrying,
            'exhausted': self.exhausted,
            'still_due': self.still_due,
            'pruned': self.pruned,
        }
