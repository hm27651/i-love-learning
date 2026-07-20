from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


class UiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        cls.style = compact((ROOT / "static" / "style.css").read_text(encoding="utf-8"))
        cls.sidebar = compact((ROOT / "static" / "sidebar.css").read_text(encoding="utf-8"))
        cls.refinements = compact((ROOT / "static" / "refinements.css").read_text(encoding="utf-8"))
        cls.ui_system = compact((ROOT / "static" / "ui-system.css").read_text(encoding="utf-8"))
        cls.app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def test_390px_mobile_visual_contract(self):
        self.assertIn('name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"', self.base)
        self.assertIn('class="mobile-topbar"', self.base)
        self.assertIn('class="mobile-nav" aria-label="手机导航"', self.base)
        self.assertIn("@media(max-width:760px)", self.style)
        self.assertIn(".sidebar{display:none}", self.style)
        self.assertIn(".app-frame{margin-left:0}", self.style)
        self.assertIn(".mobile-nav{position:fixed", self.style)
        self.assertIn("display:grid;grid-template-columns:repeat(5,1fr)", self.style)
        self.assertIn("env(safe-area-inset-bottom)", self.style)
        self.assertIn(".question-mobile-list{display:block}", self.style)
        self.assertIn("@media(max-width:520px)", self.style)

    def test_critical_pages_keep_responsive_fallbacks(self):
        contracts = {
            "questions.html": ("question-table", "question-mobile-list"),
            "practice_setup.html": ("practice-mode-grid", "match-count"),
            "imports.html": ("transfer-tabs", "transfer-job"),
            "knowledge.html": ("knowledge-tree",),
            "welcome.html": ("template-picker",),
        }
        for template_name, markers in contracts.items():
            source = (ROOT / "templates" / template_name).read_text(encoding="utf-8")
            with self.subTest(template=template_name):
                self.assertRegex(source, r"\{%\s*extends\s+['\"]base\.html['\"]\s*%\}")
                for marker in markers:
                    self.assertIn(marker, source)
        self.assertIn("@media(max-width:520px){.transfer-tabs", self.style)
        self.assertIn("@media(max-width:600px){.practice-mode-grid{grid-template-columns:1fr", self.refinements)
        self.assertIn("@media(max-width:760px){.template-picker{grid-template-columns:1fr", self.refinements)

    def test_collapsed_sidebar_keeps_accessible_navigation_contract(self):
        self.assertIn("--sidebar-width:72px", self.sidebar)
        self.assertIn("content:attr(data-label)", self.sidebar)
        self.assertIn(".sidebar-collapsed.sidebar-toggle", self.sidebar)
        self.assertIn("data-sidebar-toggle", self.base)
        self.assertIn('aria-expanded="true"', self.base)
        for label in ("今天", "章节练习", "错题复习", "题库", "知识树", "设置"):
            self.assertIn(f'data-label="{label}', self.base)
            self.assertIn(f'aria-label="{label}', self.base)

    def test_semantic_ui_layer_keeps_accessibility_contracts(self):
        self.assertIn('filename=\'ui-system.css\'', self.base)
        self.assertIn("--color-action:", self.ui_system)
        self.assertIn("--sidebar-width:72px", self.ui_system)
        self.assertIn("min-height:44px", self.ui_system)
        self.assertIn(".containera:not(.button):not(.card){min-height:44px", self.ui_system)
        self.assertIn("@media(prefers-reduced-motion:reduce)", self.ui_system)
        self.assertIn('aria-controls="mobile-more-sheet"', self.base)
        self.assertIn("sheetFocusableElements", self.app_js)
        self.assertIn("event.key !== 'Tab'", self.app_js)

    def test_mobile_data_locations_wrap_long_paths(self):
        template = (ROOT / "templates" / "data_management.html").read_text(encoding="utf-8")
        self.assertGreaterEqual(template.count("data-location-heading"), 2)
        self.assertIn(".data-location-heading{flex-wrap:wrap", self.ui_system)
        self.assertIn(".data-location-heading.muted{overflow-wrap:anywhere", self.ui_system)

    def test_knowledge_tree_uses_nested_icon_cards(self):
        template = (ROOT / "templates" / "knowledge.html").read_text(encoding="utf-8")
        for kind in ("subject", "chapter", "point"):
            self.assertIn(f"'{kind}':", self.base)
            self.assertIn(f"nav_icon('{kind}')", template)
        self.assertIn(".knowledge-subject{border:1pxsolidvar(--color-border);border-radius:var(--radius-md)", self.ui_system)
        self.assertIn(".knowledge-chapter{border:1pxsolidvar(--color-border);border-radius:11px", self.ui_system)
        self.assertIn(".knowledge-point{min-height:56px", self.ui_system)
        self.assertIn(".knowledge-level-mark.point-mark", self.ui_system)

    def test_project_card_separates_metrics_from_actions(self):
        self.assertIn(".project-card>.actions{margin-top:var(--space-6);}", self.ui_system)


if __name__ == "__main__":
    unittest.main()
