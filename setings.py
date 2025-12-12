# setings.py - РАБОЧИЙ ВАРИАНТ
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QTableView, QPushButton, QHeaderView
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from PyQt6 import uic
import sqlite3
import os


class SettingsWindow(QMainWindow):
    def __init__(self, parent_window=None):
        super().__init__()
        uic.loadUi('2.ui', self)

        self.parent_window = parent_window
        self.db_path = 'world_ru.db'  # ВАША БД!

        # Проверяем существование БД
        if not os.path.exists(self.db_path):
            QMessageBox.critical(self, "Ошибка",
                                 f"Файл БД '{self.db_path}' не найден!\n"
                                 f"Текущая папка: {os.getcwd()}")
            return

        print(f"Открываю БД: {self.db_path}")

        # Создаем модель
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(['Name', 'All_path'])

        # Устанавливаем модель в TableView
        self.tableView.setModel(self.model)

        # Настраиваем TableView
        self.tableView.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.tableView.setEditTriggers(QTableView.EditTrigger.AllEditTriggers)

        # Загружаем данные
        self.load_data()

        # Подключаем кнопки
        self.connect_buttons()

        print(f"Загружено строк: {self.model.rowCount()}")

    def load_data(self):
        """Загружает данные из БД"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Проверяем есть ли таблица Path
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='Path'")
            if not cursor.fetchone():
                QMessageBox.critical(self, "Ошибка",
                                     "В БД нет таблицы 'Path'!\n"
                                     "Таблицы в БД: " +
                                     str(cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()))
                conn.close()
                return

            # Получаем данные
            cursor.execute("SELECT Name, All_path FROM Path")
            rows = cursor.fetchall()
            conn.close()

            # Очищаем и заполняем модель
            self.model.removeRows(0, self.model.rowCount())
            for name, path in rows:
                name_item = QStandardItem(str(name))
                path_item = QStandardItem(str(path))
                self.model.appendRow([name_item, path_item])

            print(f"Получено {len(rows)} записей из БД")

        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка", f"Ошибка загрузки БД:\n{str(e)}")

    def connect_buttons(self):
        """Подключает кнопки"""
        self.save.clicked.connect(self.save_changes)
        self.cannel.clicked.connect(self.cancel_changes)

    def save_changes(self):
        """Сохраняет изменения"""
        reply = QMessageBox.question(
            self, 'Подтверждение',
            'Вы уверены, что хотите сохранить изменения?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                # Очищаем таблицу
                cursor.execute("DELETE FROM Path")

                # Вставляем данные из модели
                for row in range(self.model.rowCount()):
                    name_item = self.model.item(row, 0)
                    path_item = self.model.item(row, 1)

                    if name_item and path_item:
                        name = name_item.text().strip()
                        path = path_item.text().strip()

                        if name and path:
                            cursor.execute(
                                "INSERT INTO Path (Name, All_path) VALUES (?, ?)",
                                (name, path)
                            )

                conn.commit()
                conn.close()

                # Обновляем главное окно
                if self.parent_window and hasattr(self.parent_window, 'update_path_data'):
                    self.parent_window.update_path_data()

                # Закрываем окно
                self.close()
                if self.parent_window:
                    self.parent_window.show()

                QMessageBox.information(self, "Успех", "Изменения сохранены!")

            except Exception as e:
                QMessageBox.critical(
                    self, "Ошибка", f"Ошибка сохранения:\n{str(e)}")

    def cancel_changes(self):
        """Отменяет изменения"""
        reply = QMessageBox.question(
            self, 'Подтверждение',
            'Вы уверены, что хотите отменить изменения?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.close()
            if self.parent_window:
                self.parent_window.show()

    def closeEvent(self, event):
        """Обработчик закрытия окна"""
        if self.parent_window:
            self.parent_window.show()
        event.accept()
