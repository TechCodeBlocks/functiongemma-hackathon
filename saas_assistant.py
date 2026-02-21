import sys
import os
import json
import time
import subprocess
import wave
import tempfile

try:
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                                 QHBoxLayout, QPushButton, QTextEdit, QLabel, QLineEdit,
                                 QGraphicsDropShadowEffect, QSizePolicy, QFrame, QScrollArea)
    from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QPropertyAnimation, pyqtSignal, QThread, QTimer, QEasingCurve
    from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QLinearGradient, QBrush, QPen, QRadialGradient
except ImportError:
    print("PyQt6 not found. Installing PyQt6...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "PyQt6"])
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                                 QHBoxLayout, QPushButton, QTextEdit, QLabel, QLineEdit,
                                 QGraphicsDropShadowEffect, QSizePolicy, QFrame, QScrollArea)
    from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QPropertyAnimation, pyqtSignal, QThread, QTimer, QEasingCurve
    from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QLinearGradient, QBrush, QPen, QRadialGradient

try:
    import speech_recognition as sr
    HAS_AUDIO = True
except ImportError:
    print("Installing audio dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "SpeechRecognition", "pyaudio", "scipy", "numpy", "wavio"])
    import speech_recognition as sr
    HAS_AUDIO = True

# Add cactus path
sys.path.insert(0, "cactus/python/src")
try:
    from cactus import cactus_init, cactus_transcribe, cactus_destroy
    from main import generate_hybrid
except ImportError as e:
    print(f"Error importing cactus or main: {e}")
    print("Make sure you are running this from the functiongemma-hackathon root.")

# Define SaaS Tools
TOOLS = [
    {
        "name": "get_unpaid_invoices",
        "description": "Find unpaid invoices for a specific customer or account.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_name": {"type": "string", "description": "The name of the customer account (e.g., 'ACME')"}
            },
            "required": ["account_name"]
        }
    },
    {
        "name": "create_support_ticket",
        "description": "Open a priority support ticket for a customer issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_name": {"type": "string", "description": "The name of the customer account"},
                "issue_summary": {"type": "string", "description": "A short summary of the issue"}
            },
            "required": ["account_name", "issue_summary"]
        }
    },
    {
        "name": "check_usage_alerts",
        "description": "Check if a customer has any recent usage alerts or spikes.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_name": {"type": "string", "description": "The name of the customer account"}
            },
            "required": ["account_name"]
        }
    },
    {
        "name": "flag_expansion_opportunity",
        "description": "Flag a customer account as a potential expansion or upsell opportunity.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_name": {"type": "string", "description": "The name of the customer account"},
                "reason": {"type": "string", "description": "Reason for the expansion flag"}
            },
            "required": ["account_name", "reason"]
        }
    }
]

class VoiceRecordWorker(QThread):
    command_ready = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    
    def run(self):
        if not HAS_AUDIO:
            self.error_signal.emit("Audio dependencies missing.")
            return
class VoiceRecordWorker(QThread):
    command_ready = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self._is_running = True
        self._recognizer = sr.Recognizer()
        
    def stop(self):
        self._is_running = False
        
    def run(self):
        if not HAS_AUDIO:
            self.error_signal.emit("Audio dependencies missing.")
            return
            
        try:
            with sr.Microphone() as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.5)
                # Listen continuously, but stop quickly if _is_running becomes False
                audio_data = None
                while self._is_running:
                    try:
                        audio_data = self._recognizer.listen(source, timeout=1, phrase_time_limit=10)
                        break # Got audio successfully
                    except sr.WaitTimeoutError:
                        continue # Timeouts are expected, keep polling while running
                        
            if not self._is_running and audio_data is None:
                return # Cancelled early without getting any audio
                
            if audio_data is None:
                self.error_signal.emit("No speech detected.")
                return

            # Save to temp
            tmp_wav = tempfile.mktemp(suffix=".wav")
            with open(tmp_wav, "wb") as f:
                f.write(audio_data.get_wav_data())
            
            # Transcribe with Cactus Whisper
            try:
                whisper = cactus_init("cactus/weights/whisper-small")
            except Exception:
                # Fallback path if weights are in different structure
                whisper = cactus_init("weights/whisper-small")
                
            prompt = "<|startoftranscript|><|en|><|transcribe|><|notimestamps|>"
            result_json = cactus_transcribe(whisper, tmp_wav, prompt=prompt)
            cactus_destroy(whisper)
            
            os.remove(tmp_wav)
            
            result = json.loads(result_json)
            text = result.get("response", "").strip()
            if text:
                self.command_ready.emit(text)
            else:
                self.error_signal.emit("Could not understand audio.")
                
        except Exception as e:
            self.error_signal.emit(f"Microphone error: {str(e)}")


