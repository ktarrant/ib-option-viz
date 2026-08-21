"""Read side for ib-option-calc capture snapshots.

The capture tool writes SQLite snapshots of soon-to-expire option chains; this
package turns them into the derived series the app draws from. Nothing here ever
writes to a snapshot.
"""

__all__ = ["catalog", "db", "frames", "pricing", "surface", "events", "scoring", "cache"]
