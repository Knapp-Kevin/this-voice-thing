"""Small reusable widgets: the eliding voice chip, labelled sliders and the level meter."""

from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QSizePolicy, QSlider, QWidget

from this_voice_thing.ui import theme as ui_theme


class ElidedLabel(QLabel):
    """Shows as much of its text as fits its width, ending in an ellipsis (full text in the
    tooltip), so long text never sets the window's minimum width."""

    def __init__(self, text=""):
        super().__init__()
        self.full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text):
        self.full_text = text or ""
        self.setToolTip(self.full_text)
        self._fit()

    def text(self):
        return self.full_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight,
                                                      max(0, self.width())))


class ElidingChip(QLabel):
    """A label that shortens long text with an ellipsis instead of widening the window."""

    MAX_WIDTH = 180
    PADDING = 28  # the chip's left/right padding and border in the stylesheet

    def __init__(self, text=""):
        super().__init__()
        self.setMaximumWidth(self.MAX_WIDTH)
        self.setText(text)

    def setText(self, text):
        self.full_text = text
        super().setText(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight,
                                                      self.MAX_WIDTH - self.PADDING))

    def text(self):
        return self.full_text


class SliderWithValue(QWidget):
    def __init__(self, min_val, max_val, step_val, default_val, value_format="{:.2f}"):
        super().__init__()
        self._step_val = step_val
        self._value_format = value_format
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        # round(), not int(): e.g. int(0.25 / 0.05) truncates to 4.
        self.slider.setMinimum(round(min_val / step_val))
        self.slider.setMaximum(round(max_val / step_val))
        self.slider.setValue(round(default_val / step_val))
        self.slider.setSingleStep(1)
        self.value_label = QLabel(value_format.format(default_val))
        widest = max((value_format.format(v) for v in (min_val, max_val)), key=len)
        self.value_label.setMinimumWidth(self.value_label.fontMetrics().horizontalAdvance(widest) + 6)
        self.slider.valueChanged.connect(
            lambda val: self.value_label.setText(self._value_format.format(val * self._step_val))
        )
        layout.addWidget(self.slider)
        layout.addWidget(self.value_label)

    def get_value(self):
        return self.slider.value() * self._step_val

    def set_value(self, value):
        self.slider.setValue(round(value / self._step_val))


def dialog_accepted(result):
    return int(getattr(result, "value", result)) == QDialog.DialogCode.Accepted.value


class LevelHistoryWidget(QWidget):
    """Scrolling bar graph of recent microphone peak levels."""

    def __init__(self, bars=72, parent=None):
        super().__init__(parent)
        self.levels = deque([0.0] * bars, maxlen=bars)
        self.active = False
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def push(self, level):
        self.levels.append(level)
        self.update()

    def set_active(self, active):
        self.active = active
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), self.palette().color(self.backgroundRole()).darker(108))
        if self.active:
            normal = self.palette().color(QPalette.ColorRole.Highlight)
        else:
            normal = self.palette().color(QPalette.ColorRole.Mid)
        bar_width = width / len(self.levels)
        middle = height / 2
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        theme = ui_theme.current()
        for index, level in enumerate(self.levels):
            # Square-root scaling so normal speech fills a useful part of the height.
            bar_height = max(2.0, min(1.0, level) ** 0.5 * (height - 6))
            top = middle - bar_height / 2
            if level >= 0.98:
                brush = QColor(theme["error_text"])  # clipping stays red
            elif self.active:
                brush = ui_theme.brand_gradient(0, 0, width, 0)  # the waveform wears the brand
            else:
                brush = normal
            painter.setBrush(brush)
            bar = max(1.0, bar_width - 2)
            painter.drawRoundedRect(index * bar_width + 1, top, bar, bar_height, bar / 2, bar / 2)
        painter.end()
