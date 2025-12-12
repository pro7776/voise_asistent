import speech_recognition as sr
import pyaudio
import random
import wave
import audioop
import pyautogui
import sqlite3
import threading
import subprocess
import time
import sys
from PyQt6.QtCore import QTimer
from PyQt6 import uic
import numpy as np
from PyQt6.QtGui import QPixmap, QImage, QTransform
from PyQt6.QtCore import Qt, QRect, QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QApplication, QFileDialog
from pynput import mouse
import os
import re
import webbrowser
from setings import SettingsWindow


# Параметры записи
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

SILENCE_THRESHOLD = 500
SILENCE_DURATION = 2
OUTPUT_FILENAME = "output.wav"


class VoiceThread(QThread):
    status_changed = pyqtSignal(str)
    command_received = pyqtSignal(str, str)
    error_occurred = pyqtSignal(str)  # Новый сигнал для ошибок

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.recognizer = sr.Recognizer()
        self.running = True
        self.db_data = {}
        self.content = None
        self._wake_word_lock = threading.Lock()
        # Блокировка для аудио операций # Текущая активная операция (shutdown, sleep, restart)
        self._audio_lock = threading.Lock()
        self.active_action = None
        self.action_cancelled = False

    def run(self):
        """Запускает прослушивание только если разрешено"""
        while self.running:
            if self.parent and not self.parent.close_requested:
                time.sleep(0.5)
                continue  # ждём разрешения

            try:
                self.listen_for_wake_word()
            except Exception as e:
                self.error_occurred.emit(f"Критическая ошибка в потоке: {e}")
                time.sleep(2)

    def listen_for_wake_word(self):
        """Прослушивает ключевое слово с улучшенной обработкой ошибок"""
        self.wake_word = self.db_data.get("name")
        self.greeting()
        words_entrance = self.db_data["words_entrance"].split(",")
        # Ограничиваем количество попыток переподключения
        max_reconnect_attempts = 3
        reconnect_attempts = 0

        while self.running and reconnect_attempts < max_reconnect_attempts:
            try:
                with sr.Microphone() as source:
                    self.recognizer.adjust_for_ambient_noise(
                        source, duration=1)
                    reconnect_attempts = 0  # Сброс счетчика при успешном подключении

                    while self.running:
                        try:
                            with self._audio_lock:
                                if not self.running:
                                    break
                                try:
                                    debug_filename = f"out.wav"

                                    audio = self.recognizer.listen(
                                        source, timeout=2, phrase_time_limit=4)

                                    with open(debug_filename, "wb") as f:
                                        f.write(audio.get_wav_data())
                                except sr.WaitTimeoutError:
                                    if not self.running:
                                        break
                                    continue
                            text = self.recognizer.recognize_google(
                                audio, language=f"{self.content.lower()}-{self.content.upper()}").lower()
                            text_2 = self.recognizer.recognize_google(
                                audio, language="en-EN").lower()

                            with self._wake_word_lock:
                                current_wake_word = self.wake_word

                            if current_wake_word in text or current_wake_word in text_2:
                                random_phrase = random.choice(words_entrance)
                                self.status_changed.emit(
                                    f"{random_phrase}{self.db_data.get("helping")}")
                                time.sleep(1)
                                self.record_command()

                        except sr.WaitTimeoutError:
                            continue
                        except sr.UnknownValueError:
                            continue
                        except Exception as e:
                            self.error_occurred.emit(
                                f"Ошибка распознавания: {e}")
                            time.sleep(0.1)  # Небольшая пауза при ошибках
                            continue

                        if not self.running:
                            break
                        time.sleep(0.05)

            except OSError as e:
                reconnect_attempts += 1
                self.error_occurred.emit(
                    f"Проблема с микрофоном. Попытка {reconnect_attempts}/{max_reconnect_attempts}: {e}")
                time.sleep(2)  # Пауза перед повторной попыткой
            except Exception as e:
                self.error_occurred.emit(f"Неожиданная ошибка: {e}")
                break

        if reconnect_attempts >= max_reconnect_attempts:
            self.error_occurred.emit(
                "Не удалось подключиться к микрофону после нескольких попыток")

    def greeting(self):
        greeting = self.db_data.get("greeting")
        if int(greeting) == 0:
            hello = self.db_data.get("hello")
            self.status_changed.emit(
                f"{hello} {self.wake_word.capitalize()}")
        else:
            self.status_changed.emit(
                f"Я рада вас видеть снова. Если понадоблюсь, только скажите.")
            return

    def pause(self):
        """Приостанавливает прослушивание"""
        self.running = False
        print("⏸️ VoiceThread приостановлен")

    def resume(self):
        """Возобновляет прослушивание"""
        if not self.running:
            self.running = True
            print("▶️ VoiceThread возобновлён")
            self.start()

    def record_command(self):
        """Записывает команду после ключевого слова с улучшенной обработкой ошибок"""
        p = None
        stream = None
        try:
            p = pyaudio.PyAudio()
            stream = p.open(format=FORMAT,
                            channels=CHANNELS,
                            rate=RATE,
                            input=True,
                            frames_per_buffer=CHUNK)

            frames = []
            silent_chunks = 0
            recording = True
            max_recording_time = 30  # Максимальное время записи в секундах
            start_time = time.time()

            while recording and self.running and (time.time() - start_time) < max_recording_time:
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(data)

                    # Проверяем уровень звука
                    rms = audioop.rms(data, 2)

                    if rms < SILENCE_THRESHOLD:
                        silent_chunks += 1
                    else:
                        silent_chunks = 0

                    # Останавливаем запись после тишины
                    if silent_chunks > (SILENCE_DURATION * RATE / CHUNK):
                        recording = False

                except IOError as e:
                    self.error_occurred.emit(f"Ошибка чтения аудио: {e}")
                    break

            if self.running and frames:
                self.save_and_recognize(frames)

        except Exception as e:
            self.error_occurred.emit(f"Ошибка записи команды: {e}")
        finally:
            # Гарантированное освобождение ресурсов
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except:
                    pass
            if p:
                try:
                    p.terminate()
                except:
                    pass

    def save_and_recognize(self, frames):
        """Сохраняет и распознает записанную команду"""
        try:
            with wave.open(OUTPUT_FILENAME, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # Фиксированная ширина для paInt16
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))

            try:
                with sr.AudioFile(OUTPUT_FILENAME) as source:
                    audio_data = self.recognizer.record(source)
                    text = self.recognizer.recognize_google(
                        audio_data, language="ru-RU")
                    text_2 = self.recognizer.recognize_google(
                        audio_data, language="en-EN")
                    print(text_2)
                    print(text)
                    self.command_received.emit(text, text_2)

            except sr.UnknownValueError:
                self.status_changed.emit(f"{self.db_data.get("command")}")
            except sr.RequestError as e:
                self.status_changed.emit(f"Ошибка сервиса распознавания: {e}")

        except Exception as e:
            self.error_occurred.emit(f"Ошибка сохранения/распознавания: {e}")

    def stop(self):
        """Останавливает поток"""
        self.running = False


