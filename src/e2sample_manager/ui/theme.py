"""Readable desktop theme with restrained colour and generous click targets."""

STYLE = """
QWidget { background: #111a27; color: #e4eaf3; font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow, QDialog { background: #111a27; }
QMenuBar, QMenu, QToolBar, QStatusBar { background: #172232; }
QToolBar { border: 0; spacing: 5px; padding: 7px; }
QToolButton, QPushButton { background: #26364b; border: 1px solid #3a4b63;
    border-radius: 5px; padding: 7px 12px; }
QToolButton:hover, QPushButton:hover { background: #30475f; border-color: #5fc8bb; }
QToolButton:disabled, QPushButton:disabled { color: #69798e;
    background: #1b2737; border-color: #29384b; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
    background: #1c293b; border: 1px solid #3b4d65; border-radius: 4px; padding: 5px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #5fc8bb; }
QTableView { background: #182231; alternate-background-color: #1d2a3c;
    gridline-color: #2d3d52; selection-background-color: #34556b; border: 1px solid #2d3d52; }
QHeaderView::section { background: #223147; padding: 7px; border: 0;
    border-bottom: 1px solid #3a4b63; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #172232; color: #a9b8ca; padding: 13px 32px; margin-right: 4px; }
QTabBar::tab:selected { background: #26384a; color: #7ce3d3; border-bottom: 3px solid #5fc8bb; }
QProgressBar { background: #29394b; border: 0; border-radius: 3px; min-height: 8px; }
QProgressBar::chunk { background: #5fc8bb; border-radius: 3px; }
QLabel#appTitle { font-size: 23pt; font-weight: 600; }
QLabel#subtitle { color: #9daec4; }
QLabel#sectionTitle { color: #7ce3d3; font-weight: 600; padding-top: 9px; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QScrollBar:vertical { background: #172232; width: 12px; }
QScrollBar::handle:vertical { background: #4b607a; min-height: 25px; }
QToolTip { background: #26384a; color: #ffffff; border: 1px solid #5fc8bb; }
"""


def apply_theme(app):
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
