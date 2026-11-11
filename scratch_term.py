import sys, os
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *

class TerminalWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("font-family: monospace; background: #1e1e1e; color: #fff;")
        layout.addWidget(self.output)
        
        self.input = QLineEdit()
        self.input.setStyleSheet("font-family: monospace; background: #1e1e1e; color: #fff; border: 1px solid #333;")
        self.input.returnPressed.connect(self.run_cmd)
        layout.addWidget(self.input)
        
        self.process = QProcess(self)
        self.process.setProgram("/bin/bash")
        self.process.readyReadStandardOutput.connect(self.read_out)
        self.process.readyReadStandardError.connect(self.read_err)
        self.process.start()

    def run_cmd(self):
        cmd = self.input.text() + '\n'
        self.process.write(cmd.encode('utf-8'))
        self.output.appendPlainText(f"$ {self.input.text()}")
        self.input.clear()

    def read_out(self):
        self.output.appendPlainText(self.process.readAllStandardOutput().data().decode('utf-8').strip())
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())

    def read_err(self):
        self.output.appendPlainText(self.process.readAllStandardError().data().decode('utf-8').strip())
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())

if __name__ == '__main__':
    app = QApplication(sys.argv)
    t = TerminalWidget()
    t.show()
    sys.exit(app.exec())