class VoiceAssistant(QMainWindow):
    choose_directory_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        uic.loadUi('1.ui', self)
        self.setWindowTitle("Джарвис")
        self.data = {}
        self.path = {}
        self.voice_thread = VoiceThread(self)
        self.lang.hide()
        self.yes.hide()
        self.no.hide()
        self.lineEdit.hide()
        self.voise_name = False
        self.yes_2.hide()
        self.no_2.hide()
        self.content = None
        if os.path.exists("language.txt"):
            with open("language.txt", 'r') as file:
                self.content = file.read().strip()
            self.voice_thread.content = self.content
            self.connect = sqlite3.connect(f"world_{self.content}.db")
            cursor = self.connect.cursor()
            cursor.execute("SELECT Name_command, Text FROM Worlds")
            results = cursor.fetchall()
            self.data = {str(name).strip(): str(text)
                         for name, text in results}
            self.voice_thread.db_data = self.data
            self.update_path_data()
            cursor.execute("SELECT Name_1, Name_2 FROM Names")
            results = cursor.fetchall()
            self.name = {str(Name_1).strip(): str(Name_2)
                         for Name_1, Name_2 in results}
            self.connect.close()
            self.wake_word = self.data.get("name")
            print(self.path)
            if int(self.data["greeting"]) == 0:
                self.yes.clicked.connect(self.confirm)
                self.no.clicked.connect(self.cancel)
                self.lang.addItem("Selected language", None)
                self.lang.addItem("Русский", "ru")
                self.lang.addItem("English", "en")
                self.langue()
            else:
                self.voice_thread.start()
        else:
            self.label.hide()
            self.yes.hide()
            self.no.hide()
            self.yes.clicked.connect(self.confirm)
            self.no.clicked.connect(self.cancel)
            self.lang.addItem("Selected language", None)
            self.lang.addItem("Русский", "ru")
            self.lang.addItem("English", "en")
            self.langue()

        self.conf = None
        self.close_requested = True
        self.choose_directory_signal.connect(self.open_directory_dialog)
        self.awaiting_confirmation = False
        self.pending_action = None
        self.action_name.triggered.connect(self.record_new_name_threaded)
        self.action_help.triggered.connect(self.action_helping)
        self.action_path.triggered.connect(self.open_settings)

        self.yes_2.clicked.connect(self.on_yes_clicked)
        self.no_2.clicked.connect(self.on_no_clicked)

        self.label.setMouseTracking(True)

        self.label.mousePressEvent = self.on_label_click

        # Создаем и запускаем поток для голосового ассистента
        self.voice_thread.status_changed.connect(self.label.setText)
        self.voice_thread.command_received.connect(self.process_command)
        self.voice_thread.error_occurred.connect(
            self.handle_error)  # Обработчик ошибок
        self.action_path.setShortcut("Ctrl+S")

    def on_label_click(self, event):
        """Обработка клика по label"""
        # Проверяем ЛЕВУЮ кнопку мыши
        if event.button() == Qt.MouseButton.LeftButton:
            self.label.setText("Ай")

        super(type(self.label), self.label).mousePressEvent(event)

    def update_path_data(self):
        """ОБНОВЛЯЕТ данные из БД (вызывается при запуске и после сохранения)"""
        print("\n" + "="*60)
        print("Обновление данных из БД...")

        try:
            conn = sqlite3.connect(f"world_{self.content}.db")
            cursor = conn.cursor()
            cursor.execute("SELECT Name, All_path FROM Path")
            results = cursor.fetchall()

            # Обновляем словарь
            self.path = {str(name).strip(): str(text).strip()
                         for name, text in results}

            conn.close()

            # Выводим для отладки
            print(f"Загружено {len(self.path)} записей:")
            for name, path in self.path.items():
                print(f"  {name}: {path}")

            print("="*60)

        except Exception as e:
            print(f"Ошибка обновления данных: {e}")

    def record_new_name_threaded(self):
        """Запускает record_new_name в отдельном потоке"""
        threading.Thread(
            target=self.record_new_name,
            daemon=True
        ).start()

    def greeting(self):
        self.connect = sqlite3.connect(f"world_{self.current_language}.db")
        cursor = self.connect.cursor()
        self.voice_thread.start()
        cursor.execute(
            "UPDATE Worlds SET Text = ? WHERE Name_command = 'greeting'",
            (str(1),)
        )
        self.connect.commit()
        self.connect.close()
        self.data["greeting"] = 1
        self.voice_thread.db_data = self.data
        self.wake_word = self.data.get("name")
        with open("language.txt", "w") as file:
            file.write(f"{self.current_language}\n")
        time.sleep(1)
        self.voice_thread.start()

    def confirm(self):
        """Подтверждение выбора языка"""
        self.current_language = None

        if hasattr(self, 'pending_language'):
            self.current_language = self.pending_language
            self.lang.hide()
            self.yes.hide()
            self.no.hide()
            self.label.show()
            self.greeting()

        # Очищаем временные переменные
        del self.pending_language
        del self.pending_language_text

    def cancel(self):
        """Отмена выбора языка"""
        self.label.hide()
        self.yes.hide()
        self.no.hide()
        self.lang.show()

        # Сбрасываем выбор в ComboBox
        self.lang.setCurrentIndex(0)

        # Очищаем временные переменные
        if hasattr(self, 'pending_language'):
            del self.pending_language
            del self.pending_language_text

    def langue(self):
        """Инициализация языка"""
        # Устанавливаем заголовок как начальный выбор
        self.lang.show()
        self.lang.setCurrentIndex(0)
        self.lang.currentIndexChanged.connect(self.on_language_changed)

    def on_language_changed(self):
        """Обработчик изменения языка"""
        selected_language = self.lang.currentData()

        # Игнорируем заголовок "Selected language"
        if selected_language is None:
            return

        if selected_language:
         # Сохраняем выбранный язык во временные переменные
            self.pending_language = selected_language
            self.pending_language_text = self.lang.currentText()

        # Показываем подтверждение
        self.lang.hide()
        self.label.show()
        self.label.setText(f"Выбрать {self.pending_language_text}?")
        self.yes.show()
        self.no.show()

    def handle_error(self, error_message):
        """Обрабатывает ошибки из потока"""
        print(f"Error: {error_message}")
        self.label.setText(f"Error: {error_message}")

    def get_available_drives(self):
        """Получает список всех доступных дисков в системе"""
        drives = []

        # Для Windows
        if os.name == 'nt':
            import string
            for drive_letter in string.ascii_uppercase:
                drive_path = f"{drive_letter}:\\"
                if os.path.exists(drive_path):
                    drives.append(drive_path)

        # Для Linux/Mac
        else:
            # Проверяем стандартные точки монтирования
            common_mounts = ['/', '/home', '/mnt', '/media']
            for mount in common_mounts:
                if os.path.exists(mount):
                    drives.append(mount)

        return drives

    def find_folder_or_file(self, name_to_find):
        """Умный поиск файлов и папок на всех дисках"""
        search_key = name_to_find.lower().strip()
        print(f"🔎 НАЧИНАЕМ ПОИСК: '{name_to_find}'")

        # 1. Сначала проверяем кэш
        cached_path = self.load_path_from_file(search_key)
        if cached_path and os.path.exists(cached_path):
            print(f"✅ Используется кэшированный путь: {cached_path}")
            return cached_path

        # 2. Получаем все диски
        all_drives = self.get_available_drives()
        print(f"📀 Диски для поиска: {all_drives}")

        found_paths = []

        for drive in all_drives:
            print(f"🔍 Поиск на диске {drive}...")
            try:
                drive_paths = self.search_on_drive(drive, name_to_find)
                found_paths.extend(drive_paths)
                print(
                    f"📊 Найдено на диске {drive}: {len(drive_paths)} результатов")

                # Если нашли хорошие результаты, можно остановиться
                if len([p for p in found_paths if p[1] > 50]) >= 2:
                    break

            except Exception as e:
                print(f"⚠️ Ошибка на диске {drive}: {e}")

        # 3. Выбираем лучший результат
        if found_paths:
            best_path = self.select_best_match(found_paths, name_to_find)
            # Сохраняем только лучший
            self.save_paths_to_file(search_key, [best_path])
            print(f"✅ НАЙДЕН ЛУЧШИЙ ПУТЬ: {best_path}")
            return best_path

        print(f"❌ Не найдено: '{name_to_find}'")
        return None

    def is_system_directory(self, dir_name, full_path):
        """Проверяет, является ли папка системной (исключаем из поиска)"""
        system_keywords = {
            'windows', 'system32', 'syswow64', 'programdata', 'recovery',
            '$recycle.bin', 'system volume information', 'temp', 'tmp',
            'cache', 'logs', 'log files', 'prefetch', 'appdata', 'local settings',
            'microsoft', 'adobe', 'google', 'mozilla', 'temp', 'tmp'
        }

        dir_lower = dir_name.lower()
        path_lower = full_path.lower()

        # Исключаем папки с системными ключевыми словами
        if any(keyword in dir_lower for keyword in system_keywords):
            return True

        # Исключаем скрытые и системные папки
        try:
            if os.stat(path_lower).st_file_attributes & (2 | 4):  # Скрытый или системный
                return True
        except:
            pass

        return False

    def search_on_drive(self, drive, name_to_find):
        """Поиск на конкретном диске с безопасной оценкой релевантности"""
        found_paths = []

        try:
            for root, dirs, files in os.walk(drive):
                # Фильтруем системные папки
                dirs[:] = [d for d in dirs if not self.is_system_directory(
                    d, os.path.join(root, d))]

                # Проверяем папки
                for dir_name in dirs:
                    if self.is_match(dir_name, name_to_find):
                        full_path = os.path.join(root, dir_name)
                        score = int(self.calculate_match_score(
                            dir_name, name_to_find, full_path))
                        found_paths.append((full_path, score, "folder"))
                        print(f"📁 Папка [{score}]: {dir_name} -> {full_path}")

                # Проверяем файлы (.exe, .lnk, .bat, .msi, .url)
                for file_name in files:
                    ext = os.path.splitext(file_name)[1].lower()
                    if ext in ('.exe', '.lnk', '.bat', '.msi', '.url'):
                        if self.is_match(file_name, name_to_find):
                            full_path = os.path.join(root, file_name)
                            score = int(self.calculate_match_score(
                                file_name, name_to_find, full_path))
                            found_paths.append((full_path, score, "file"))
                            print(
                                f"📄 Файл [{score}]: {file_name} -> {full_path}")

        except (PermissionError, OSError) as e:
            print(f"🚫 Нет доступа: {drive} - {e}")
        except Exception as e:
            print(f"❌ Ошибка: {drive} - {e}")

        # Фильтруем только те пути, у которых score это int
        valid_paths = [p for p in found_paths if isinstance(p[1], int)]

        # Сортируем по убыванию score
        valid_paths.sort(key=lambda x: x[1], reverse=True)

        return [path for path, score, type in valid_paths[:10]]

    def split_words(self, text: str):
        """Разбивает строку на слова: буквы/цифры + CamelCase"""
        # Разбиваем по заглавным буквам и цифрам
        text = re.sub(r'([a-z])([A-Z])', r'\1 \2',
                      text)  # camelCase → camel Case
        text = re.sub(r'([A-Za-z])(\d)', r'\1 \2', text)  # word123 → word 123
        text = re.sub(r'(\d)([A-Za-z])', r'\1 \2', text)  # 123word → 123 word
        # Дальше режем на слова
        return re.findall(r'[a-zа-я0-9]+', text.lower())

    def is_match(self, item_name, search_name):
        item_words = set(self.split_words(os.path.splitext(item_name)[0]))
        search_words = set(self.split_words(search_name))

        # 1. Точное совпадение
        if " ".join(item_words) == " ".join(search_words):
            return True

        # 2. Все слова из поиска должны быть в имени
        if search_words.issubset(item_words):
            return True

        return False

    def calculate_match_score(self, item_name, search_name, full_path):
        score = 0
        item_words = set(self.split_words(os.path.splitext(item_name)[0]))
        search_words = set(self.split_words(search_name))
        path_lower = full_path.lower()

        # Тип файла
        if os.path.isdir(full_path):
            score += 10
        elif full_path.endswith('.exe'):
            score += 50  # .exe теперь приоритетнее
        elif full_path.endswith('.lnk'):
            score += 30

        # Совпадение по словам
        if item_words == search_words:
            score += 100
        elif search_words.issubset(item_words):
            score += 70
        elif any(word in item_words for word in search_words):
            score += 40

        # Хорошее расположение
        good_locations = ['program files', 'games', 'steam', 'desktop']
        if any(loc in path_lower for loc in good_locations):
            score += 30

        # Бонус за .exe в глубине игры
        if full_path.endswith("dota2.exe"):
            score += 200  # специально для главного exe игры

        return score

    def select_best_match(self, found_paths, search_name):
        """Выбирает лучший результат из найденных"""
        if not found_paths:
            return None

        # Создаем список с оценками
        scored_paths = []
        for path in found_paths:
            score = self.calculate_match_score(
                os.path.basename(path), search_name, path)
            scored_paths.append((path, score))

        # Сортируем по убыванию релевантности
        scored_paths.sort(key=lambda x: x[1], reverse=True)

        # Возвращаем самый релевантный
        return scored_paths[0][0]

    def load_path_from_file(self, key):
        """Загружает путь из файла по ключу (регистронезависимо)"""
        try:
            if hasattr(self, 'path') and self.path:
                for k, v in self.path.items():
                    # РЕГИСТРОНЕЗАВИСИМОЕ сравнение ключей
                    if k.strip().lower() == key.strip().lower():
                        return v.split("|")[0].strip()
            return None
        except Exception as e:
            print(f"Ошибка загрузки пути: {e}")
            return None

    def save_paths_to_file(self, key, paths):
        """Сохраняет пути в файл в формате ключ:значение"""
        try:

            self.path[key.lower()] = (key, "|".join(paths))
            self.connect = sqlite3.connect(
                f"world_{self.content}.db")
            cursor = self.connect.cursor()
            cursor.execute(
                "UPDATE Path SET All_path = ? WHERE Name = ?", ("|".join(
                    paths), key.lower())
            )
            self.connect.commit()
            cursor.execute("SELECT Name, All_path FROM Path")
            results = cursor.fetchall()
            self.path = {str(name).strip(): str(text)
                         for name, text in results}
            cursor.execute("SELECT Name_1, Name_2 FROM Names")
            results = cursor.fetchall()
            self.name = {str(Name_1).strip(): str(Name_2)
                         for Name_1, Name_2 in results}
            self.connect.close()

            print(f"Сохранено в кэш: {key} -> {len(paths)} путей")

        except Exception as e:
            print(f"Ошибка сохранения путей: {e}")

    def confirmation(self):
        recognizer = sr.Recognizer()
        QApplication.processEvents()
        while True:
            try:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(
                        source, duration=0.5)
                    audio = recognizer.listen(
                        source, timeout=15, phrase_time_limit=3)
                    text = recognizer.recognize_google(
                        audio, language=f"{self.content.lower()}-{self.content.upper()}").lower()
                    if self.data["world_conf"].split(',')[0] in text and self.data["world_conf"].split(',')[1] in text:
                        self.label.setText("Не верное подтверждение")
                        text = None
                    else:
                        if text in self.data["world_conf"].split(','):
                            return text
            except sr.UnknownValueError:
                continue
            except sr.WaitTimeoutError:
                self.label.setText("Вы долго не отвечали, поэтому я отменила")
                return 'cancel'
            except sr.RequestError as e:
                self.label.setText(f"Ошибка сервиса: {e}")
                return None

    def run_delayed_action(self, action_type, delay_seconds, action_function):
        self.close_requested = False
        """Универсальный метод для выполнения действий с задержкой и возможностью голосовой отмены"""
        self.active_action = action_type
        self.action_cancelled = False
        self.anywest = None
        self.voice_thread.pause()

        def listen_for_yea():
            self.label.setText("Вы уверены?")
            self.anywest = None
            while not self.action_cancelled and self.active_action:
                try:
                    while self.anywest not in self.data["world_conf"].split(','):
                        self.anywest = self.confirmation()
                        if self.anywest == self.data["world_conf"].split(',')[1]:
                            self.label.setText(
                                f"{action_type.capitalize()} через {delay_seconds} секунд. Скажите 'отмена' для отмены."
                            )

                            def listen_for_cancel():
                                recognizer = sr.Recognizer()
                                while not self.action_cancelled and self.active_action:
                                    try:
                                        with sr.Microphone() as source:
                                            recognizer.adjust_for_ambient_noise(
                                                source, duration=0.5)
                                            audio = recognizer.listen(
                                                source, timeout=3, phrase_time_limit=3)
                                            text = recognizer.recognize_google(
                                                audio, language=f"{self.content.lower()}-{self.content.upper()}").lower()
                                            if any(word in text for word in ["отмена", "отмени", "стоп", "cancel"]):
                                                self.cancel_shutdown()
                                                return
                                    except sr.WaitTimeoutError:
                                        continue
                                    except sr.UnknownValueError:
                                        continue
                                    except Exception as e:
                                        print(
                                            f"Ошибка прослушивания отмены: {e}")
                                        break

                            def countdown():
                                for i in range(delay_seconds, 0, -1):
                                    if self.action_cancelled:
                                        self.label.setText(
                                            f"✅ {action_type.capitalize()} отменено.")
                                        self.active_action = None
                                        return
                                    self.label.setText(
                                        f"{action_type.capitalize()} через {i} секунд... (Скажите 'отмена')")
                                    time.sleep(1)

                                if not self.action_cancelled:
                                    action_function()
                                    self.active_action = None

                            threading.Thread(
                                target=listen_for_cancel, daemon=True).start()
                            threading.Thread(target=countdown,
                                             daemon=True).start()
                        elif self.anywest == self.data["world_conf"].split(',')[0]:
                            self.label.setText("отменено.")
                            self.voice_thread.resume()
                            return
                        elif self.anywest == None:
                            continue

                except sr.WaitTimeoutError:
                    continue
                except sr.UnknownValueError:
                    continue
                except Exception as e:
                    print(f"Ошибка прослушивания отмены: {e}")
                    break

        # 🔹 Запускаем оба потока параллельно
        threading.Thread(target=listen_for_yea, daemon=True).start()
        self.close_requested = True

    def action_helping(self):
        help_text = "Доступные команды:\n- 'Время' - узнать текущее время\n- 'Поменять имя' - изменить имя ассистента\n- 'Стоп' или 'Выход' - завершить работу\n- 'настроить папку' - настраивает папку для открытия"
        self.label.setText(help_text)

    def process_command(self, command, command_en):
        """Обрабатывает распознанную команду в главном потоке"""
        self.command = command.lower()
        self.command_en = command_en.lower()
        self.label.setText(f"Обрабатываю команду: {self.command}")

        if any(word in self.command for word in ["отмена", "отмени", "отменить", "стоп", "cancel"]):
            self.cancel_shutdown()
            return

        elif "время" in self.command or "времени" in self.command:
            import datetime
            current_time = datetime.datetime.now().strftime("%H:%M")
            self.label.setText(f"Сейчас {current_time}")

        elif "поменять имя" in self.command:
            threading.Thread(target=self.record_new_name, daemon=True).start()

        elif "команды" in self.command or "помощь" in self.command or "Help" in self.command_en:
            help_text = "Доступные команды:\n- 'Время' - узнать текущее время\n- 'Поменять имя' - изменить имя ассистента\n- 'Стоп' или 'Выход' - завершить работу\n- 'настроить папку' - настраивает папку для открытия"
            self.label.setText(help_text)

        elif "настроить папку" in command or "настроить папки" in self.command:
            self.label.setText(f"Скажите название папки")
            threading.Thread(
                target=self.record_new_direct, daemon=True).start()

        elif "открой" in self.command or "запусти" in self.command:
            self.open_file()

        elif any(word in self.command for word in ["выключи компьютер", "выключи пк", "заверши работу компьютера"]):
            delay = 30  # стандартная задержка
            if "через" in self.command:
                # Пытаемся извлечь число из команды
                import re
                numbers = re.findall(r'\d+', self.command)
                if numbers:
                    delay = int(numbers[0])
                    # Если указаны минуты, конвертируем в секунды
                if any(word in self.command for word in ["минут", "минуты"]):
                    delay = delay * 60

                self.shutdown_computer(delay)
            else:
                self.shutdown_computer(delay)

        elif any(word in self.command for word in ["перезагрузи компьютер", "перезагрузи пк"]):
            delay = 30  # стандартная задержка
            if "через" in self.command:
                # Пытаемся извлечь число из команды
                import re
                numbers = re.findall(r'\d+', self.command)
                if numbers:
                    delay = int(numbers[0])
                    # Если указаны минуты, конвертируем в секунды
                if any(word in self.command for word in ["минут", "минуты"]):
                    delay = delay * 60

                self.restart_computer(delay)
            else:
                self.restart_computer(delay)

        elif any(word in self.command for word in ["сон", "спящий режим", "режим сна", "усни", "засни"]):

            # Проверяем, указано ли время
            delay = 30  # стандартная задержка
            if "через" in self.command:
                # Пытаемся извлечь число из команды
                import re
                numbers = re.findall(r'\d+', self.command)
                if numbers:
                    delay = int(numbers[0])
                    # Если указаны минуты, конвертируем в секунды
                if any(word in self.command for word in ["минут", "минуты"]):
                    delay = delay * 60

                self.sleep_computer(delay)
            else:
                self.sleep_computer(delay)

        elif 'рестарт' in self.command:
            self.restart_voice_thread()

        elif "стоп" in self.command or "выход" in self.command:
            self.label.setText("Завершаю работу...")
            self.close()

        else:
            self.label.setText(f"Команда '{self.command}' не распознана")

    def open_file(self):
        self.close_requested = False
        self.voice_thread.pause()
        if "игру" in self.command:
            if "игры" in self.path.keys():
                index_command = self.command.index("игру") + len("игру")
                game_name_1 = self.command[index_command:].strip()
                games_folder = self.path.get("игры")
                path_to_game = None
                clos = True
                go = True

                if game_name_1.lower() in self.name.keys():
                    found_key = self.name.get(game_name_1.lower())
                    cached_path = self.load_path_from_file(found_key.lower())
                else:
                    cached_path = self.load_path_from_file(game_name_1.lower())

                if cached_path:
                    print(f"✅ Используем кэшированный путь: {cached_path}")
                    self.run_executable(cached_path)
                    self.label.setText("Сделано")
                else:
                    self.label.setText(
                        f"Вы впервые запускаете эту игру, я правильно услышала?{game_name_1}")
                    conf = False

                    while not conf:
                        conf = self.confirmation()
                        if conf == 'cancel':
                            self.close_requested = True
                            self.voice_thread.resume()
                            return

                    if conf == self.data["world_conf"].split(',')[0]:
                        self.label.setText(f"Напишите название игры")
                        self.lineEdit.show()
                        self.yes_2.show()
                        self.no_2.show()
                        self.yes_2.clicked = False
                        self.no_2.clicked = False
                        while go:
                            QApplication.processEvents()
                            time.sleep(0.05)
                            if self.yes_2.clicked:
                                self.voise_name = self.lineEdit.text().strip().lower()
                                if self.voise_name and any(c.isalnum() for c in self.voise_name):
                                    go = False
                                else:
                                    self.label.setText(
                                        "Имя должно содержать цифры или буквы")
                                    self.yes_2.clicked = False
                            elif self.no_2.clicked:
                                self.label.setText(f"Отмена запуска")
                                go = False
                                return
                    self.lineEdit.hide()
                    self.yes_2.hide()
                    self.no_2.hide()
                    if self.voise_name:
                        voise_name = self.voise_name
                    else:
                        voise_name = game_name_1

                    if games_folder and os.path.exists(games_folder) and "игру" in self.command:
                        # Проходим по файлам/папкам в этой папке
                        for item in os.listdir(games_folder):
                            if self.is_match(item, voise_name):
                                path_to_game = os.path.join(
                                    games_folder, item)
                                cached_path = self.load_path_from_file(
                                    voise_name.strip().lower())
                                if not cached_path:
                                    # Если нет — сохраняем путь
                                    self.connect = sqlite3.connect(
                                        f"world_{self.content}.db")
                                    cursor = self.connect.cursor()
                                    cursor.execute(
                                        "INSERT INTO Path (Name) VALUES (?)", (voise_name,))
                                    self.connect.commit()
                                    if self.voise_name:
                                        cursor.execute(
                                            "INSERT INTO Names (Name_1, Name_2) VALUES (?, ?)", (game_name_1, voise_name))
                                        self.connect.commit()
                                    self.connect.close()
                                    self.save_paths_to_file(
                                        voise_name.strip().lower(), [path_to_game])
                                    print(
                                        f"💾 Добавлено в кеш: {voise_name} → {path_to_game}")
                                else:
                                    print(
                                        f"⚡ Уже есть в кеше: {cached_path}")
                                    break
                        if path_to_game:
                            self.run_executable(path_to_game)
                            clos = False
                            self.voise_name = False
                    if clos:
                        open_file = self.find_folder_or_file(
                            f"{voise_name}")
                        if open_file:
                            self.connect = sqlite3.connect(
                                f"world_{self.content}.db")
                            cursor = self.connect.cursor()
                            cursor.execute(
                                "INSERT INTO Path (Name) VALUES (?)", (voise_name,))
                            self.connect.commit()
                            if self.voise_name:
                                cursor.execute(
                                    "INSERT INTO Names (Name_1, Name_2) VALUES (?, ?)", (game_name_1, voise_name))
                                self.connect.commit()
                            self.connect.close()
                            self.save_paths_to_file(
                                voise_name.strip().lower(), [open_file])
                            self.run_executable(open_file)
                            self.label.setText("Сделано")
                            self.voise_name = False
                        else:
                            self.label.setText("Такой игры не найдено")
                            self.voise_name = False
            else:
                index_command = self.command.index("игру") + len("игру")
                game_name = self.command[index_command:].strip()

                if game_name.lower() in self.name.keys():
                    found_key = self.name.get(game_name.lower())
                    cached_path = self.load_path_from_file(found_key.lower())
                else:
                    cached_path = self.load_path_from_file(game_name.lower())

                if cached_path:
                    print(f"✅ Используем кэшированный путь: {cached_path}")
                    self.run_executable(cached_path)
                    self.label.setText("Сделано")
                else:
                    self.label.setText(
                        f"Вы впервые запускаете эту игру, я правильно услышала?{game_name}")
                    conf = False

                    while not conf:
                        conf = self.confirmation()
                        if conf == 'cancel':
                            self.close_requested = True
                            self.voice_thread.resume()
                            return

                    if conf == self.data["world_conf"].split(',')[0]:
                        self.label.setText(f"Напишите название игры")
                        self.lineEdit.show()
                        self.yes_2.show()
                        self.no_2.show()
                        self.yes_2.clicked = False
                        self.no_2.clicked = False
                        while go:
                            QApplication.processEvents()
                            time.sleep(0.05)
                            if self.yes_2.clicked:
                                self.voise_name = self.lineEdit.text().strip().lower()
                                if self.voise_name and any(c.isalnum() for c in self.voise_name):
                                    go = False
                                else:
                                    self.label.setText(
                                        "Имя должно содержать цифры или буквы")
                                    self.yes_2.clicked = False
                            elif self.no_2.clicked:
                                self.label.setText(f"Отмена запуска")
                                go = False
                                return
                        self.lineEdit.hide()
                        self.yes_2.hide()
                        self.no_2.hide()
                    if self.voise_name:
                        voise_name = self.voise_name
                    else:
                        voise_name = game_name

                    open_file = self.find_folder_or_file(
                        f"{voise_name}")
                    if open_file:
                        self.connect = sqlite3.connect(
                            f"world_{self.content}.db")
                        cursor = self.connect.cursor()
                        cursor.execute(
                            "INSERT INTO Path (Name) VALUES (?)", (voise_name,))
                        self.connect.commit()
                        if self.voise_name:
                            cursor.execute(
                                "INSERT INTO Names (Name_1, Name_2) VALUES (?, ?)", (game_name, voise_name))
                        self.connect.commit()
                        self.connect.close()
                        self.save_paths_to_file(
                            voise_name.strip().lower(), [open_file])
                        self.run_executable(open_file)
                        self.label.setText("Сделано")
                        self.voise_name = False
                    else:
                        self.label.setText("Такой игры не найдено")
                        self.voise_name = False

        elif "папку" in self.command:
            index_command = self.command.index("папку") + len("папку")
            name = self.command[index_command:].strip()

            if name.lower() in self.name.keys():
                found_key = self.name.get(name.lower())
                cached_path = self.load_path_from_file(found_key.lower())
            else:
                cached_path = self.load_path_from_file(name.lower())

            if cached_path:
                print(f"✅ Используем кэшированный путь: {cached_path}")
                self.run_executable(cached_path)
                self.label.setText("Сделано")
            else:
                self.label.setText(
                    f"Вы впервые запускаете эту папку, я правильно услышала?{name}")
                conf = False

                while not conf:
                    conf = self.confirmation()
                    if conf == 'cancel':
                        self.close_requested = True
                        self.voice_thread.resume()
                        return

                if conf == self.data["world_conf"].split(',')[0]:
                    self.label.setText(f"Напишите название папки")
                    self.lineEdit.show()
                    self.yes_2.show()
                    self.no_2.show()
                    self.yes_2.clicked = False
                    self.no_2.clicked = False
                    while go:
                        QApplication.processEvents()
                        time.sleep(0.05)
                        if self.yes_2.clicked:
                            self.voise_name = self.lineEdit.text().strip().lower()
                            if self.voise_name and any(c.isalnum() for c in self.voise_name):
                                go = False
                            else:
                                self.label.setText(
                                    "Имя должно содержать цифры или буквы")
                                self.yes_2.clicked = False
                        elif self.no_2.clicked:
                            self.label.setText(f"Отмена запуска")
                            go = False
                            return
                    self.lineEdit.hide()
                    self.yes_2.hide()
                    self.no_2.hide()
                if self.voise_name:
                    voise_name = self.voise_name
                else:
                    voise_name = name

                open_file = self.find_folder_or_file(
                    f"{voise_name}")
                if open_file:
                    self.connect = sqlite3.connect(
                        f"world_{self.content}.db")
                    cursor = self.connect.cursor()
                    cursor.execute(
                        "INSERT INTO Path (Name) VALUES (?)", (voise_name,))
                    self.connect.commit()
                    if self.voise_name:
                        cursor.execute(
                            "INSERT INTO Names (Name_1, Name_2) VALUES (?, ?)", (name, voise_name))
                    self.connect.commit()
                    self.connect.close()
                    self.save_paths_to_file(
                        voise_name.strip().lower(), [open_file])
                    self.run_executable(open_file)
                    self.label.setText("Сделано")
                    self.voise_name = False
                else:
                    self.label.setText("Такой игры не найдено")
                    self.voise_name = False

        elif "диспетчер задач" in self.command:
            pyautogui.hotkey('ctrl', 'shift', "esc")

        elif "открой" in self.command:
            index_command = self.command.index("открой") + len("открой")
            name = self.command[index_command:].strip()

            if name.lower() in self.name.keys():
                found_key = self.name.get(name.lower())
                cached_path = self.load_path_from_file(found_key.lower())
            else:
                cached_path = self.load_path_from_file(name.lower())

            if cached_path:
                print(f"✅ Используем кэшированный путь: {cached_path}")
                self.run_executable(cached_path)
                self.label.setText("Сделано")
            else:
                self.label.setText(
                    f"Вы впервые запускаете эту программу, я правильно услышала?{name}")
                conf = False

                while not conf:
                    conf = self.confirmation()
                    if conf == 'cancel':
                        self.close_requested = True
                        self.voice_thread.resume()
                        return

                if conf == self.data["world_conf"].split(',')[0]:
                    self.label.setText(
                        f"Напишите название программы, документа или папки")
                    self.lineEdit.show()
                    self.yes_2.show()
                    self.no_2.show()
                    self.yes_2.clicked = False
                    self.no_2.clicked = False
                    while go:
                        QApplication.processEvents()
                        time.sleep(0.05)
                        if self.yes_2.clicked:
                            self.voise_name = self.lineEdit.text().strip().lower()
                            if self.voise_name and any(c.isalnum() for c in self.voise_name):
                                go = False
                            else:
                                self.label.setText(
                                    "Имя должно содержать цифры или буквы")
                                self.yes_2.clicked = False
                        elif self.no_2.clicked:
                            self.label.setText(f"Отмена запуска")
                            go = False
                            return
                    self.lineEdit.hide()
                    self.yes_2.hide()
                    self.no_2.hide()
                if self.voise_name:
                    voise_name = self.voise_name
                else:
                    voise_name = name

                open_file = self.find_folder_or_file(
                    f"{voise_name}")
                if open_file:
                    self.connect = sqlite3.connect(
                        f"world_{self.content}.db")
                    cursor = self.connect.cursor()
                    cursor.execute(
                        "INSERT INTO Path (Name) VALUES (?)", (voise_name,))
                    self.connect.commit()
                    if self.voise_name:
                        cursor.execute(
                            "INSERT INTO Names (Name_1, Name_2) VALUES (?, ?)", (name, voise_name))
                    self.connect.commit()
                    self.connect.close()
                    self.save_paths_to_file(
                        voise_name.strip().lower(), [open_file])
                    self.run_executable(open_file)
                    self.label.setText("Сделано")
                    self.voise_name = False
                else:
                    self.label.setText("Такой игры не найдено")
                    self.voise_name = False

        elif "запусти" in self.command:
            index_command = self.command.index("запусти") + len("запусти")
            name = self.command[index_command:].strip()

            if name.lower() in self.name.keys():
                found_key = self.name.get(name.lower())
                cached_path = self.load_path_from_file(found_key.lower())
            else:
                cached_path = self.load_path_from_file(name.lower())

            if cached_path:
                print(f"✅ Используем кэшированный путь: {cached_path}")
                self.run_executable(cached_path)
                self.label.setText("Сделано")
                return
            else:
                self.label.setText(
                    f"Вы впервые запускаете эту программу, я правильно услышала?{name}")
                conf = False

                while not conf:
                    conf = self.confirmation()
                    if conf == 'cancel':
                        self.close_requested = True
                        self.voice_thread.resume()
                        return

                if conf == self.data["world_conf"].split(',')[0]:
                    self.label.setText(f"Напишите название игры")
                    self.lineEdit.show()
                    self.yes_2.show()
                    self.no_2.show()
                    self.yes_2.clicked = False
                    self.no_2.clicked = False
                    while go:
                        QApplication.processEvents()
                        time.sleep(0.05)
                        if self.yes_2.clicked:
                            self.voise_name = self.lineEdit.text().strip().lower()
                            if self.voise_name and any(c.isalnum() for c in self.voise_name):
                                go = False
                            else:
                                self.label.setText(
                                    "Имя должно содержать цифры или буквы")
                                self.yes_2.clicked = False
                        elif self.no_2.clicked:
                            self.label.setText(f"Отмена запуска")
                            go = False
                            return
                    self.lineEdit.hide()
                    self.yes_2.hide()
                    self.no_2.hide()
                if self.voise_name:
                    voise_name = self.voise_name
                else:
                    voise_name = name

                open_file = self.find_folder_or_file(
                    f"{voise_name}")
                if open_file:
                    self.connect = sqlite3.connect(
                        f"world_{self.content}.db")
                    cursor = self.connect.cursor()
                    cursor.execute(
                        "INSERT INTO Path (Name) VALUES (?)", (voise_name,))
                    self.connect.commit()
                    if self.voise_name:
                        cursor.execute(
                            "INSERT INTO Names (Name_1, Name_2) VALUES (?, ?)", (name, voise_name))
                    self.connect.commit()
                    self.connect.close()
                    self.save_paths_to_file(
                        voise_name.strip().lower(), [open_file])
                    self.run_executable(open_file)
                    self.label.setText("Сделано")
                    self.voise_name = False
                else:
                    self.label.setText("Такой игры не найдено")
                    self.voise_name = False
        self.close_requested = True
        self.voice_thread.resume()

    def shutdown_computer(self, delay_seconds):
        def do_shutdown():
            if os.name == 'nt':
                subprocess.run(["shutdown", "/s", "/f", "/t", "0"])
            else:
                subprocess.run(["shutdown", "-h", "now"])
        self.run_delayed_action("выключение компьютера",
                                delay_seconds, do_shutdown)

    def restart_computer(self, delay_seconds):
        def do_restart():
            if os.name == 'nt':
                subprocess.run(["shutdown", "/r", "/f", "/t", "0"])
            else:
                subprocess.run(["reboot"])
        self.run_delayed_action("перезагрузка компьютера",
                                delay_seconds, do_restart)

    def sleep_computer(self, delay_seconds):
        def do_sleep():
            if os.name == 'nt':
                subprocess.run(
                    ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            else:
                subprocess.run(["systemctl", "suspend"])
        self.run_delayed_action("сон", delay_seconds, do_sleep)

    def cancel_shutdown(self):
        """Отмена любого действия (выключение, сон, перезапуск)"""
        if self.active_action:
            self.action_cancelled = True
            self.label.setText(
                f"✅ {self.active_action.capitalize()} отменено.")
            self.active_action = None

            import subprocess
            import os
            if os.name == 'nt':
                subprocess.run(["shutdown", "/a"], check=False)
            print("🛑 Действие отменено пользователем.")
        else:
            self.label.setText("Нет активной операции для отмены.")

    def run_executable(self, path):
        if isinstance(path, str) and path.startswith(('steam://', 'http://', 'https://', 'uplay://', 'battle.net://', 'com.epicgames.launcher://')):
            try:
                webbrowser.open(path)
                print(f"🌐 Открыта ссылка: {path}")
                return
            except Exception as e:
                print(f"❌ Ошибка открытия ссылки {path}: {e}")
                return
        elif os.path.isdir(path):
            # Открываем папку
            try:
                os.startfile(path)
                print(f"📁 Открыта папка: {path}")
            except Exception as e:
                print(f"❌ Ошибка открытия папки {path}: {e}")
        elif os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()

            if ext == ".lnk":
                try:
                    os.startfile(path)
                    print(f"📄 Открыт ярлык: {path}")
                except Exception as e:
                    print(f"❌ Ошибка открытия ярлыка {path}: {e}")

            elif ext == ".url":
                try:
                    # Читаем URL из файла
                    url = None
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith("URL="):
                                url = line[4:].strip()
                                break
                    if url:
                        webbrowser.open(url)
                        print(f"🌐 Открыт интернет-ярлык: {url}")
                    else:
                        print(f"❌ Не удалось прочитать URL в {path}")
                except Exception as e:
                    print(f"❌ Ошибка открытия интернет-ярлыка {path}: {e}")

            else:
                # Для .exe, .bat, .msi и т.д.
                workdir = os.path.dirname(path)
                try:
                    subprocess.Popen([path], cwd=workdir)
                    print(f"🚀 Запущено: {path}")
                except Exception as e:
                    print(f"❌ Ошибка запуска {path}: {e}")

    def on_yes_clicked(self):
        self.yes_2.clicked = True

    def on_no_clicked(self):
        self.no_2.clicked = True

    def record_new_direct(self):
        """Записывает или меняет путь к папке (только запись названия)"""
        self.close_requested = False
        self.anywest = None
        self.voice_thread.pause()
        voise_direct_name = True
        self.label.setText("Напишите название папки")
        go = True
        self.lineEdit.show()
        self.yes_2.show()
        self.no_2.show()
        self.yes_2.clicked = False
        self.no_2.clicked = False

        while go:
            no = False
            QApplication.processEvents()
            time.sleep(0.05)
            if self.yes_2.clicked:
                new_direct_name = self.lineEdit.text().strip().lower()
                if new_direct_name and any(c.isalnum() for c in new_direct_name):
                    no = False
                    go = False
                    self.connect = sqlite3.connect(
                        f"world_{self.content}.db")
                    cursor = self.connect.cursor()
                    cursor.execute(
                        "INSERT INTO Path (Name) VALUES (?)", (new_direct_name,))
                    self.connect.commit()
                    self.connect.close()
                else:
                    self.label.setText(
                        "Имя должно содержать цифры или буквы")
                    self.yes_2.clicked = False
            elif self.no_2.clicked:
                no = True
                go = False
                break

        existing_path = self.path.get(new_direct_name)
        if existing_path:
            self.label.setText(
                f"Папка '{new_direct_name}' уже настроена. Выберите новый путь.")
        else:
            self.label.setText(
                f"'{new_direct_name}' новая папка. Выберите путь.")
            self.connect = sqlite3.connect(
                f"world_{self.content}.db")
            cursor = self.connect.cursor()
            cursor.execute(
                "INSERT INTO Path (Name) VALUES (?)", (new_direct_name,))
            self.connect.commit()
            self.connect.close()

        self.lineEdit.hide()
        self.yes_2.hide()
        self.no_2.hide()

        if no:
            self.label.setText("Отменено")
        else:
            self.choose_directory_signal.emit(new_direct_name)

        self.close_requested = True
        self.voice_thread.resume()

    def open_directory_dialog(self, folder_name):
        """Слот для открытия диалога выбора папки в GUI-потоке"""
        user_chosen_path = QFileDialog.getExistingDirectory(
            self, f"Выберите папку для '{folder_name}'")
        if user_chosen_path:
            self.save_paths_to_file(folder_name, [user_chosen_path])
            self.label.setText(
                f"Папка '{folder_name}' успешно настроена: {user_chosen_path}")
        else:
            self.label.setText("Выбор папки отменён")

    def record_new_name(self):
        """Записывает новое имя в отдельном потоке"""
        self.voice_thread.pause()
        name = self.data.get("new_name")
        self.label.setText(f"{name}")
        old_name = self.wake_word
        recognizer = sr.Recognizer()
        self.close_requested = False

        try:
            self.wake_word = None
            while self.wake_word == None:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=1)
                    audio = recognizer.listen(
                        source, timeout=10, phrase_time_limit=5)
                    new_name = recognizer.recognize_google(
                        audio, language=f"{self.content.lower()}-{self.content.upper()}")
                    if "отмена" in new_name:
                        self.wake_word = old_name
                        break
                    self.wake_word = new_name.lower()
                    self.connect = sqlite3.connect(
                        f"world_{self.content}.db")
                    cursor = self.connect.cursor()
                    cursor.execute(
                        "UPDATE Worlds SET Text = ? WHERE Name_command = 'name'",
                        (str(new_name.lower()),)
                    )
                    self.connect.commit()
                    self.connect.close()
                    self.name_create = self.data["name_create"]
                    self.label.setText(
                        f"{self.name_create} '{self.wake_word}'")
                    self.data["name"] = self.wake_word
                    self.voice_thread.db_data = self.data
                print(self.wake_word)

        except sr.WaitTimeoutError:
            self.label.setText("Время ожидания истекло")
        except sr.UnknownValueError:
            self.label.setText("Имя не распознано")
        except Exception as e:
            self.label.setText(f"Error: {e}")
        finally:
            self.voice_thread.resume()
            self.close_requested = True

    def open_settings(self):
        """Открытие окна настроек"""
        # Скрываем главное окно
        self.hide()

        # Создаем окно настроек (передаем себя как родительское окно)
        self.settings_window = SettingsWindow(self)

        # Показываем окно настроек
        self.settings_window.show()

    def restart_voice_thread(self):
        """Перезапускает голосовой поток"""
        self.voice_thread.stop()
        self.voice_thread.wait(5000)  # Ждем до 5 секунд
        self.voice_thread = VoiceThread(self.wake_word)
        self.voice_thread.status_changed.connect(self.label.setText)
        self.voice_thread.command_received.connect(self.process_command)
        self.voice_thread.error_occurred.connect(self.handle_error)
        self.voice_thread.start()
        self.label.setText("Голосовой поток перезапущен")

    def closeEvent(self, event):
        """Останавливает поток при закрытии окна"""
        self.voice_thread.stop()
        self.voice_thread.wait(5000)  # Ждем до 5 секунд
        event.accept()


# Запуск приложения
if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = VoiceAssistant()
    ex.show()
    sys.exit(app.exec())
