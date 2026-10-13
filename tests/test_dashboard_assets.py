"""Guards against the dashboard's HTML, JavaScript and CSS drifting apart.

An earlier stylesheet had no rules for 43 of the 86 classes in the page, so
the layout was broken. These checks fail if that happens again.
"""
import os
import re
import unittest

DASHBOARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard")

# Classes that are intentionally unstyled (hooks or inline-styled elements).
UNSTYLED_OK = set(['backend-panel', 'chart-panel', 'events-panel', 'live-label', 'pulse-bar', 'topology-panel'])


def read(*parts):
    with open(os.path.join(DASHBOARD, *parts)) as handle:
        return handle.read()


def tokens(text):
    return {c for c in re.split(r"\s+", text.strip()) if c and not re.search(r"[{}$]", c)}


class DashboardAssetTests(unittest.TestCase):

    def setUp(self):
        self.html = read("templates", "index.html")
        self.js = read("static", "js", "app.js")
        self.css = re.sub(r"/\*.*?\*/", "", read("static", "css", "style.css"), flags=re.S)

    def test_every_id_the_script_uses_exists_in_the_page(self):
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        used = set(re.findall(r'getElementById\(\s*"([^"]+)"', self.js))
        self.assertEqual(sorted(used - ids), [])

    def test_every_class_in_the_page_and_script_has_a_style(self):
        used = set()
        for match in re.findall(r'class="([^"]+)"', self.html):
            used |= tokens(match)
        for match in re.findall(r'class="([^"$]*)', self.js):
            used |= tokens(match)
        for match in re.findall(r'classList\.(?:add|remove|toggle)\(\s*"([^"]+)"', self.js):
            used |= tokens(match)
        styled = set(re.findall(r"\.([a-zA-Z][\w-]*)", self.css))
        self.assertEqual(sorted(used - styled - UNSTYLED_OK), [])

    def test_refresh_and_algorithm_controls_are_present_and_wired(self):
        for element_id in ("refresh-now", "refresh-toggle", "refresh-interval",
                           "algorithm-trigger", "algorithm-menu", "toast-region"):
            self.assertIn(f'id="{element_id}"', self.html)
        for name in ("refreshNow", "setRefreshPaused", "setRefreshInterval", "showToast"):
            self.assertIn(f"function {name}(", self.js)

    def test_page_has_responsive_breakpoints_and_reduced_motion(self):
        self.assertIn("@media (max-width: 640px)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)


if __name__ == "__main__":
    unittest.main()
