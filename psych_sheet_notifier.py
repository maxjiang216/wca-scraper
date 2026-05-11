#!/usr/bin/env python3
"""
WCA Psych Sheet Notifier - top-level entry point.

Usage:
    python psych_sheet_notifier.py --watch-list watch_list.json --email you@example.com --dry-run
    python psych_sheet_notifier.py --help
"""

from wca_org.psych_sheet_notifier import main

if __name__ == "__main__":
    main()
