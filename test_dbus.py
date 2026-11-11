from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtWidgets import QApplication
import sys

app = QApplication(sys.argv)
bus = QDBusConnection.sessionBus()
msg = QDBusMessage.createMethodCall("org.kde.KIOFuse", "/org/kde/KIOFuse", "org.kde.KIOFuse.VFS", "mountUrl")
msg.setArguments(["smb://"])
reply = bus.call(msg)
if reply.type() == QDBusMessage.ReplyMessage:
    print("SUCCESS:", reply.arguments()[0])
else:
    print("ERROR:", reply.errorMessage())
