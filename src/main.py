import time
import queue
import re
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from engine.stt_engine import STTEngine
from engine.keyring_manager import KeyringManager
from engine.gemini_engine import GeminiEngine

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
        
        # GUI変数の初期化
        self.selected_device_id = tk.IntVar()
        self.gemini_enabled = tk.BooleanVar(value=True)  # Gemini連携の内部有効フラグ (常時True)
        
        # 表示オプションフラグ (助言は常時ONの主役、文字起こしと補正は表示ON/OFF可能とする)
        self.show_whisper_raw = tk.BooleanVar(value=True)   # Whisper生文字起こし表示
        self.show_ai_corrected = tk.BooleanVar(value=True)  # AI発話補正テキスト表示
        
        # 平仮名・片仮名検証用フラグ (UI部品としては非表示にし、コード側で安全に切り替え可能とする)
        self.is_kana_hira_mode = False 
        self.engine.set_output_mode(self.is_kana_hira_mode)
        
        # APIキー取得
        self.api_key_val = tk.StringVar(value=KeyringManager.load_api_key())
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
        
        self.start_btn = ttk.Button(btn_frame, text=" 音声認識 開始 ", command=self._start_stt, state=tk.DISABLED)
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.stop_btn = ttk.Button(btn_frame, text=" 停止 ", command=self._stop_stt, state=tk.DISABLED)
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
        """APIキーを資格情報マネージャーに安全に保存する"""
        key = self.api_key_val.get().strip()
        if not key:
            messagebox.showwarning("警告", "APIキーを入力してください。")
            return
            
        success = KeyringManager.save_api_key(key)
        if success:
            messagebox.showinfo("成功", "APIキーをWindows資格情報マネージャーに安全に保存しました。")
        else:
            messagebox.showerror("エラー", "APIキーの保存に失敗しました。")

    def _check_model_loading(self):
        """非同期でのWhisperモデルのロード完了を監視する"""
        if self.engine.model_loaded.is_set():
            if self.engine.model is not None:
                # ロード成功
                self.status_label.config(
                    text=f"[ステータス]: 準備完了 (モデル {self.engine.model_name} のロードが完了しました)", 
                    foreground="#27AE60"
                )
                self.start_btn.config(state=tk.NORMAL)
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

    def _start_stt(self):
        """音声認識の開始処理"""
        selected_index = self.device_combo.current()
        if selected_index < 0:
            messagebox.showwarning("警告", "マイクデバイスを選択してください。")
            return
        
        # エンジン用デバイスインデックスの取得
        dev_id = self.devices[selected_index][0]
        
        try:
            # 閾値と動作モードを最新のUI状態にしてからエンジンを起動
            self._on_threshold_changed()
            self.engine.set_output_mode(self.is_kana_hira_mode)
            self.engine.start(dev_id)
            
            # UI状態の切り替え
            self.status_label.config(text="[ステータス]: 音声認識中 (実況を監視しています)", foreground="#2980B9") # ブルー
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
            self.device_combo.config(state=tk.DISABLED)
        except Exception as e:
            messagebox.showerror("エラー", f"音声キャプチャの開始に失敗しました:\n{e}")

    def _stop_stt(self):
        """音声認識の停止処理"""
        try:
            self.engine.stop()
            
            # UI状態の切り替え
            self.status_label.config(text="[ステータス]: 準備完了 (監視を停止しました)", foreground="#27AE60")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.device_combo.config(state="readonly")
            
            # 音量メーターを完全にリセット
            self.vol_bar['value'] = 0
        except Exception as e:
            messagebox.showerror("エラー", f"音声キャプチャの停止中にエラーが発生しました:\n{e}")

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
                self.gemini_engine.request_analysis(full_merged_text, self.is_kana_hira_mode)
                
                # 送信したためバッファをクリア
                self.gemini_text_buffer = []
        except queue.Empty:
            pass

        # 3. Gemini分析結果の回収とタイムラインへの埋め込み
        try:
            while True:
                success, corrected, advice, duration = self.gemini_queue.get_nowait()
                print(f"[UI] Gemini結果をQueueから受信! (成否: {success}, 補正: \"{corrected}\", 時間: {duration:.2f}s)")
                
                self.text_area.configure(state=tk.NORMAL)
                
                if success:
                    # 1. AI発話補正テキストの描画 (表示オプションONの場合のみ、完全左寄せ)
                    if self.show_ai_corrected.get():
                        self.text_area.insert(tk.END, f"✨ AI補正 [思考: {duration:.2f}s]: {corrected}\n", "ai_corrected")
                    
                    # 2. AI助言の描画 (常時表示されるタイムラインの主役、完全左寄せ)
                    self.text_area.insert(tk.END, f"💬 仲居助言: {advice}\n", "ai_advice")
                    self.text_area.insert(tk.END, "\n", "whisper")  # タイムラインの塊ごとの適度な余白
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
