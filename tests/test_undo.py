"""Тесты функции Отмены действий (Ctrl+Z / Undo)."""
try:
    import _bootstrap
except ImportError:
    pass

import unittest
import theme


class TestUndo(unittest.TestCase):
    def test_theme_undo_icon_present(self):
        self.assertIn("undo", theme.ICONS)
        svg_content = theme.ICONS["undo"]
        self.assertIn("polyline", svg_content)
        self.assertIn("path", svg_content)

    def test_undo_stack_lifo(self):
        # Имитируем поведение стека отмены главного окна
        actions_done = []
        undo_stack = []
        is_undoing = False

        def push_undo(fn, desc):
            nonlocal is_undoing
            if is_undoing:
                return
            undo_stack.append({"desc": desc, "undo": fn})

        def undo():
            nonlocal is_undoing
            if not undo_stack:
                return False
            item = undo_stack.pop()
            is_undoing = True
            try:
                item["undo"]()
            finally:
                is_undoing = False
            return True

        # Выполняем действия
        actions_done.append("place_1")
        push_undo(lambda: actions_done.remove("place_1"), "Удалить вышку 1")

        actions_done.append("move_1")
        push_undo(lambda: actions_done.append("move_back_1"), "Переместить назад")

        self.assertEqual(len(undo_stack), 2)
        self.assertEqual(undo_stack[-1]["desc"], "Переместить назад")

        # Отменяем последнее действие
        self.assertTrue(undo())
        self.assertIn("move_back_1", actions_done)
        self.assertEqual(len(undo_stack), 1)

        # Отменяем первое действие
        self.assertTrue(undo())
        self.assertNotIn("place_1", actions_done)
        self.assertEqual(len(undo_stack), 0)

        # Больше нечего отменять
        self.assertFalse(undo())


if __name__ == "__main__":
    unittest.main()
