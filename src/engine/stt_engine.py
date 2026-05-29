import os
# Hugging Face のシンボリックリンク警告を非表示にする
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import sys
import time
import queue
import threading
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

class STTEngine:
    def __init__(self, text_queue, volume_queue, model_name="base"):
        self.text_queue = text_queue      # UIへの文字起こし受け渡し用
        self.volume_queue = volume_queue  # UIへのリアルタイム音量受け渡し用
        self.model_name = model_name      # "tiny" や "base" など
        
        # 音響判定パラメータ (デフォルト値)
        self.open_threshold = 0.015       # 開放しきい値 (発話開始の音量)
        self.close_threshold = 0.008      # 閉鎖しきい値 (発話継続・無音判定の音量)
        self.silence_duration = 3.0       # 無音判定時間 (秒) - 黒子モデル用にデフォルトを3.0秒に延長
        self.sample_rate = 16000          # 16kHz
        self.block_duration = 0.1         # 100msブロック
        self.block_size = int(self.sample_rate * self.block_duration)
        
        # 状態管理
        self.is_running = False
        self.state = "silent"             # "silent" または "speaking"
        self.voice_buffer = []            # 音声データを一時的に溜めるリスト
        self.silence_frames_limit = int(self.silence_duration / self.block_duration)
        self.silence_counter = 0
        
        # スレッド & キュー
        self.raw_audio_queue = queue.Queue() # コールバック -> VAD判定
        self.stt_task_queue = queue.Queue()  # VAD判定 -> STT推論
        
        self.model = None
        self.model_loaded = threading.Event()
        self.model_loading_error = None
        
        # バックグラウンドでのモデル非同期ロード開始 (UIをフリーズさせないため)
        self.is_kana_hira_mode = False  # ひらがな・カタカナ誘導モードのフラグ (検証用)
        threading.Thread(target=self._load_model_async, daemon=True).start()

    def _load_model_async(self):
        """Whisperモデルを非同期にロードする"""
        try:
            # CPUで動く指定モデルをロード (int8量子化でメモリとCPU負荷を劇的に削減)
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            self.model_loaded.set()
        except Exception as e:
            self.model_loading_error = str(e)
            self.model_loaded.set()  # エラー時もブロック解除のためにセット

    def set_thresholds(self, open_threshold, close_threshold, silence_duration):
        """UIからしきい値を動的に変更するためのセッター"""
        self.open_threshold = open_threshold
        self.close_threshold = close_threshold
        self.silence_duration = silence_duration
        self.silence_frames_limit = int(self.silence_duration / self.block_duration)

    def set_output_mode(self, is_kana_hira_mode):
        """音声認識結果をひらがな・カタカナのみの誘導にするかどうかのセッター"""
        self.is_kana_hira_mode = is_kana_hira_mode

    def get_devices(self):
        """システムに接続されているマイクデバイスの一覧を取得する"""
        try:
            devices = sd.query_devices()
            input_devices = []
            for i, dev in enumerate(devices):
                if dev['max_input_channels'] > 0:
                    # デバイス名とインデックスを格納
                    input_devices.append((i, f"{dev['name']} (Input: {dev['max_input_channels']}ch)"))
            return input_devices
        except Exception as e:
            print(f"Error querying audio devices: {e}", file=sys.stderr)
            return []

    def start(self, device_index):
        """マイクキャプチャと認識処理を開始する"""
        if self.is_running:
            return
        
        if not self.model_loaded.is_set():
            raise RuntimeError("Whisperモデルのロードが完了していません。")
        
        if self.model is None:
            raise RuntimeError(f"Whisperモデルのロードに失敗しています: {self.model_loading_error}")
        
        self.is_running = True
        self.state = "silent"
        self.voice_buffer = []
        self.silence_counter = 0
        
        # キューのクリア
        while not self.raw_audio_queue.empty():
            self.raw_audio_queue.get()
        while not self.stt_task_queue.empty():
            self.stt_task_queue.get()

        # 音声キャプチャスレッドの起動 (sounddevice stream)
        self.stream = sd.InputStream(
            device=device_index,
            channels=1,
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            callback=self._audio_callback
        )
        self.stream.start()

        # VAD判定ループとSTTループの起動
        self.vad_thread = threading.Thread(target=self._audio_processing_loop, daemon=True)
        self.stt_thread = threading.Thread(target=self._stt_loop, daemon=True)
        self.vad_thread.start()
        self.stt_thread.start()

    def stop(self):
        """マイクキャプチャと認識処理を停止する"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # ストリームの停止
        if hasattr(self, 'stream'):
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print(f"Error stopping stream: {e}", file=sys.stderr)
            
        # ダミーデータを投げてスレッドのループを抜けるトリガーにする
        self.raw_audio_queue.put(None)
        self.stt_task_queue.put(None)
        
        # スレッドの終了待ち
        if hasattr(self, 'vad_thread'):
            self.vad_thread.join(timeout=1.0)
        if hasattr(self, 'stt_thread'):
            self.stt_thread.join(timeout=1.0)

    def _audio_callback(self, indata, frames, time_info, status):
        """sounddeviceの入力コールバック (100msごとに呼び出される)"""
        if status:
            print(f"Audio stream status: {status}", file=sys.stderr)
        if self.is_running:
            # データをコピーしてスレッドセーフなQueueに投げる
            self.raw_audio_queue.put(indata.copy())

    def _audio_processing_loop(self):
        """VAD判定ループ (マイクデータから発話・無音を判定し、バッファを制御)"""
        while self.is_running:
            try:
                chunk = self.raw_audio_queue.get()
                if chunk is None:  # 終了シグナル
                    break
                
                # 1. 音量 (RMS値) の計算
                # チャンネルはモノラルなので平坦化して計算
                audio_data = chunk.flatten()
                rms = np.sqrt(np.mean(audio_data**2)) if len(audio_data) > 0 else 0.0
                
                # リアルタイムの音量をUIスレッドへ通知 (0.0〜1.0の範囲)
                self.volume_queue.put(rms)
                
                # 2. ヒステリシス付きノイズゲートVAD状態遷移ロジック
                if self.state == "silent":
                    # 待機中: 開放しきい値を超えたら「発話開始」
                    if rms >= self.open_threshold:
                        self.state = "speaking"
                        self.voice_buffer = [audio_data]
                        self.silence_counter = 0
                
                elif self.state == "speaking":
                    # 発話中: 音声データをバッファに追加
                    self.voice_buffer.append(audio_data)
                    
                    # セーフティガード: 3秒以上の沈黙がないまま45秒以上喋り続けた場合、
                    # バッファの過度な肥大化とハルシネーションを防ぐため強制的に一度区切る
                    max_frames = int(45.0 / self.block_duration)
                    if len(self.voice_buffer) >= max_frames:
                        print("[STTEngine] Safety Guard: 連続音声が45秒に達したため、強制的に一度区切って文字起こしを実行します。")
                        full_audio = np.concatenate(self.voice_buffer)
                        self.stt_task_queue.put(full_audio)
                        
                        # バッファのみをクリアし、発話状態（speaking）のまま次の音声を溜め始める
                        self.voice_buffer = []
                        self.silence_counter = 0
                        continue
                    
                    # 閉鎖しきい値を下回った場合、無音フレームをカウント
                    if rms < self.close_threshold:
                        self.silence_counter += 1
                        
                        # 指定された無音判定時間に達したら「発話終了」
                        if self.silence_counter >= self.silence_frames_limit:
                            # 溜まった音声を統合してSTTキューに送る
                            full_audio = np.concatenate(self.voice_buffer)
                            self.stt_task_queue.put(full_audio)
                            
                            # リセットして待機状態に戻る
                            self.state = "silent"
                            self.voice_buffer = []
                            self.silence_counter = 0
                    else:
                        # 閾値を超えている間は無音カウントをリセットして発話を維持
                        self.silence_counter = 0
                        
            except Exception as e:
                print(f"Error in audio processing loop: {e}", file=sys.stderr)
                time.sleep(0.1)

    def _stt_loop(self):
        """STT推論ループ (VADで切り出された音声をバックグラウンドで文字起こし)"""
        while self.is_running:
            try:
                audio_data = self.stt_task_queue.get()
                if audio_data is None:  # 終了シグナル
                    break
                
                # 処理時間（推論遅延）の計測開始
                start_time = time.time()
                
                # 音声認識の実行
                # 指定モデルを使用。日本語に固定することで速度と精度を最適化
                # ひらがな・カタカナ誘導モードの場合は強力な初期プロンプトを設定して漢字を抑制
                prompt_val = None
                if self.is_kana_hira_mode:
                    prompt_val = "ひらがな と カタカナ のみで かいてください。かんじは つかわないで。こんにちは、きょうのてんきははれです。"
                
                segments, info = self.model.transcribe(
                    audio_data, 
                    beam_size=5, 
                    language="ja",
                    vad_filter=True,  # 内部のSilero VADを補助として有効化
                    initial_prompt=prompt_val
                )
                
                # 認識されたセグメントのテキストを連結
                text_list = [segment.text for segment in segments]
                recognized_text = "".join(text_list).strip()
                
                # 処理時間の計測終了
                duration = time.time() - start_time
                
                # テキストが空でなければ、タイムスタンプおよび推論遅延時間付きでUIスレッドへ通知
                if recognized_text:
                    # タイムスタンプの生成 [HH:MM:SS]
                    timestamp = time.strftime("[%H:%M:%S]")
                    formatted_result = f"{timestamp} [推論: {duration:.2f}s] {recognized_text}"
                    self.text_queue.put(formatted_result)
                    
            except Exception as e:
                print(f"Error in STT loop: {e}", file=sys.stderr)
                time.sleep(0.1)
