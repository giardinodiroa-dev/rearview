from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextEdit
from PyQt6.QtCore import pyqtSignal, Qt, QMetaObject, Q_ARG
from PyQt6.QtGui import QTextCursor, QTextCharFormat, QColor, QFont

from thefuzz import fuzz


class TeleprompterWidget(QWidget):
    position_updated = pyqtSignal(int)

    # Internal signal used to marshal update_position calls to the Qt thread.
    _update_position_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._script_words: list[str] = []
        self._current_pos: int = 0
        self._full_text: str = ""

        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setFixedHeight(140)
        self._text_edit.setWordWrapMode(
            __import__("PyQt6.QtGui", fromlist=["QTextOption"]).QTextOption.WrapMode.WordWrap
        )

        mono_font = QFont("Monospace")
        mono_font.setStyleHint(QFont.StyleHint.TypeWriter)
        mono_font.setPointSize(12)
        self._text_edit.setFont(mono_font)

        self._text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #1a1a1a;
                color: #dddddd;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._text_edit)

        # Connect internal signal so calls from non-Qt threads are safe.
        self._update_position_signal.connect(self._process_transcript_chunk)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_script(self, text: str, variables: dict) -> None:
        """Load script text, replacing {key} placeholders with values."""
        for key, value in variables.items():
            text = text.replace(f"{{{key}}}", str(value))
        self._full_text = text
        self._script_words = text.split()
        self._current_pos = 0
        self._text_edit.setPlainText(text)

    def update_position(self, transcript_chunk: str) -> None:
        """
        Called with new STT text; may be called from any thread.
        Marshals processing to the Qt thread via signal.
        """
        self._update_position_signal.emit(transcript_chunk)

    def reset(self) -> None:
        """Scroll to top and clear all highlights."""
        self._current_pos = 0
        self._clear_highlight()
        cursor = self._text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._text_edit.setTextCursor(cursor)

    def highlight_range(self, start_word: int, end_word: int) -> None:
        """Highlight words [start_word, end_word) in the script."""
        if not self._script_words:
            return
        start_word = max(0, start_word)
        end_word = min(end_word, len(self._script_words))
        if start_word >= end_word:
            return

        # Find character offset of start_word and end_word in _full_text.
        char_start = self._word_char_offset(start_word)
        char_end = self._word_char_offset(end_word)

        self._clear_highlight()

        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#fbbf24"))
        fmt.setForeground(QColor("#1a1a1a"))

        cursor = self._text_edit.textCursor()
        cursor.setPosition(char_start)
        cursor.setPosition(char_end, QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(fmt)

        # Scroll so highlighted text is visible.
        self._text_edit.setTextCursor(cursor)
        self._text_edit.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_transcript_chunk(self, transcript_chunk: str) -> None:
        """Runs on the Qt thread. Performs sliding-window fuzzy match."""
        if not self._script_words or not transcript_chunk.strip():
            return

        chunk_words = transcript_chunk.split()
        window_size = 8
        query = " ".join(chunk_words[-window_size:])

        best_score = 0
        best_pos = self._current_pos

        # Search forward from current position (allow slight backtrack of 4 words).
        search_start = max(0, self._current_pos - 4)
        search_end = len(self._script_words) - window_size + 1

        for i in range(search_start, max(search_end, search_start + 1)):
            window = " ".join(self._script_words[i: i + window_size])
            score = fuzz.partial_ratio(query, window)
            if score > best_score:
                best_score = score
                best_pos = i

        if best_score > 65:
            self._current_pos = best_pos
            match_end = min(best_pos + window_size, len(self._script_words))
            self.highlight_range(best_pos, match_end)
            self.position_updated.emit(self._current_pos)

    def _clear_highlight(self) -> None:
        """Remove all character formatting from the document."""
        cursor = self._text_edit.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#1a1a1a"))
        fmt.setForeground(QColor("#dddddd"))
        cursor.mergeCharFormat(fmt)

    def _word_char_offset(self, word_index: int) -> int:
        """Return the character offset in _full_text where word_index starts."""
        words = self._full_text.split()
        if word_index >= len(words):
            return len(self._full_text)

        pos = 0
        for i, word in enumerate(words):
            pos = self._full_text.find(word, pos)
            if i == word_index:
                return pos
            pos += len(word)
        return len(self._full_text)
