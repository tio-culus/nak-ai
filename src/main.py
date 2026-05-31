import time
import queue
import re
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from engine.stt_engine import STTEngine
from engine.keyring_manager import KeyringManager
from engine.gemini_engine import GeminiEngine
from engine.config_manager import ConfigManager
from engine.sound_player import SoundPlayer
# NakAIが公式にサポートするLLMモデルのホワイトリスト (モデルIDのセット)
# Why not: なぜホワイトリスト形式かつセットでモデルIDを管理するのか？
# ティオさんのご指摘通り、ブラックリスト形式ではGoogleが将来的に新たな特殊モデルを追加した際にすり抜けて表示されるリスクがあるため、
# ホワイトリストで厳格に許可モデル（gemini-3.1-flash-lite）のみを制限するため。またセットで保持することでO(1)の高速な存在判定を行えるため。
SUPPORTED_MODELS_WHITELIST = {"gemini-3.1-flash-lite"}

# APIキー検証前のオフライン起動用フォールバック表示名マッピング
# Why not: なぜオフライン用のフォールバック表示名マッピングを定義するのか？
# アプリ起動時のようにAPIキー検証前でネットワーク通信を行っていない段階でも、
# 保存済みのモデルIDに対応する表示名を安全かつ親切にUIドロップダウンに表示させ、滑らかなUXを提供するため。
OFFLINE_DISPLAY_NAMES = {
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite"
}

