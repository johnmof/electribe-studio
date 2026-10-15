"""Preview an all-or-nothing move without overwriting occupied slots."""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from e2sample_manager.services import ServiceError

from .pattern_editor import spin


class MoveRangeDialog(QDialog):
    def __init__(self, service, selected, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Sample-Bereich verschieben")
        self.resize(580, 460)
        self.start = spin(1, 1020, min(selected) if selected else 501)
        self.end = spin(1, 1020, max(selected) if selected else 600)
        self.target = spin(1, 1020, 501)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Die Slot-Abstände bleiben erhalten. Freie Stellen im Quellbereich bleiben frei. "
            "Belegte Ziele werden nicht überschrieben."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        for title, widget in (
            ("Von Slot", self.start),
            ("Bis Slot (einschließlich)", self.end),
            ("Neuer Startslot", self.target),
        ):
            form.addRow(title, widget)
            widget.valueChanged.connect(self.preview)
        layout.addLayout(form)
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        layout.addWidget(self.preview_text)
        self.note = QLabel(
            "Geladene Sampler-Patterns werden automatisch angepasst. "
            "Danach Samples UND Patterns speichern. Nicht geöffnete Patterns bleiben unverändert."
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Bereich verschieben")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.preview()

    def values(self):
        return self.start.value(), self.end.value(), self.target.value()

    def preview(self):
        try:
            mapping = self.service.plan_move(*self.values())
            lines = [
                f"{old:03d}  →  {new:03d}    {self.service.require_sample(old).metadata.name}"
                for old, new in mapping.items()
            ]
            self.preview_text.setPlainText(f"{len(mapping)} Samples\n\n" + "\n".join(lines))
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(
                any(a != b for a, b in mapping.items())
            )
        except ServiceError as exc:
            self.preview_text.setPlainText(str(exc))
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
