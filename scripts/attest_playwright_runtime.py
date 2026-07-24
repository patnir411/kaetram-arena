#!/usr/bin/env python3
"""Print the pinned Playwright Chromium identity after one real local launch."""
from __future__ import annotations

import json

from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    executable = playwright.chromium.executable_path
    browser = playwright.chromium.launch(headless=True)
    try:
        print(json.dumps({
            "browser_name": playwright.chromium.name,
            "browser_version": browser.version,
            "executable_path": executable,
        }, sort_keys=True))
    finally:
        browser.close()