class NakAIApp:
    def __init__(self, root):
        self.root = root
        self.root.title("NakAI (仲居) - Prototype")
        self.root.geometry("600x650")  # 助言ウィンドウ最適化サイズ
        self.root.minsize(500, 550)
        
        # キューの初期化 (スレッド間のデータ受け渡し用)
        self.text_queue = queue.Queue()
        self.volume_queue = queue.Queue()
        self.gemini_queue = queue.Queue()  # Geminiからの結果受け取り用
        
        # 音声認識エンジンの初期化 
        # ※検証および精度向上のため、デフォルトを "base" モデルに設定
        self.engine = STTEngine(self.text_queue, self.volume_queue, model_name="base")
        
        # Gemini連携エンジンの初期化
        self.gemini_engine = GeminiEngine(self.gemini_queue)
        
        # 通知音プレイヤーの初期化
        self.sound_player = SoundPlayer()
        
        # GUI変数の初期化
        self.selected_device_id = tk.IntVar()
        self.gemini_enabled = tk.BooleanVar(value=True)  # Gemini連携の内部有効フラグ (常時True)
        
        # 表示オプションフラグ (助言は常時ONの主役、文字起こしと補正は表示ON/OFF可能とする)
        self.show_whisper_raw = tk.BooleanVar(value=True)   # Whisper生文字起こし表示
        self.show_ai_corrected = tk.BooleanVar(value=True)  # AI発話補正テキスト表示
        
        # 平仮名・片仮名検証用フラグ (UI部品としては非表示にし、コード側で安全に切り替え可能とする)
        self.is_kana_hira_mode = False 
        self.engine.set_output_mode(self.is_kana_hira_mode)
        
        # 起動時のモデル一覧の初期化 (オフライン用)
        # Why not: なぜ起動時に OFFLINE_DISPLAY_NAMES のコピーを保持するのか？
        # まだAPI接続（キー検証）を行う前のオフライン起動時であっても、
        # 保存されていた設定モデルIDに対応する表示名を安全にコンボボックスへ表示させ、
        # 未検証状態でも快適な初期ロード状態を提供するため。
        self.available_models = OFFLINE_DISPLAY_NAMES.copy()
        
        # APIキーおよびモデル設定取得
        # Why not: なぜ保存済みのモデル名も取得するのか？
        # 起動時に前回の選択モデルを自動で復元し、利用者が毎回モデルを再選択する手数を解消するため。
        # Why not: なぜ KeyringManager ではなく ConfigManager なのか？
        # ティオさんのご指摘通り、一般設定であるモデル名をKeyring（機密情報用）に同居させるのは設計意図に反するため、通常のJSON設定ファイル用クラスに切り分けて管理する。
        # Why not: なぜmodel_var（Tkinter変数）に格納する際に表示名（display_name）に変換するのか？
        # ティオさんのご指摘に従い、設定画面上にはユーザーフレンドリーで親切な表示名（例: Gemini 3.1 Flash-Lite）を表示し、直感的にモデルを認識させるため。
        self.api_key_val = tk.StringVar(value=KeyringManager.load_api_key())
        loaded_model_id = ConfigManager.load_model_name()
        display_name = self.available_models.get(loaded_model_id, "")
        self.model_var = tk.StringVar(value=display_name)
        self.api_key_valid = False  # APIキー接続検証完了フラグ（初期状態は無効）
        self.settings_expanded = False  # 設定エリアの展開フラグ
        self.last_gemini_request_time = 0.0  # レート制限回避用の前回のAPI送信時刻
        self.gemini_text_buffer = []  # クールダウン中に発生した発話を蓄積する文脈バッファ
        
        # 感度調整用パラメータのデフォルト値
        self.open_threshold_val = tk.DoubleVar(value=0.015)
        self.close_threshold_val = tk.DoubleVar(value=0.008)
        self.silence_duration_val = tk.DoubleVar(value=3.0)  # 黒子モデル用にデフォルトを3.0秒に延長
        
        # GUIスタイルの設定 (モダンな見た目の適用)
        self.style = ttk.Style()
        self.style.theme_use("clam")
        
        # ウィンドウを画面中央に配置
        self.root.eval('tk::PlaceWindow . center')
        
        # ウィジェットの構築
        self._create_widgets()
        
        # バックグラウンドでのモデルロード状況の監視開始
        self.root.after(500, self._check_model_loading)
        
        # スレッドキューのポーリング（更新確認）監視開始
        self.root.after(100, self._poll_queues)
        
        # 起動時のAPIキー自動接続検証
        # Why not: なぜ起動後1秒待ってから自動検証するのか？
        # 起動直後のUI描画やWhisper初期読み込みタスクのCPU負荷と干渉せず、
        # アプリ全体が完全に起動完了した落ち着いたタイミングで滑らかに接続確認を行うため。
        if self.api_key_val.get().strip():
            self.root.after(1000, lambda: self._fetch_latest_models(is_startup=True))

    def _create_widgets(self):
        # メインコンテナフレーム
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # ==========================================
        # 1. 接続・基本設定エリア (アコーディオン付き)
        # ==========================================
        top_frame = ttk.LabelFrame(main_frame, text=" 接続・基本設定 ", padding="10")
        top_frame.pack(fill=tk.X, pady=(0, 10))
        
        # ステータスラベル (モデル名を動的に表示)
        self.status_label = ttk.Label(
            top_frame, 
            text=f"[ステータス]: Whisper({self.engine.model_name}) モデルをロード中... (初回はダウンロードが発生します)",
            foreground="#D35400",  # オレンジ調の警告色
            font=("Helvetica", 9, "bold")
        )
        self.status_label.pack(anchor=tk.W, pady=(0, 8))
        
        # マイクデバイス選択
        dev_frame = ttk.Frame(top_frame)
        dev_frame.pack(fill=tk.X, pady=(0, 6))
        
        ttk.Label(dev_frame, text="マイクデバイス:").pack(side=tk.LEFT, padx=(0, 5))
        self.device_combo = ttk.Combobox(dev_frame, state="readonly")
        self.device_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # デバイス一覧をエンジンから取得して格納
        self.devices = self.engine.get_devices()
        device_names = [name for _, name in self.devices]
        self.device_combo['values'] = device_names
        if device_names:
            self.device_combo.current(0)
            
        # アコーディオン用トグルボタン
        self.accordion_btn = ttk.Button(top_frame, text="▶ LLM (Gemini) 連携設定を開く", command=self._toggle_settings)
        self.accordion_btn.pack(fill=tk.X, pady=(4, 0))
        
        # アコーディオン本体フレーム (初期状態は非表示)
        self.settings_frame = ttk.Frame(top_frame, padding=(5, 8))
        
        # APIキー入力行
        key_line = ttk.Frame(self.settings_frame)
        key_line.pack(fill=tk.X, pady=4)
        ttk.Label(key_line, text="Gemini APIキー:").pack(side=tk.LEFT, padx=(0, 5))
        self.key_entry = ttk.Entry(key_line, textvariable=self.api_key_val, show="*", width=30)
        self.key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(key_line, text="保存", command=self._save_api_key, width=6).pack(side=tk.RIGHT)
        
        # モデル選択行
        model_line = ttk.Frame(self.settings_frame)
        model_line.pack(fill=tk.X, pady=4)
        ttk.Label(model_line, text="使用LLMモデル:").pack(side=tk.LEFT, padx=(0, 5))
        self.model_combo = ttk.Combobox(model_line, textvariable=self.model_var, state="readonly")
        # Why not: なぜ available_models の値（表示名）一覧をあらかじめセットするのか？
        # 起動直後のAPI未接続状態であっても、オフライン用マップに登録された許可モデル（表示名）を
        # 初期選択肢として提供し、ユーザーに空でない状態を提示して安心感を与えるため。
        # Why not: なぜ「更新」ボタンを設置しないのか？
        # ティオさんのご指摘通り、ドロップダウンでモデルを選択した時点で config.json に即時保存され、
        # またAPIキー保存時・起動時に自動でモデル一覧フェッチが走るため、手動ボタンは冗長であり排除するため。
        self.model_combo['values'] = list(self.available_models.values())
        self.model_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_changed)
        
        # タイムライン表示オプション
        opt_lbl = ttk.Label(self.settings_frame, text="タイムライン表示オプション:", font=("Helvetica", 9, "bold"))
        opt_lbl.pack(anchor=tk.W, pady=(6, 2))
        
        self.whisper_chk = ttk.Checkbutton(
            self.settings_frame, 
            text="Whisperの生文字起こしを画面に表示する", 
            variable=self.show_whisper_raw
        )
        self.whisper_chk.pack(anchor=tk.W, pady=2, padx=10)
        
        self.corrected_chk = ttk.Checkbutton(
            self.settings_frame, 
            text="AIによる発話補正テキストを画面に表示する", 
            variable=self.show_ai_corrected
        )
        self.corrected_chk.pack(anchor=tk.W, pady=2, padx=10)
            
        # ==========================================
        # 2. ノイズゲート・感度調整エリア (インジケーター & スライダー)
        # ==========================================
        config_frame = ttk.LabelFrame(main_frame, text=" ノイズゲート・感度調整 ", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 10))
        
        # リアルタイム音量インジケーター (音量メーター)
        vol_frame = ttk.Frame(config_frame)
        vol_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 他のスライダーラベルと幅 (width=22) を完全に統一
        ttk.Label(vol_frame, text="現在の音量 (入力レベル):", width=22, anchor=tk.W).pack(side=tk.LEFT)
        
        # 音量メーターの最大値を100に設定
        self.vol_bar = ttk.Progressbar(vol_frame, orient=tk.HORIZONTAL, mode='determinate', maximum=100)
        self.vol_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # 右端のスライダー数値ラベルと幅 (width=6) を一致させるためのダミー空白
        ttk.Label(vol_frame, text="", width=6).pack(side=tk.RIGHT)
        
        # スライダー：開放しきい値 (発話開始判定)
        open_frame = ttk.Frame(config_frame)
        open_frame.pack(fill=tk.X, pady=3)
        ttk.Label(open_frame, text="開放しきい値 (発話開始):", width=22, anchor=tk.W).pack(side=tk.LEFT)
        self.open_scale = ttk.Scale(
            open_frame, from_=0.001, to=0.100, 
            variable=self.open_threshold_val,
            command=self._on_threshold_changed
        )
        self.open_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.open_lbl = ttk.Label(open_frame, text="0.015", width=6, anchor=tk.E)
        self.open_lbl.pack(side=tk.RIGHT)
        
        # スライダー：閉鎖しきい値 (発話継続・終了判定)
        close_frame = ttk.Frame(config_frame)
        close_frame.pack(fill=tk.X, pady=3)
        ttk.Label(close_frame, text="閉鎖しきい値 (発話終了):", width=22, anchor=tk.W).pack(side=tk.LEFT)
        self.close_scale = ttk.Scale(
            close_frame, from_=0.001, to=0.100, 
            variable=self.close_threshold_val,
            command=self._on_threshold_changed
        )
        self.close_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.close_lbl = ttk.Label(close_frame, text="0.008", width=6, anchor=tk.E)
        self.close_lbl.pack(side=tk.RIGHT)
        
        # スライダー：無音判定時間
        silence_frame = ttk.Frame(config_frame)
        silence_frame.pack(fill=tk.X, pady=3)
        ttk.Label(silence_frame, text="無音判定時間 (秒):", width=22, anchor=tk.W).pack(side=tk.LEFT)
        self.silence_scale = ttk.Scale(
            silence_frame, from_=0.5, to=5.0,  # 黒子モデル用に上限を5.0秒へ拡張
            variable=self.silence_duration_val,
            command=self._on_threshold_changed
        )
        self.silence_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.silence_lbl = ttk.Label(silence_frame, text="3.0s", width=6, anchor=tk.E)
        self.silence_lbl.pack(side=tk.RIGHT)
        
        # ==========================================
        # 3. 制御ボタンエリア
        # ==========================================
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Why not: なぜ「音声認識開始」ではなく「配信開始」にするのか？
        # 音声認識だけでなく、Geminiのchatsセッション管理のライフサイクルをこのボタン1つで完全に同期・連動させるため。
        self.start_btn = ttk.Button(btn_frame, text=" 配信開始 ", command=self._start_delivery, state=tk.DISABLED)
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.stop_btn = ttk.Button(btn_frame, text=" 配信終了 ", command=self._stop_delivery, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5, 0))
        
        # ==========================================
        # 4. リアルタイム・タイムライン表示エリア (助言ウィンドウ)
        # ==========================================
        text_frame = ttk.LabelFrame(main_frame, text=" AI実況支援タイムライン (助言ウィンドウ) ", padding="10")
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        # スクロールバー
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 統合テキストエリア
        self.text_area = tk.Text(
            text_frame, 
            wrap=tk.WORD, 
            yscrollcommand=scrollbar.set,
            font=("Meiryo", 10),
            bg="#F9F9F6",  # 目に優しいオフホワイト
            fg="#2C3E50",  # 深みのある紺
            padx=12,
            pady=12
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)
        self.text_area.configure(state=tk.DISABLED)  # 読み取り専用設定
        scrollbar.config(command=self.text_area.yview)
        
        # スタイリッシュなビジュアルタグの設定 (色のコントラストと視認性を最適化、フラット左寄せに統一)
        self.text_area.tag_config("whisper", foreground="#7F8C8D", font=("Meiryo", 10))  # 生起こしは落ち着いたグレーで脇役化
        self.text_area.tag_config("ai_corrected", foreground="#95A5A6", font=("Meiryo", 9, "italic"))  # 補正も控えめな斜体
        self.text_area.tag_config("ai_advice", foreground="#8E44AD", font=("Meiryo", 10, "bold"))      # 上品な紫の太字で常時ONの「主役」を引き立てる

    def _toggle_settings(self):
        """アコーディオン設定パネルの開閉を切り替える"""
        if self.settings_expanded:
            self.settings_frame.pack_forget()
            self.accordion_btn.config(text="▶ LLM (Gemini) 連携設定を開く")
            self.settings_expanded = False
        else:
            self.settings_frame.pack(fill=tk.X, after=self.accordion_btn)
            self.accordion_btn.config(text="▼ LLM (Gemini) 連携設定を閉じる")
            self.settings_expanded = True

    def _save_api_key(self):
        """入力されたAPIキーの有効性を接続テストで検証し、成功すれば安全に保存する"""
        key = self.api_key_val.get().strip()
        if not key:
            messagebox.showwarning("警告", "APIキーを入力してください。")
            return
            
        # 非同期接続検証をキック（保存アクション=True）
        # Why not: なぜ保存の前に接続テストを走らせるのか？
        # ティオさんのご指摘通り、無効なキーを誤って保存するのを完全に防ぎ、
        # 確実にAPI接続が通る状態を保証してから設定を完了させるため。
        self._fetch_latest_models(target_key=key, is_save_action=True)

    def _on_model_changed(self, event=None):
        """モデル選択が変更された際に安全に設定をconfig.jsonに永続保存し、配信ボタンを連動制御する"""
        selected_display = self.model_var.get()
        # Why not: なぜ available_models の値を走査してモデルIDを特定するのか？
        # available_models は { model_id: display_name } 構造で統一しているため、
        # 表示名からIDを引くには値側を走査する必要がある。構造を統一することで
        # 起動時の復元検証をID基準で安全に行え、表記ブレによる誤検知を完全に排除できるため。
        selected_model_id = ""
        for m_id, m_disp in self.available_models.items():
            if m_disp == selected_display:
                selected_model_id = m_id
                break
        if selected_model_id:
            # Why not: なぜ保存先に Keyring ではなく ConfigManager を使うのか？
            # 資格情報マネージャー（Keyring）は機密性の高い資格情報を保護するための保管庫であり、
            # 単なるモデルの名称設定を保存するのはアンチパターンのため、通常のJSON設定ファイルに分離する。
            ConfigManager.save_model_name(selected_model_id)
            print(f"[UI] ユーザーによって選択されたモデルIDをJSON設定ファイルに保存しました: {selected_model_id}")
            
            # Why not: なぜモデル変更時に配信開始ボタンの活性化をチェックするのか？
            # ティオさんのご指摘通り、モデル未設定状態からユーザーが手動でモデルを選んだ瞬間に、
            # Whisperロード完了などの他条件が揃っていれば即座に「配信開始」ボタンを押せるようにし、自然なUXを提供するため。
            if self.api_key_valid and self.engine.model_loaded.is_set() and self.engine.model is not None:
                self.status_label.config(
                    text=f"[ステータス]: 準備完了 (Whisper準備完了 & Gemini({selected_display})接続成功)", 
                    foreground="#27AE60"
                )
                self.start_btn.config(state=tk.NORMAL)

    def _check_model_loading(self):
        """非同期でのWhisperモデルのロード完了を監視する"""
        if self.engine.model_loaded.is_set():
            if self.engine.model is not None:
                # Whisperロード成功
                # Why not: なぜ LLM APIキーの有効性と、モデルの選択状態の双方と連動させるのか？
                # ティオさんのご指摘通り、APIキーだけでなく使用モデル名も確実に有効かつ選択された状態でのみ
                # 配信ボタンをアクティブにし、不完全な構成での起動によるエラーを確実に防ぐため。
                current_model = self.model_var.get()
                if self.api_key_valid and current_model and current_model.strip():
                    self.status_label.config(
                        text=f"[ステータス]: 準備完了 (Whisper準備完了 & Gemini({current_model})接続成功)", 
                        foreground="#27AE60"
                    )
                    self.start_btn.config(state=tk.NORMAL)
                elif self.api_key_valid:
                    self.status_label.config(
                        text="[ステータス]: モデル未選択 (Whisper準備完了。接続設定からLLMモデルを選択してください)", 
                        foreground="#D35400"
                    )
                    self.start_btn.config(state=tk.DISABLED)
                else:
                    self.status_label.config(
                        text="[ステータス]: 接続待ち (Whisper準備完了。接続設定からAPIキーを保存してください)", 
                        foreground="#D35400"
                    )
                    self.start_btn.config(state=tk.DISABLED)
            else:
                # ロード失敗
                self.status_label.config(
                    text=f"[ステータス]: エラー (モデルロード失敗: {self.engine.model_loading_error})", 
                    foreground="#C0392B"
                )
                messagebox.showerror("モデルロードエラー", f"モデルのダウンロード・ロード中にエラーが発生しました:\n{self.engine.model_loading_error}")
        else:
            self.root.after(500, self._check_model_loading)

    def _on_threshold_changed(self, event=None):
        """スライダーの値が変更された際のコールバック (リアルタイムにエンジンへ反映)"""
        open_val = self.open_threshold_val.get()
        close_val = self.close_threshold_val.get()
        silence_val = self.silence_duration_val.get()
        
        # 表示用ラベルの更新 (しきい値は小数点以下3桁、秒数は1桁)
        self.open_lbl.config(text=f"{open_val:.3f}")
        self.close_lbl.config(text=f"{close_val:.3f}")
        self.silence_lbl.config(text=f"{silence_val:.1f}s")
        
        # エンジンへ動的にしきい値を通知
        self.engine.set_thresholds(open_val, close_val, silence_val)

    def _start_delivery(self):
        """配信開始処理 (chatsセッション開始 & 音声認識起動)"""
        selected_index = self.device_combo.current()
        if selected_index < 0:
            messagebox.showwarning("警告", "マイクデバイスを選択してください。")
            return
        
        # 1. APIキーのセキュアな取得
        api_key = self.api_key_val.get().strip()
        if not api_key:
            api_key = KeyringManager.load_api_key()
            
        if not api_key:
            messagebox.showwarning("警告", "APIキーが設定されていません。\nLLM連携設定からAPIキーを入力して保存してください。")
            return
            
        # 2. Gemini chats セッションの開始
        # Why not: なぜここでAPIキーとUIで選択されたモデル名を渡してセッションを起動するのか？
        # 配信の開始と同時に、ユーザーがドロップダウンで選択した最新のLLMモデルでコンテキスト保持用のセッションを初期化し、
        # シームレスかつ正確な実況開始体験を提供する設計にするため。
        # Why not: なぜ表示名ではなくモデルIDを available_models から走査して engine に渡すのか？
        # available_models は { model_id: display_name } 構造に統一しているため、
        # 表示名から値を走査してIDを特定する。config.json に保存されるのもIDであり、
        # Gemini APIの呼び出しにも正式なモデルIDを渡す必要があるため。
        display_name = self.model_var.get()
        model_id = ""
        for m_id, m_disp in self.available_models.items():
            if m_disp == display_name:
                model_id = m_id
                break
        
        if not model_id or not model_id.strip():
            messagebox.showerror("エラー", "有効なLLMモデルが設定されていません。\n接続設定から使用するモデルを選択し直してください。")
            return
            
        success = self.gemini_engine.start_session(api_key, model_id)
        if not success:
            messagebox.showerror("エラー", f"配信セッション({display_name})の初期化に失敗しました。\nAPIキーが有効であるか、また選択されたモデルが現在も有効であるかご確認ください。")
            return
            
        # バッファと前回のスマートデバウンス要求時間をクリーンにリセット
        self.gemini_text_buffer = []
        self.last_gemini_request_time = 0.0 # 開始直後の最初の発言は30秒待たずに即時送信可能にする
        
        # 3. 音声認識エンジンの起動
        dev_id = self.devices[selected_index][0]
        try:
            # 閾値と動作モードを最新のUI状態にしてからエンジンを起動
            self._on_threshold_changed()
            self.engine.set_output_mode(self.is_kana_hira_mode)
            self.engine.start(dev_id)
            
            # UI状態の切り替え
            self.status_label.config(text="[ステータス]: 配信中 (AI仲居がお耳を傾けています)", foreground="#2980B9") # ブルー
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
            self.device_combo.config(state=tk.DISABLED)
            
            # タイムラインにセッション開始案内を表示 (完全左寄せ)
            self.text_area.configure(state=tk.NORMAL)
            self.text_area.insert(tk.END, "🔔 --- 配信セッションが開始されました --- 🔔\n\n", "whisper")
            self.text_area.see(tk.END)
            self.text_area.configure(state=tk.DISABLED)
        except Exception as e:
            messagebox.showerror("エラー", f"配信開始（音声認識）に失敗しました:\n{e}")

    def _stop_delivery(self):
        """配信終了処理 (音声認識停止 & 最終要約の非同期生成)"""
        try:
            # 1. 音声認識の停止
            self.engine.stop()
            
            # UI状態の切り替え
            self.status_label.config(text="[ステータス]: 配信終了 (本日も素晴らしい実況でございました)", foreground="#27AE60")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.device_combo.config(state="readonly")
            
            # 音量メーターを完全にリセット
            self.vol_bar['value'] = 0
            
            # タイムラインにセッション終了案内と、要約作成中メッセージを表示 (完全左寄せ)
            self.text_area.configure(state=tk.NORMAL)
            self.text_area.insert(tk.END, "🔔 --- 配信セッションが終了しました --- 🔔\n", "whisper")
            self.text_area.insert(tk.END, "✨ AI仲居が本日の実況を振り返り、要約（サマリー）をまとめております。少々お待ちくださいませ...\n\n", "ai_corrected")
            self.text_area.see(tk.END)
            self.text_area.configure(state=tk.DISABLED)
            
            # 2. Geminiセッションを終了し、配信全体の最終要約（サマリー）の非同期生成を要求
            # Why not: なぜここで非同期に要約させるのか？
            # 要約生成には数秒のGemini API呼び出し時間がかかるため、UIスレッドをフリーズさせずに、
            # バックグラウンドで安全に生成して結果キュー経由でタイムラインに流すため。
            self.gemini_engine.end_session()
        except Exception as e:
            messagebox.showerror("エラー", f"配信終了処理中にエラーが発生しました:\n{e}")

    def _poll_queues(self):
        """STTエンジン、リアルタイム音量、およびGeminiの補正結果を周期的に回収してタイムラインを更新する (100ms周期)"""
        
        # 1. 音量メーターの更新
        try:
            latest_volume = None
            while True:
                latest_volume = self.volume_queue.get_nowait()
        except queue.Empty:
            pass
        
        if latest_volume is not None:
            scaled_volume = min(int(latest_volume * 1000), 100)
            self.vol_bar['value'] = scaled_volume

        # 2. 文字起こしテキストの受信＆Gemini分析のトリガー
        try:
            while True:
                new_text = self.text_queue.get_nowait()
                print(f"\n[UI] 文字起こしキューから受信: \"{new_text}\"")
                
                # タイムラインエリアへの生テキスト追記 (表示オプションONの場合のみ、完全左寄せ)
                if self.show_whisper_raw.get():
                    self.text_area.configure(state=tk.NORMAL)
                    self.text_area.insert(tk.END, new_text + "\n", "whisper")
                    self.text_area.see(tk.END)  # 最下部へ自動スクロール
                    self.text_area.configure(state=tk.DISABLED)
                
                # Gemini連携用の文脈バッファへの蓄積
                # タイムスタンプや推論時間部分 ("[22:28:23] [推論: 0.38s] ") を削り、純粋な認識テキストだけにする
                parts = new_text.split("] ", 2)
                raw_recognized_text = parts[2].strip() if len(parts) >= 3 else new_text
                print(f"[UI] Gemini送信対象テキスト抽出: \"{raw_recognized_text}\"")
                
                # 最低文字数制限 (4文字未満はノイズや細切れ発話としてバッファにも追加せずスキップ)
                if len(raw_recognized_text) < 4:
                    print(f"[UI] 文字数が少なすぎるため（\"{raw_recognized_text}\": {len(raw_recognized_text)}文字）、バッファ追加をスキップします。")
                    continue
                
                # 有効な発言をバッファに追加して結合保持
                self.gemini_text_buffer.append(raw_recognized_text)
                print(f"[UI] テキストをマージ用バッファに蓄積しました (現在のバッファ件数: {len(self.gemini_text_buffer)}件)")
                    
                # スマートデバウンス判定 (前回の送信から30.0秒以上空いている場合のみ、溜まったバッファをマージ送信)
                current_time = time.time()
                elapsed = current_time - self.last_gemini_request_time
                if elapsed < 30.0:
                    print(f"[UI] クールダウン中のため送信保留（経過時間: {elapsed:.2f}s < 30.0s）。現在のバッファ内容: {self.gemini_text_buffer}")
                    continue
                
                # 30秒以上経過している場合、結合して送信 (助言機能はメインの根幹のため常時キック)
                full_merged_text = " ".join(self.gemini_text_buffer)
                print(f"[UI] スマートデバウンス制限クリア！溜まったバッファを結合してGeminiへ送信します (結合テキスト: \"{full_merged_text}\")")
                
                self.last_gemini_request_time = current_time
                self.gemini_engine.request_analysis(full_merged_text)
                
                # 送信したためバッファをクリア
                self.gemini_text_buffer = []
        except queue.Empty:
            pass

        # 3. Gemini分析結果の回収とタイムラインへの埋め込み
        try:
            while True:
                success, corrected, advice, duration = self.gemini_queue.get_nowait()
                print(f"[UI] Gemini結果をQueueから受信! (成否: {success}, 補正: \"{corrected}\", 時間: {duration:.2f}s, タイプ: {advice})")
                
                self.text_area.configure(state=tk.NORMAL)
                
                if success:
                    if advice == "summary":
                        # 最終配信サマリーの描画処理
                        # Why not: 配信全体の締めくくりとして、通常の助言タイムラインと
                        # 明確に区別された美しい区切り線と装飾でサマリーを描画し、特別感を演出します。
                        self.text_area.insert(tk.END, "\n" + "─" * 40 + "\n", "whisper")
                        self.text_area.insert(tk.END, f"✨ 本日の配信サマリー [作成時間: {duration:.2f}s]\n", "ai_advice")
                        self.text_area.insert(tk.END, f"{corrected}\n", "whisper")
                        self.text_area.insert(tk.END, "─" * 40 + "\n\n", "whisper")
                        
                        # ステータスを準備完了に戻す
                        self.status_label.config(text="[ステータス]: 準備完了 (配信要約を生成しました)", foreground="#27AE60")
                    else:
                        # 1. AI発話補正テキストの描画 (表示オプションONの場合のみ、完全左寄せ)
                        if self.show_ai_corrected.get():
                            self.text_area.insert(tk.END, f"✨ AI補正 [思考: {duration:.2f}s]: {corrected}\n", "ai_corrected")
                        
                        # 2. AI助言の描画 (常時表示されるタイムラインの主役、完全左寄せ)
                        self.text_area.insert(tk.END, f"💬 仲居助言: {advice}\n", "ai_advice")
                        
                        # 仲居助言がタイムラインに表示された瞬間に通知音を再生
                        self.sound_player.play()
                        self.text_area.insert(tk.END, "\n", "whisper")  # タイムラインの塊ごとの適度な余白
                else:
                    if advice == "summary":
                        # サマリー生成エラー時
                        self.text_area.insert(tk.END, "\n" + "─" * 40 + "\n", "whisper")
                        self.text_area.insert(tk.END, f"⚠️ AI仲居: {corrected}\n", "ai_corrected")
                        self.text_area.insert(tk.END, "─" * 40 + "\n\n", "whisper")
                        self.status_label.config(text="[ステータス]: 準備完了 (要約の生成に失敗しました)", foreground="#C0392B")
                    else:
                        # 恒久エラー発生時: 配信者用の優しい情緒的なエラーテキストを表示 (完全左寄せ)
                        self.text_area.insert(tk.END, f"⚠️ AI仲居: {corrected}\n", "ai_corrected")
                        self.text_area.insert(tk.END, "\n", "whisper")
                    
                self.text_area.see(tk.END)  # 最下部へ自動スクロール
                self.text_area.configure(state=tk.DISABLED)
                print("[UI] 共通タイムラインへの書き込みと表示が完了しました。")
        except queue.Empty:
            pass
        
        # 100ms後に再スケジュール
        if self.root.winfo_exists():
            self.root.after(100, self._poll_queues)

    def _fetch_latest_models(self, target_key=None, is_save_action=False, is_startup=False):
        """
        Gemini APIから最新の利用可能モデル一覧を非同期で取得する（APIキー検証も兼ねる）
        """
        api_key = target_key if target_key else self.api_key_val.get().strip()
        if not api_key:
            if not is_startup:
                messagebox.showwarning("警告", "APIキーが設定されていません。")
            return
            
        if is_save_action:
            self.status_label.config(text="[ステータス]: APIキーの有効性を検証中...", foreground="#2980B9")
            
        def run():
            try:
                from google import genai
                client = genai.Client(api_key=api_key)
                
                # models.list を呼び出し、例外が出なければAPIキーは有効とみなせる
                # Why not: なぜモデル名と表示名のタプルを蓄積してUIスレッドに引き渡すのか？
                # ティオさんからご提案いただいた通り、GenAI SDK の Model オブジェクトには正確な name (ID) と
                # display_name (表示名) の両方が含まれるため、これをペアでメインスレッドに安全に送り、
                # UI用ドロップダウンとIDの逆引き不要な動的マッピングを直接構築できるようにするため。
                model_pairs = []
                for m in client.models.list():
                    name = m.name
                    if name.startswith("models/"):
                        name = name[7:]
                    display_name = m.display_name if m.display_name else name
                    model_pairs.append((name, display_name))
                            
                if not model_pairs:
                    raise Exception("利用可能なGeminiモデルが見つかりませんでした。")
                    
                model_pairs.sort(key=lambda x: x[1])  # 表示名順でソート
                
                # メインUIスレッドへ安全に更新通知
                self.root.after(0, lambda: self._on_validation_success(api_key, model_pairs, is_save_action, is_startup))
            except Exception as e:
                err_str = str(e)
                self.root.after(0, lambda: self._on_validation_failure(err_str, is_save_action, is_startup))
                
        import threading
        threading.Thread(target=run, daemon=True).start()

    def _on_validation_success(self, api_key, model_pairs, is_save_action, is_startup):
        """APIキー接続検証の成功時ハンドラ"""
        self.api_key_valid = True
        
        if is_save_action:
            KeyringManager.save_api_key(api_key)
            messagebox.showinfo("成功", "APIキーの有効性を確認し、Windows資格情報マネージャーに安全に保存しました！")
            
        # APIが実際に返したモデル一覧とホワイトリストを交差させ、{ model_id: display_name } の動的マッピングを構築
        # Why not: なぜ available_models を { model_id: display_name } 構造で統一するのか？
        # config.json に保存されるのは不変のモデルID（例: gemini-3.1-flash-lite）であり、
        # 再起動時の復元検証もID基準で行うことで、Google側の display_name 表記変更や微細なブレに影響されず
        # 100%安全にモデルを復元できるようにするため。
        self.available_models = {}
        for m_id, m_disp in model_pairs:
            if m_id in SUPPORTED_MODELS_WHITELIST:
                self.available_models[m_id] = m_disp
                
        available_displays = list(self.available_models.values())
        self.model_combo['values'] = available_displays
        
        # Why not: なぜ表示名ではなくモデルIDで有効性を検証するのか？
        # config.json に保存されているのはモデルIDであり、IDで突き合わせることで
        # display_name の微細な表記ブレによる「無効なモデル」誤検知を完全に排除するため。
        loaded_model_id = ConfigManager.load_model_name()
        if loaded_model_id and loaded_model_id in self.available_models:
            # IDに対応する最新の表示名を取得してComboboxにセット
            restored_display = self.available_models[loaded_model_id]
            self.model_combo.set(restored_display)
            self.model_var.set(restored_display)
            
            # 有効なモデルがセットされていて、かつWhisperのロードが終わっていれば配信ボタンを活性化
            if self.engine.model_loaded.is_set() and self.engine.model is not None:
                self.status_label.config(
                    text=f"[ステータス]: 準備完了 (Whisper準備完了 & Gemini({restored_display})接続成功)", 
                    foreground="#27AE60"
                )
                self.start_btn.config(state=tk.NORMAL)
            else:
                self.status_label.config(
                    text=f"[ステータス]: Whisper({self.engine.model_name}) モデルをロード中... (LLM接続完了)", 
                    foreground="#D35400"
                )
                self.start_btn.config(state=tk.DISABLED)
        else:
            # 過去に設定したモデルが存在しないか、空である場合
            self.model_combo.set("")
            ConfigManager.save_model_name("")
            self.start_btn.config(state=tk.DISABLED)
            
            if not loaded_model_id:
                self.status_label.config(
                    text="[ステータス]: モデル未選択 (接続設定から使用するLLMモデルを選択してください)", 
                    foreground="#D35400"
                )
            else:
                self.status_label.config(
                    text=f"[ステータス]: 警告 (過去に設定したモデル '{loaded_model_id}' は無効です。選択し直してください)", 
                    foreground="#C0392B"
                )

    def _on_validation_failure(self, err_msg, is_save_action, is_startup):
        """APIキー接続検証の失敗時ハンドラ"""
        self.api_key_valid = False
        self.start_btn.config(state=tk.DISABLED)
        
        if is_save_action:
            messagebox.showerror("検証失敗", f"APIキーの検証に失敗しました。正しいキーかご確認ください:\n\n{err_msg}")
            self.status_label.config(text="[ステータス]: 接続エラー (無効なAPIキーです)", foreground="#C0392B")
        else:
            if is_startup:
                self.status_label.config(text="[ステータス]: 接続エラー (保存済みAPIキーの自動接続検証に失敗しました。キーを再設定してください)", foreground="#C0392B")
            else:
                messagebox.showerror("エラー", f"最新モデル一覧の取得に失敗しました:\n{err_msg}")

def main():
    root = tk.Tk()
    app = NakAIApp(root)
    
    # 終了時のクリーンアップ処理
    def on_closing():
        if app.engine.is_running:
            app.engine.stop()
        root.destroy()
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()
