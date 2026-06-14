"""Compatibility shim.

The real implementation now lives in the ``prmonitor`` package (``prmonitor.paths``
+ ``prmonitor.common``), which is three-root aware (PLUGIN_ROOT / PROJECT_DIR /
PLUGIN_DATA). This shim re-exports those names so every legacy
``from lib.common import ...`` step-script becomes plugin-aware without edits.
"""
import os
import sys

# scripts/lib/common.py -> up 3 == plugin/repo root, where the prmonitor package lives.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from prmonitor.paths import (  # noqa: F401,E402
    PROJECT_DIR, CONFIG_DIR, RAW_DIR, PROCESSED_DIR, OUTPUT_DIR,
    NEWSLETTER_OUTPUT_DIR, PR_OUTPUT_DIR, CACHE_DIR, SELF_CONTEXT_DIR, LOGS_DIR,
)
from prmonitor.common import (  # noqa: F401,E402
    load_yaml, load_json, save_json, BLOG_DOMAINS, is_blog,
)

# Legacy alias — old code referred to a single PROJECT_ROOT.
PROJECT_ROOT = PROJECT_DIR
