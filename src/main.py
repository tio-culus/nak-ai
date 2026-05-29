import time
import queue
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from engine.stt_engine import STTEngine

class NakAIApp:
    def __init__(self, root):
        self.root = root
        self.root.title("NakAI (仲居) - Prototype")
        self.root.geometry("600x650")
        self.root.minsize(500, 550)
        
        # キューの初期化 (スレッド間のデータ受け渡し用)
        self.text_queue = queue.Queue()
        self.volume_queue = queue.Queue()
        
        # 音声認識エンジンの初期化 
        # ※検証および精度向上のため、デフォルトを "base" モデルに設定
        self.engine = STTEngine(self.text_queue, self.volume_queue, model_name="base")
        
        # GUI変数の初期化
        self.selected_device_id = tk.IntVar()
        
        # 感度調整用パラメータのデフォルト値
        self.open_threshold_val = tk.DoubleVar(value=0.015)
        self.close_threshold_val = tk.DoubleVar(value=0.008)
        self.silence_duration_val = tk.DoubleVar(value=1.2)
        
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
        # 1. 接続設定エリア (ステータス・デバイス選択)
        # ==========================================
        top_frame = ttk.LabelFrame(main_frame, text=" 接続設定 ", padding="10")
        top_frame.pack(fill=tk.X, pady=(0, 10))
        
        # ステータスラベル (モデル名を動的に表示)
        self.status_label = ttk.Label(
            top_frame, 
            text=f"[ステータス]: Whisper({self.engine.model_name}) モデルをロード中... (初回のみダウンロードが発生します)",
            foreground="#D35400",  # オレンジ調の警告色
            font=("Helvetica", 9, "bold")
        )
        self.status_label.pack(anchor=tk.W, pady=(0, 10))
        
        # マイクデバイス選択
        dev_frame = ttk.Frame(top_frame)
        dev_frame.pack(fill=tk.X)
        
        ttk.Label(dev_frame, text="マイクデバイス:").pack(side=tk.LEFT, padx=(0, 5))
        self.device_combo = ttk.Combobox(dev_frame, state="readonly")
        self.device_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        # デバイス一覧をエンジンから取得して格納
        self.devices = self.engine.get_devices()
        device_names = [name for _, name in self.devices]
        self.device_combo['values'] = device_names
        if device_names:
            self.device_combo.current(0)
            
        # ==========================================
        # 2. ノイズゲート・感度調整エリア (インジケーター & スライダー)
        # ==========================================
        config_frame = ttk.LabelFrame(main_frame, text=" ノイズゲート・感度調整 ", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 10))
        
        # リアルタイム音量インジケーター (音量メーター)
        vol_frame = ttk.Frame(config_frame)
        vol_frame.pack(fill=tk.X, pady=(0, 12))
        
        # 他のスライダーラベルと幅 (width=22) を完全に統一
        ttk.Label(vol_frame, text="現在の音量 (入力レベル):", width=22, anchor=tk.W).pack(side=tk.LEFT)
        
        # 音量メーターの最大値を100に設定 (0.000〜0.100 のしきい値スケールと同期)
        self.vol_bar = ttk.Progressbar(vol_frame, orient=tk.HORIZONTAL, mode='determinate', maximum=100)
        self.vol_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # 右端のスライダー数値ラベルと幅 (width=6) を一致させるためのダミー空白
        ttk.Label(vol_frame, text="", width=6).pack(side=tk.RIGHT)
        
        # スライダー：開放しきい値 (発話開始判定)
        open_frame = ttk.Frame(config_frame)
        open_frame.pack(fill=tk.X, pady=4)
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
        close_frame.pack(fill=tk.X, pady=4)
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
        silence_frame.pack(fill=tk.X, pady=4)
        ttk.Label(silence_frame, text="無音判定時間 (秒):", width=22, anchor=tk.W).pack(side=tk.LEFT)
        self.silence_scale = ttk.Scale(
            silence_frame, from_=0.5, to=3.0, 
            variable=self.silence_duration_val,
            command=self._on_threshold_changed
        )
        self.silence_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.silence_lbl = ttk.Label(silence_frame, text="1.2s", width=6, anchor=tk.E)
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
        # 4. 文字起こし結果表示エリア (タイムスタンプ付き)
        # ==========================================
        text_frame = ttk.LabelFrame(main_frame, text=" リアルタイム文字起こし結果 (タイムスタンプ/推論時間付き) ", padding="10")
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        # スクロールバー
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # テキストエリア
        # ※フォントを等幅ラテン用のConsolasから、日本語が圧倒的に綺麗な「メイリオ (Meiryo)」へ変更
        self.text_area = tk.Text(
            text_frame, 
            wrap=tk.WORD, 
            yscrollcommand=scrollbar.set,
            font=("Meiryo", 10),
            bg="#F9F9F6",  # 目に優しいオフホワイト
            fg="#2C3E50",  # 深みのある紺
            padx=10,
            pady=10
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)
        self.text_area.configure(state=tk.DISABLED)  # 読み取り専用設定
        scrollbar.config(command=self.text_area.yview)

    def _check_model_loading(self):
        """非同期でのWhisperモデルのロード完了を監視する"""
        if self.engine.model_loaded.is_set():
            if self.engine.model is not None:
                # ロード成功
                self.status_label.config(
                    text=f"[ステータス]: 準備完了 (モデル {self.engine.model_name} のロードが完了しました)", 
                    foreground="#27AE60"  # 美しいグリーン
                )
                self.start_btn.config(state=tk.NORMAL)
            else:
                # ロード失敗
                self.status_label.config(
                    text=f"[ステータス]: エラー (モデルロード失敗: {self.engine.model_loading_error})", 
                    foreground="#C0392B"  # 鮮やかなレッド
                )
                messagebox.showerror("モデルロードエラー", f"モデルのダウンロード・ロード中にエラーが発生しました:\n{self.engine.model_loading_error}")
        else:
            # まだロード中なら、0.5秒後に再度チェック
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
            # 閾値を最新のUI状態にしてからエンジンを起動
            self._on_threshold_changed()
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
        """STTエンジンからの認識結果およびリアルタイム音量を周期的に回収し、UIを更新する (100ms周期)"""
        
        # 1. 音量メーターの更新
        try:
            # メーター表示の遅延を防ぐため、キューがたまっている場合は最新の値のみを採用 (残りは破棄)
            latest_volume = None
            while True:
                latest_volume = self.volume_queue.get_nowait()
        except queue.Empty:
            pass
        
        if latest_volume is not None:
            # RMS値（0.000〜0.100が主要範囲）を Progressbarの範囲 (0〜100) に物理同期スケーリング
            # RMSの 0.100 が Progressbarの最大 100 となるように、1000倍して値を設定します。
            # これにより、メーターの位置としきい値スライダーの位置が「1対1で完全シンクロ」します。
            scaled_volume = min(int(latest_volume * 1000), 100)
            self.vol_bar['value'] = scaled_volume

        # 2. 文字起こしテキストの更新
        try:
            while True:
                new_text = self.text_queue.get_nowait()
                
                # テキストエリアへの追記
                self.text_area.configure(state=tk.NORMAL)
                self.text_area.insert(tk.END, new_text + "\n")
                self.text_area.see(tk.END)  # 最下部へ自動スクロール
                self.text_area.configure(state=tk.DISABLED)
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
