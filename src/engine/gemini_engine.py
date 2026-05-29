import sys
import time
import threading
import queue
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from engine.keyring_manager import KeyringManager

class NakAIAnalysis(BaseModel):
    corrected_text: str = Field(description="前後の文脈を読み解き、自然で美しい漢字交じりの日本語に修正したテキスト")
    advice: str = Field(description="補正後の発話に基づき、実況を盛り上げるための合いの手、関連する豆知識、あるいは配信者へのアドバイス（1〜2文でフランクに）")

class GeminiEngine:
    def __init__(self, gemini_queue: queue.Queue):
        self.gemini_queue = gemini_queue  # 結果をUIスレッドに返すためのQueue

    def request_analysis(self, recognized_text: str, is_kana_hira_mode: bool = False):
        """音声認識テキストの補正＆アドバイス要求を非同期で実行する"""
        print(f"\n[GeminiEngine] >>> 分析要求を受け取りました: \"{recognized_text}\" (平仮名カタカナ誘導: {is_kana_hira_mode})")
        
        # UIをロックさせないため、スレッドを起動してバックグラウンドでリクエストを投げる
        threading.Thread(
            target=self._run_analysis_async, 
            args=(recognized_text, is_kana_hira_mode), 
            daemon=True
        ).start()

    def _run_analysis_async(self, recognized_text: str, is_kana_hira_mode: bool):
        """バックグラウンドスレッドでGemini APIを呼び出す実体"""
        # 1. APIキーの読み込み
        print("[GeminiEngine] Windows資格情報マネージャーからAPIキーを読み込んでいます...")
        api_key = KeyringManager.load_api_key()
        if not api_key:
            err_msg = "APIキーが設定されていないため、サポートをお休みしています。接続設定からAPIキーを保存してください。"
            print(f"[GeminiEngine] ERROR: {err_msg}", file=sys.stderr)
            self.gemini_queue.put((False, err_msg, "", 0.0))
            return
        
        # APIキーの頭文字だけを表示して安全にロードを確認
        masked_key = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "..."
        print(f"[GeminiEngine] APIキーを正常にロードしました (キー: {masked_key})")

        # 応答時間計測の開始
        api_start_time = time.time()

        try:
            # 2. プロンプトの構築
            if is_kana_hira_mode:
                prompt = (
                    "あなたはプロの配信アシスタント「NakAI（仲居）」です。\n"
                    "以下の【音声認識テキスト】は、配信者のマイク音声をリアルタイムで文字起こししたもので、漢字変換を省いた「ひらがな・カタカナのみ」に近い形になっています。\n\n"
                    "【タスク】\n"
                    "1. 【誤字・文脈補正】: この平仮名・片仮名の並びを、前後の文脈から正確に読み解き、自然で美しい漢字交じりの日本語に変換・補正してください。元の意味や意図を勝手に変更したり、推測できない情報を付け加えたりしないでください。\n"
                    "2. 【配信アドバイス】: 補正後の内容に基づき、配信実況を盛り上げるための短い合いの手、配信に役立つ豆知識、または次のトークの話題（1〜2文程度でフランクに）を提示してください。\n\n"
                    f"【音声認識テキスト】: \"{recognized_text}\""
                )
            else:
                prompt = (
                    "あなたはプロの配信アシスタント「NakAI（仲居）」です。\n"
                    "以下の【音声認識テキスト】は、配信者のマイク音声をリアルタイムで文字起こししたものです。マイクの性質やWhisperの誤変換により、同音異義語の誤字（例：「五時」←「誤字」）や聞き間違いが含まれている可能性があります。\n\n"
                    "【タスク】\n"
                    "1. 【誤字・文脈補正】: 前後の文脈を読み解き、自然で美しい漢字交じりの正しい日本語に修正してください。元の発言の意味や意図を一切変えたり、余計な情報を足したりしないでください。\n"
                    "2. 【配信アドバイス】: 補正後の内容に基づき、配信実況を盛り上げるための短い合いの手、配信に役立つ豆知識、または次のトークの話題（1〜2文程度でフランクに）を提示してください。\n\n"
                    f"【音声認識テキスト】: \"{recognized_text}\""
                )

            print("[GeminiEngine] Gemini API (gemini-2.5-flash) へリクエストを送信中... (Structured Outputs適用)")
            
            # 3. Clientの初期化とリクエスト送信 (Structured Outputs適用)
            client = genai.Client(api_key=api_key)
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=NakAIAnalysis,
                    temperature=0.3,  # 補正精度を安定させるために少し低めに設定
                ),
            )
            
            # 応答時間計測の終了
            duration = time.time() - api_start_time
            print(f"[GeminiEngine] Gemini API から応答を受信しました (所要時間: {duration:.2f}秒)。レスポンスを解析中...")
            
            # 4. レスポンスのパース (google-genai SDK は parsed を自動提供する)
            parsed_data = response.parsed
            if parsed_data and isinstance(parsed_data, NakAIAnalysis):
                print(f"[GeminiEngine] スキーマ解析成功 [思考時間: {duration:.2f}s]:")
                print(f"  - 補正テキスト: \"{parsed_data.corrected_text}\"")
                print(f"  - アドバイス  : \"{parsed_data.advice}\"")
                # UIキューに成功結果を渡す (True, 補正後テキスト, アドバイス, 応答時間)
                self.gemini_queue.put((True, parsed_data.corrected_text, parsed_data.advice, duration))
            else:
                print("[GeminiEngine] WARNING: .parsed が NakAIAnalysis インスタンスとして解釈されませんでした。生のテキストパースを試みます。")
                import json
                raw_text = response.text
                print(f"[GeminiEngine] 受信生テキスト:\n{raw_text}")
                
                # 余計なマークダウンなどを削るためのパース
                if raw_text.startswith("```json"):
                    raw_text = raw_text.replace("```json", "", 1).rstrip("` \n")
                elif raw_text.startswith("```"):
                    raw_text = raw_text.replace("```", "", 1).rstrip("` \n")
                
                data = json.loads(raw_text)
                corrected = data.get("corrected_text", recognized_text)
                advice = data.get("advice", "実況がんばってください！")
                
                print(f"[GeminiEngine] フォールバック解析成功:")
                print(f"  - 補正テキスト: \"{corrected}\"")
                print(f"  - アドバイス  : \"{advice}\"")
                
                self.gemini_queue.put((True, corrected, advice, duration))
                
        except Exception as e:
            duration = time.time() - api_start_time
            print(f"[GeminiEngine] ERROR in _run_analysis_async (経過時間: {duration:.2f}s): {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            
            # エラー内容に応じた情緒的なメッセージへの翻訳ハンドリング
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str:
                friendly_msg = "ただいま他のお客様で混み合っているようです。少し間を置いてから、お耳を傾けに参りますね。"
            elif "429" in err_str or "quota" in err_str.lower() or "limit" in err_str.lower():
                friendly_msg = "少しお喋りが活発すぎるようです！仲居が追いつくよう、実況のペースをゆっくり見守りますね。"
            elif "API_KEY_INVALID" in err_str or "invalid" in err_str.lower():
                friendly_msg = "ご登録いただいたAPIキーが正しくないようです。接続設定からキーをもう一度ご確認ください。"
            else:
                friendly_msg = f"少しお耳の調子が悪いようです（ネットワーク状況をご確認ください）。"
                
            self.gemini_queue.put((False, friendly_msg, "", duration))