class GenerateWorker(QThread):
    result_signal = pyqtSignal(dict)
    
    def __init__(self, query):
        super().__init__()
        self.query = query
        
    def run(self):
        try:
            messages = [{"role": "user", "content": self.query}]
            result = generate_hybrid(messages, TOOLS)
            self.result_signal.emit(result)
        except Exception as e:
            self.result_signal.emit({"error": str(e)})


class GlassButton(QPushButton):
    def __init__(self, text, primary=False):
        super().__init__(text)
        self.primary = primary
        self.setMinimumHeight(45)
        self.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(self._get_style(False, False))
        
        self.glow = QGraphicsDropShadowEffect(self)
        self.glow.setBlurRadius(15)
        self.glow.setColor(QColor(100, 150, 255, 80) if primary else QColor(255, 255, 255, 30))
        self.glow.setOffset(0, 0)
        self.setGraphicsEffect(self.glow)
        
    def _get_style(self, hover, pressed):
        bg = "rgba(100, 150, 255, 0.2)" if self.primary else "rgba(255, 255, 255, 0.05)"
        border = "rgba(100, 150, 255, 0.5)" if self.primary else "rgba(255, 255, 255, 0.1)"
        color = "#FFFFFF" if self.primary else "#DDDDDD"
        
        if hover:
            bg = "rgba(100, 150, 255, 0.3)" if self.primary else "rgba(255, 255, 255, 0.1)"
            border = "rgba(100, 150, 255, 0.8)" if self.primary else "rgba(255, 255, 255, 0.3)"
        if pressed:
            bg = "rgba(100, 150, 255, 0.4)" if self.primary else "rgba(255, 255, 255, 0.2)"
            
        return f"""
            QPushButton {{
                background-color: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 5px 20px;
            }}
        """

    def enterEvent(self, event):
        self.setStyleSheet(self._get_style(True, False))
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        self.setStyleSheet(self._get_style(False, False))
        super().leaveEvent(event)
        
    def mousePressEvent(self, event):
        self.setStyleSheet(self._get_style(True, True))
        super().mousePressEvent(event)
        
    def mouseReleaseEvent(self, event):
        self.setStyleSheet(self._get_style(True, False))
        super().mouseReleaseEvent(event)


