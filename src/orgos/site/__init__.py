"""The public results page: one static page built from the conclusion and the findings report.

The Phase 1 conclusion lives in the private docs repo, so it has no public home.
This package builds `site/index.html` and `site/style.css` from a tracked copy of
the conclusion (word for word) and `results/findings/report.json`, and checks the
page before it is published: a regeneration, a numbers check and a scan for email
addresses and real looking names (spec 0016). It opens no store, calls no model
and loads no `Settings`, so it runs in CI with no `.env`. Every failure it can
expect surfaces as one `SiteError`, which the CLI turns into exit code 8.
"""

from __future__ import annotations


class SiteError(Exception):
    """Raised when the page cannot be built, differs from its inputs, or fails the scan."""
