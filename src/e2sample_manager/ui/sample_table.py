"""Filterable, editable sample table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from e2sample_manager.services import LibraryService, ServiceError


class SampleTableModel(QAbstractTableModel):
    """Qt adapter around the current ``LibraryService`` library."""

    changed = Signal()
    error = Signal(str)
    HEADERS = (
        "Slot",
        "Name",
        "Kategorie",
        "One-shot",
        "+12 dB",
        "Tune",
        "Samplerate",
        "Kanäle",
        "Dauer",
        "Speicher",
    )

    def __init__(self, service: LibraryService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        # Keep the old rows alive until Qt has been notified of a model reset.
        self._samples = list(service.library.samples)
        self.revision = 0

    def rowCount(self, _parent: QModelIndex | None = None) -> int:
        return 0 if _parent is not None and _parent.isValid() else len(self._samples)

    def columnCount(self, _parent: QModelIndex | None = None) -> int:
        return 0 if _parent is not None and _parent.isValid() else len(self.HEADERS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ) -> Any:
        if role == Qt.DisplayRole:
            return self.HEADERS[section] if orientation == Qt.Horizontal else section + 1
        return None

    def sample_at(self, row: int):
        return self._samples[row] if 0 <= row < len(self._samples) else None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        sample = self.sample_at(index.row())
        if sample is None or not 0 <= index.column() < len(self.HEADERS):
            return None
        metadata = sample.metadata
        column = index.column()
        if role == Qt.UserRole:
            return metadata.slot
        if role == Qt.CheckStateRole and column in (3, 4):
            value = metadata.one_shot if column == 3 else metadata.plus_12db
            return Qt.Checked if value else Qt.Unchecked
        if role in (Qt.DisplayRole, Qt.EditRole):
            channels, rate, align = sample.wave.pcm_format
            values = (
                metadata.slot,
                metadata.name,
                metadata.category,
                "",
                "",
                metadata.tune,
                metadata.sample_rate,
                "STEREO" if channels == 2 else "MONO",
                f"{len(sample.audio_data) / align / rate:.2f} s",
                f"{sample.memory_size / 1024:.1f} KiB",
            )
            return values[column]
        if role == Qt.TextAlignmentRole and column != 1:
            return int(Qt.AlignCenter)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        flags = super().flags(index) | Qt.ItemIsDropEnabled
        if index.isValid():
            flags |= Qt.ItemIsDragEnabled
        if index.isValid() and index.column() in range(6):
            flags |= Qt.ItemIsEditable
        if index.column() in (3, 4):
            flags |= Qt.ItemIsUserCheckable
        return flags

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or not 0 <= index.column() < 6:
            return False
        sample = self.sample_at(index.row())
        if sample is None:
            return False
        slot = sample.metadata.slot
        slots_before = [item.metadata.slot for item in self._samples]
        fields = ("slot", "name", "category", "one_shot", "plus_12db", "tune", "sample_rate")
        field = fields[index.column()]
        if role == Qt.CheckStateRole and index.column() in (3, 4):
            value = value == Qt.Checked
        elif role != Qt.EditRole:
            return False
        try:
            if field == "name":
                value = str(value).encode("ascii", "replace").decode("ascii")[:16]
            elif field not in {"one_shot", "plus_12db"}:
                value = int(value)
            if field == "sample_rate":
                if value < 1:
                    raise ValueError
            self._samples[index.row()] = self.service.update_metadata(slot, **{field: value})
        except (ServiceError, ValueError, OverflowError) as exc:
            # A failed service operation restores its snapshot with new objects.
            self._samples = [self.service.require_sample(slot) for slot in slots_before]
            self.error.emit(str(exc) or "Ungültiger Wert.")
            return False
        # Preserve source row identities; the proxy handles sorting via dataChanged.
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), 9), [])
        self.changed.emit()
        return True

    def refresh(self) -> None:
        self.beginResetModel()
        self._samples = list(self.service.library.samples)
        self.revision += 1
        self.endResetModel()

    def supportedDropActions(self):
        return Qt.MoveAction | Qt.CopyAction


class SampleDelegate(QStyledItemDelegate):
    """Bind an open editor to its sample, even if proxy sorting changes its row."""

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if editor is not None:
            source = index.model().mapToSource(index)
            editor._sample = source.model().sample_at(source.row())
        return editor

    def setModelData(self, editor, model, index):
        sample = getattr(editor, "_sample", None)
        source = model.sourceModel()
        row = next((i for i, item in enumerate(source._samples) if item is sample), None)
        if row is None:
            return  # This editor belonged to an older document/undo snapshot.
        current = model.mapFromSource(source.index(row, index.column()))
        if current.isValid():
            super().setModelData(editor, model, current)


class AudioDropTableView(QTableView):
    audioDropped = Signal(list)
    moveRequested = Signal(list, int)
    SAMPLE_MIME = "application/x-electribe-studio-samples"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self._drag_slots = []
        self._drag_revision = None

    def startDrag(self, supported_actions) -> None:
        self._drag_slots = sorted(
            {
                self.model().data(index, Qt.UserRole)
                for index in self.selectionModel().selectedRows()
            }
        )
        if not self._drag_slots:
            return
        self._drag_revision = self.model().sourceModel().revision
        mime = QMimeData()
        mime.setData(self.SAMPLE_MIME, b"selection")
        drag = QDrag(self)
        drag.setMimeData(mime)
        try:
            drag.exec(Qt.MoveAction)
        finally:
            self._drag_slots = []
            drag.deleteLater()

    def _internal_drag(self, event) -> bool:
        return (
            event.source() is self
            and event.mimeData().hasFormat(self.SAMPLE_MIME)
            and bool(self._drag_slots)
            and self._drag_revision == self.model().sourceModel().revision
        )

    def dragEnterEvent(self, event) -> None:
        if self._internal_drag(event):
            event.acceptProposedAction()
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        if paths and all(
            path.suffix.lower() in {".wav", ".wave", ".aif", ".aiff", ".mp3"} for path in paths
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        if self._internal_drag(event):
            index = self.indexAt(event.position().toPoint())
            target = self.model().data(index, Qt.UserRole) if index.isValid() else -1
            slots = list(self._drag_slots)
            event.acceptProposedAction()
            # Finish Qt's drag operation before opening the preview or changing rows.
            QTimer.singleShot(0, lambda: self.moveRequested.emit(slots, target))
            return
        if event.mimeData().hasFormat(self.SAMPLE_MIME):
            event.ignore()
            return
        self.audioDropped.emit([url.toLocalFile() for url in event.mimeData().urls()])
        event.acceptProposedAction()


class SampleTableWidget(QWidget):
    audioDropped = Signal(list)
    selectionChanged = Signal()

    def __init__(self, service: LibraryService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.filter_edit = QLineEdit(
            placeholderText="Suchen: Sample-Name, Slot, MONO oder STEREO …"
        )
        self.filter_edit.setClearButtonEnabled(True)
        self.model = SampleTableModel(service, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.view = AudioDropTableView(self)
        self.view.setModel(self.proxy)
        self.view.setItemDelegate(SampleDelegate(self.view))
        self.view.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.view.setToolTip(
            "Strg/Umschalt: mehrere auswählen. Ziehen öffnet die Verschiebe-Vorschau."
        )
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, Qt.AscendingOrder)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(34)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.view)
        self.empty_label = QWidget()
        hint_layout = QVBoxLayout(self.empty_label)
        from PySide6.QtWidgets import QLabel

        hint = QLabel(
            "Samples hierher ziehen oder oben Audio importieren wählen.\n"
            "WAV · AIFF · MP3   |   Mehrfachauswahl mit Strg / Umschalt"
        )
        hint.setAlignment(Qt.AlignCenter)
        hint.setObjectName("subtitle")
        hint_layout.addWidget(hint)
        layout.addWidget(self.empty_label)
        self.filter_edit.textChanged.connect(self.proxy.setFilterFixedString)
        self.view.audioDropped.connect(self.audioDropped)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.selectionChanged.emit())

    def selected_slots(self) -> list[int]:
        rows = {index.row() for index in self.view.selectionModel().selectedRows()}
        samples = [
            self.model.sample_at(self.proxy.mapToSource(self.proxy.index(row, 0)).row())
            for row in rows
        ]
        return sorted(sample.metadata.slot for sample in samples if sample is not None)

    def current_sample(self):
        current = self.view.currentIndex()
        if not current.isValid():
            return None
        return self.model.sample_at(self.proxy.mapToSource(current).row())

    def refresh(self) -> None:
        self.model.refresh()

    def select_slots(self, slots) -> None:
        wanted = set(slots)
        selection = QItemSelection()
        first = None
        for row, sample in enumerate(self.model._samples):
            if sample.metadata.slot in wanted:
                index = self.proxy.mapFromSource(self.model.index(row, 0))
                if index.isValid():
                    selection.select(
                        index, self.proxy.index(index.row(), self.proxy.columnCount() - 1)
                    )
                    first = first if first is not None else index
        self.view.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)
        if first is not None:
            self.view.selectionModel().setCurrentIndex(first, QItemSelectionModel.NoUpdate)
            self.view.scrollTo(first)