class SmartAPIAssistant(QMainWindow):
    def __init__(self):
        super().__init__()
        # Glassmorphic window
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1000, 750)
        
        self.drag_pos = QPoint()
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)
        
        # --- HEADER ---
        self.header_layout = QHBoxLayout()
        
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        
        self.title_label = QLabel("SaaS Copilot")
        self.title_label.setFont(QFont("Inter", 24, QFont.Weight.Bold))
        self.title_label.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        
        self.subtitle_label = QLabel("Hybrid Edge/Cloud API Orchestration")
        self.subtitle_label.setFont(QFont("Inter", 12))
        self.subtitle_label.setStyleSheet("color: #A0C0FF;")
        
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        
        self.header_layout.addLayout(title_box)
        self.header_layout.addStretch()
        
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(36, 36)
        self.close_btn.setFont(QFont("Inter", 14, QFont.Weight.Bold))
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A0C0FF;
                border: 1px solid rgba(160, 192, 255, 0.3);
                border-radius: 18px;
            }
            QPushButton:hover {
                background-color: rgba(255, 50, 50, 0.6);
                color: white;
                border: 1px solid rgba(255, 50, 50, 0.8);
            }
        """)
        self.close_btn.clicked.connect(self.close)
        self.header_layout.addWidget(self.close_btn)
        
        self.main_layout.addLayout(self.header_layout)

        # --- EXAMPLES (Quick actions) ---
        self.examples_layout = QHBoxLayout()
        self.examples_layout.setSpacing(10)
        
        ex1 = GlassButton("Example: Check ACME invoices & ticket")
        ex1.clicked.connect(lambda: self.run_query("Find unpaid invoices for ACME and open a support ticket about unpaid invoices."))
        
        ex2 = GlassButton("Example: Flag TechCorp expansion")
        ex2.clicked.connect(lambda: self.run_query("Check usage alerts for TechCorp and flag them as an expansion opportunity due to high usage."))
        
        self.examples_layout.addWidget(ex1)
        self.examples_layout.addWidget(ex2)
        self.examples_layout.addStretch()
        self.main_layout.addLayout(self.examples_layout)
        
        # --- LOG / EXECUTION AREA ---
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFont(QFont("Menlo", 12))
        self.log_area.setStyleSheet("""
            QTextEdit {
                background-color: rgba(10, 15, 30, 0.7);
                color: #FFFFFF;
                border: 1px solid rgba(100, 150, 255, 0.3);
                border-radius: 15px;
                padding: 15px;
                selection-background-color: rgba(100, 150, 255, 0.4);
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(100, 150, 255, 0.3);
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(100, 150, 255, 0.6);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        self.log_area.setMinimumHeight(350)
        
        # Shadow for text area
        text_shadow = QGraphicsDropShadowEffect()
        text_shadow.setBlurRadius(20)
        text_shadow.setColor(QColor(0, 0, 0, 100))
        text_shadow.setOffset(0, 5)
        self.log_area.setGraphicsEffect(text_shadow)
        
        self.main_layout.addWidget(self.log_area)
        
        # --- INPUT AREA ---
        self.input_layout = QHBoxLayout()
        self.input_layout.setSpacing(15)
        
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Ask the copilot to perform actions...")
        self.command_input.setFont(QFont("Inter", 14))
        self.command_input.setMinimumHeight(56)
        self.command_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(20, 30, 50, 0.6);
                color: #FFFFFF;
                border: 1px solid rgba(100, 150, 255, 0.4);
                border-radius: 28px;
                padding: 0 25px;
            }
            QLineEdit:focus {
                background-color: rgba(25, 40, 60, 0.8);
                border: 1px solid rgba(100, 150, 255, 0.9);
            }
        """)
        self.command_input.returnPressed.connect(self.on_submit_text)
        
        # Voice Button (Microphone Icon)
        self.voice_btn = QPushButton("🎙️")
        self.voice_btn.setFixedSize(56, 56)
        self.voice_btn.setFont(QFont("Arial", 20))
        self.voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(100, 150, 255, 0.1);
                color: #FFFFFF;
                border: 1px solid rgba(100, 150, 255, 0.5);
                border-radius: 28px;
            }
            QPushButton:hover {
                background-color: rgba(100, 150, 255, 0.3);
                border: 1px solid rgba(100, 150, 255, 1.0);
            }
        """)
        self.voice_btn.clicked.connect(self.toggle_voice)
        
        # Submit Button
        self.submit_btn = QPushButton("🚀")
        self.submit_btn.setFixedSize(56, 56)
        self.submit_btn.setFont(QFont("Arial", 20))
        self.submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_btn.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4A90E2, stop:1 #50E3C2);
                color: #FFFFFF;
                border: none;
                border-radius: 28px;
            }
            QPushButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5ca1f3, stop:1 #61f4d3);
            }
        """)
        self.submit_btn.clicked.connect(self.on_submit_text)
        
        self.input_layout.addWidget(self.command_input)
        self.input_layout.addWidget(self.voice_btn)
        self.input_layout.addWidget(self.submit_btn)
        
        self.main_layout.addLayout(self.input_layout)
        
        # Status Label
        self.status_label = QLabel("SYSTEM IDLE")
        self.status_label.setFont(QFont("Inter", 10))
        self.status_label.setStyleSheet("color: rgba(160, 192, 255, 0.6);")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_layout.addWidget(self.status_label)
        
        self.append_log("<span style='color: #4A90E2;'>[SYSTEM]</span> SaaS Copilot initialized.")
        self.append_log("<span style='color: #4A90E2;'>[SYSTEM]</span> FunctionGemma hybrid model loaded.")
        self.append_log("<br>")

        # Voice state
        self.is_recording = False

    def append_log(self, text):
        self.log_area.append(text)
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def toggle_voice(self):
        if self.is_recording:
            # Stop recording
            self.is_recording = False
            self.voice_worker.stop()
            self.status_label.setText("PROCESSING AUDIO...")
            self.append_log("<span style='color: #A0C0FF;'>[MIC]</span> Recording stopped. Transcribing...")
            self.voice_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(100, 150, 255, 0.1);
                    color: #FFFFFF;
                    border: 1px solid rgba(100, 150, 255, 0.5);
                    border-radius: 28px;
                }
            """)
            return
            
        self.is_recording = True
        self.voice_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 50, 50, 0.5);
                color: #FFFFFF;
                border: 2px solid rgba(255, 50, 50, 1.0);
                border-radius: 28px;
            }
        """)
        self.status_label.setText("LISTENING... (Click again to stop)")
        self.append_log("<span style='color: #FF6B6B;'>[MIC]</span> Recording started. Speak now, then click the mic button again to stop.")
        
        self.voice_worker = VoiceRecordWorker()
        self.voice_worker.command_ready.connect(self.on_voice_success)
        self.voice_worker.error_signal.connect(self.on_voice_error)
        self.voice_worker.start()

    def on_voice_success(self, text):
        self.reset_voice_btn()
        self.append_log(f"<span style='color: #A0C0FF;'>[WHISPER]</span> Transcribed: '{text}'")
        self.command_input.setText(text)
        self.run_query(text)

    def on_voice_error(self, err):
        self.reset_voice_btn()
        self.append_log(f"<span style='color: #FF6B6B;'>[ERROR]</span> {err}")

    def reset_voice_btn(self):
        self.is_recording = False
        self.status_label.setText("SYSTEM IDLE")
        self.voice_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(100, 150, 255, 0.1);
                color: #FFFFFF;
                border: 1px solid rgba(100, 150, 255, 0.5);
                border-radius: 28px;
            }
            QPushButton:hover {
                background-color: rgba(100, 150, 255, 0.3);
                border: 1px solid rgba(100, 150, 255, 1.0);
            }
        """)

    def on_submit_text(self):
        text = self.command_input.text().strip()
        if not text:
            return
        self.command_input.clear()
        self.run_query(text)

    def run_query(self, text):
        self.append_log(f"<span style='color: #FFFFFF; font-size: 14px;'><b>> {text}</b></span>")
        self.append_log("<i><span style='color: #A0C0FF;'>Processing orchestration via Hybrid Model...</span></i>")
        self.status_label.setText("INFERENCE IN PROGRESS...")
        
        self.gen_worker = GenerateWorker(text)
        self.gen_worker.result_signal.connect(self.on_generate_result)
        self.gen_worker.start()

    def on_generate_result(self, result):
        self.status_label.setText("SYSTEM IDLE")
        
        if "error" in result:
            self.append_log(f"<span style='color: #FF6B6B;'>[FAULT] Orchestration Error: {result['error']}</span>")
            self.append_log("<br>")
            return
            
        calls = result.get("function_calls", [])
        time_ms = result.get("total_time_ms", 0)
        source = result.get("source", "unknown")
        
        if not calls:
            self.append_log(f"<span style='color: #AAAAAA;'>[COPILOT] Result ({time_ms:.1f}ms). No tool calls planned.</span>")
            self.append_log("<br>")
            return
        
        # Display the result elegantly
        source_color = "#50E3C2" if "device" in source.lower() else "#F5A623"
        
        summary = f"<span style='color: #A0C0FF;'>[METRICS]</span> Latency: <b>{time_ms:.1f}ms</b> | Source Engine: <b style='color: {source_color};'>{source.upper()}</b> | Tool Calls Generated: <b>{len(calls)}</b>"
        self.append_log(summary)
        
        for idx, call in enumerate(calls):
            name = call.get('name', 'unknown_function')
            args = json.dumps(call.get('arguments', {}), indent=2)
            # Create a nice code block
            box = f"""
<div style='background-color: rgba(30, 40, 60, 0.5); border: 1px solid rgba(100, 150, 255, 0.2); border-radius: 8px; padding: 10px; margin-top: 5px; margin-bottom: 5px;'>
<b style='color: #50E3C2;'>[Step {idx+1}]</b> {name}
<pre style='color: #E2E8F0; margin-top: 5px; font-size: 11px;'>{args}</pre>
</div>
"""
            self.log_area.insertHtml(box)
            self.log_area.append("") # newline hack
            
        self.append_log("<br>")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    # Window drag logic for frameless window
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if not self.drag_pos.isNull():
            delta = event.globalPosition().toPoint() - self.drag_pos
            self.move(self.pos() + delta)
            self.drag_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = QPoint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = QRect(10, 10, self.width() - 20, self.height() - 20)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 25, 25)
        
        # Base glass fill - beautiful modern gradient
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor(15, 20, 35, 240))
        gradient.setColorAt(0.5, QColor(25, 30, 50, 230))
        gradient.setColorAt(1.0, QColor(10, 15, 25, 250))
        
        painter.fillPath(path, QBrush(gradient))
        
        # Accent lighting (subtle radial glow top left)
        radial_grad = QRadialGradient(200, 200, 400)
        radial_grad.setColorAt(0, QColor(74, 144, 226, 30))
        radial_grad.setColorAt(1, QColor(74, 144, 226, 0))
        painter.fillPath(path, QBrush(radial_grad))
        
        # Border
        pen = QPen(QColor(100, 150, 255, 60))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(path)
        
        # Inner reflection line
        pen.setColor(QColor(255, 255, 255, 20))
        pen.setWidth(1)
        painter.setPen(pen)
        path_inner = QPainterPath()
        path_inner.addRoundedRect(QRectF(rect.adjusted(2, 2, -2, -2)), 23, 23)
        painter.drawPath(path_inner)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Optional: Font setup
    QFont.insertSubstitution("Inter", "Helvetica Neue")
    
    window = SmartAPIAssistant()
    window.show()
    sys.exit(app.exec())
