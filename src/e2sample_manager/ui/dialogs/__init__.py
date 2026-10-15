"""Small application dialogs."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ImportOptionsDialog(QDialog):
    """Choose target slot and optional stereo-to-mono mix."""

    def __init__(
        self,
        *,
        first_slot: int = 501,
        mono: bool = False,
        left: float = 0.5,
        right: float = 0.5,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Audio importieren")
        self.first_slot = QSpinBox()
        self.first_slot.setRange(1, 1020)
        self.first_slot.setValue(first_slot)
        self.mono = QCheckBox("Stereo zu Mono mischen")
        self.mono.setChecked(mono)
        self.left = self._gain(left)
        self.right = self._gain(right)
        form = QFormLayout()
        form.addRow("Erster Slot", self.first_slot)
        form.addRow("", self.mono)
        form.addRow("Pegel links", self.left)
        form.addRow("Pegel rechts", self.right)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @staticmethod
    def _gain(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-2.0, 2.0)
        spin.setSingleStep(0.05)
        spin.setValue(value)
        return spin

    def options(self) -> dict[str, int | float | bool]:
        return {
            "first_slot": self.first_slot.value(),
            "mono": self.mono.isChecked(),
            "left": self.left.value(),
            "right": self.right.value(),
        }


class HacktribeArrangeDialog(QDialog):
    """Preview the destructive Hacktribe slot arrangement."""

    def __init__(self, slots: list[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Für Hacktribe anordnen")
        preview = "\n".join(
            f"{old:04d} → {501 + index:04d}" for index, old in enumerate(slots[:16])
        )
        if len(slots) > 16:
            preview += f"\n… und {len(slots) - 16} weitere"
        text = QLabel(
            "<b>Alle Samples werden ab Slot 501 lückenlos angeordnet.</b><br><br>"
            "Geladene Sampler-Patterns werden mit angepasst. Danach beide Dateien speichern. "
            "Nicht geöffnete Patterns bleiben unverändert.<br><br>"
            f"<pre>{preview or 'Keine Samples'}</pre>"
        )
        text.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Anordnen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(buttons)
