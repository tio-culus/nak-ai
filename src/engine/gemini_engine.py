import sys
import time
import threading
import queue
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from engine.keyring_manager import KeyringManager

# ----------------------------------------------------
# NakAI 配信アシスタントの基本ロール（ペルソナ）定義
# Why not: なぜ基本ロールを独立した変数として定義するのか？
# システム指示（system_instruction）と配信終了時の要約プロンプトの双方で、
# AIのキャラクターやアイデンティティ定義が一切ブレないよう一貫して統一管理するため。
# ----------------------------------------------------
NAK_AI_ROLE = "ゲーム実況配信者を影から慎ましく支えるアシスタント「NakAI（ナカイ）」"

# NakAI 配信アシスタント用システムプロンプト (システム指示)
NAK_AI_SYSTEM_INSTRUCTION = f"""
あなたは{NAK_AI_ROLE}です。

【前提条件】
・入力されるテキストは、配信者の実況音声をWhisperで文字起こししたものです。
・マイクのノイズやWhisperの誤変換により、同音異義語の誤字（例：「五時」←「誤字」）や聞き間違いが多く含まれています。前後の文脈を読み解き、自然で美しい漢字交じりの正しい日本語に補正してください。

【タスク】
1. 【誤字・文脈補正】: 配信者の実況テキストを、自然で美しい日本語に修正してください（corrected_text に格納）。
2. 【状況確認（助言）】: 実況が止まった（沈黙した）タイミングで、直前までの実況文脈から、配信者が直前の状況を思い出せるような「状況を確認するメッセージ」をそっと提供してください（advice に格納）。
"""

class NakAIAnalysis(BaseModel):
    corrected_text: str = Field(description="前後の文脈を読み解き、自然で美しい漢字交じりの正しい日本語に修正したテキスト")
    advice: str = Field(description="配信者がゲームに集中して無言になった際、直前まで話していた内容（コンテキスト）を忘れないよう、一歩引いて「その後どうなりましたでしょうか？」等とそっとお耳打ちする1文の状況確認メッセージ")

class GeminiEngine:
    def __init__(self, gemini_queue: queue.Queue):
        self.gemini_queue = gemini_queue  # 結果をUIスレッドに返すためのQueue
        self.client = None
        self.chat = None

    def start_session(self, api_key: str) -> bool:
        """
        配信開始時に新しいchatsセッションを起動する。
        Why not: なぜ初回メッセージでシステム設定を送らないのか？
        初回メッセージで設定を送ると、Structured Outputsのスキーマ制約により、APIが無理やりJSONを返そうとしてエラーになる。
        system_instruction（システム指示）に定義を渡すことで、初期メッセージ不要で安全にセッション全体へルールを永続適用できる。
        """
        print("\n[GeminiEngine] >>> 配信セッションを開始します。")
        if not api_key:
            print("[GeminiEngine] ERROR: APIキーが指定されていません。")
            return False

        try:
            self.client = genai.Client(api_key=api_key)
            
            # chats.createを用いてセッションを開始
            self.chat = self.client.chats.create(
                model='gemini-2.5-flash',
                config=types.GenerateContentConfig(
                    system_instruction=NAK_AI_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=NakAIAnalysis,
                    temperature=0.3, # 補正と助言のブレを抑えるために低めの創造性に設定
                )
            )
            print("[GeminiEngine] chatsセッションが正常に初期化されました。")
            return True
        except Exception as e:
            print(f"[GeminiEngine] chatsセッション初期化エラー: {e}", file=sys.stderr)
            return False

    def request_analysis(self, recognized_text: str):
        """音声認識テキストの補正＆お耳打ち要求を非同期で実行する"""
        print(f"\n[GeminiEngine] >>> 分析要求を受け取りました: \"{recognized_text}\"")
        
        if not self.chat:
            err_msg = "配信セッションが開始されていません。配信開始ボタンを押してください。"
            print(f"[GeminiEngine] ERROR: {err_msg}", file=sys.stderr)
            self.gemini_queue.put((False, err_msg, "", 0.0))
            return
            
        # UIをロックさせないため、スレッドを起動してバックグラウンドでリクエストを投げる
        threading.Thread(
            target=self._run_analysis_async, 
            args=(recognized_text,), 
            daemon=True
        ).start()

    def _run_analysis_async(self, recognized_text: str):
        """バックグラウンドスレッドでGemini chats.send_message を呼び出す実体"""
        api_start_time = time.time()

        try:
            print("[GeminiEngine] Gemini chatsセッションへメッセージを送信中... (Structured Outputs適用)")
            
            # Why not: なぜ generate_content ではなく chats.send_message なのか？
            # chats.send_message を通じて呼び出すことで、SDK側でこれまでの会話履歴（文脈）が自動で累積保持され、
            # 直前までの文脈を100%考慮した状況確認メッセージ（助言）を生成させることができるため。
            response = self.chat.send_message(message=recognized_text)
            
            duration = time.time() - api_start_time
            print(f"[GeminiEngine] 応答を受信しました (所要時間: {duration:.2f}秒)。")
            
            parsed_data = response.parsed
            if parsed_data and isinstance(parsed_data, NakAIAnalysis):
                print(f"[GeminiEngine] 解析成功:")
                print(f"  - 補正テキスト: \"{parsed_data.corrected_text}\"")
                print(f"  - お耳打ち助言  : \"{parsed_data.advice}\"")
                self.gemini_queue.put((True, parsed_data.corrected_text, parsed_data.advice, duration))
            else:
                # parsedが取れなかった場合のJSON直接パースによる堅牢なフォールバック
                import json
                raw_text = response.text
                if raw_text.startswith("```json"):
                    raw_text = raw_text.replace("```json", "", 1).rstrip("` \n")
                elif raw_text.startswith("```"):
                    raw_text = raw_text.replace("```", "", 1).rstrip("` \n")
                
                data = json.loads(raw_text)
                corrected = data.get("corrected_text", recognized_text)
                advice = data.get("advice", "お耳を澄ませております。")
                self.gemini_queue.put((True, corrected, advice, duration))
                
        except Exception as e:
            duration = time.time() - api_start_time
            print(f"[GeminiEngine] ERROR in _run_analysis_async: {e}", file=sys.stderr)
            
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str:
                friendly_msg = "ただいま他のお客様で混み合っているようです。少し間を置いてから、お耳を傾けに参りますね。"
            elif "429" in err_str or "quota" in err_str.lower() or "limit" in err_str.lower():
                friendly_msg = "少しお喋りが活発すぎるようです！仲居が追いつくよう、実況のペースをゆっくり見守りますね。"
            elif "API_KEY_INVALID" in err_str or "invalid" in err_str.lower():
                friendly_msg = "ご登録いただいたAPIキーが正しくないようです。接続設定からキーをもう一度ご確認ください。"
            else:
                friendly_msg = "少しお耳の調子が悪いようです（ネットワーク状況をご確認ください）。"
                
            self.gemini_queue.put((False, friendly_msg, "", duration))

    def end_session(self):
        """配信終了時に非同期でセッションをクローズし、配信全体の要約を生成する"""
        if not self.chat:
            return
            
        print("[GeminiEngine] >>> 配信終了にともない、配信全体の要約を生成します。")
        threading.Thread(
            target=self._run_summary_async,
            daemon=True
        ).start()

    def _run_summary_async(self):
        """バックグラウンドでこれまでの実況履歴を取り出して最終要約を生成する"""
        api_start_time = time.time()
        try:
            # 1. これまでのchats履歴から、配信者の発言（Userロールのもの）のみを抽出
            # Why not: なぜAI自身の助言や補正テキストを含めないのか？
            # AIが出力した「補正された文章」や「仲居の問いかけ」まで要約の素材に混ぜると、要約の中にAI自身の言葉が混入してノイズになり、
            # 配信者自身の純粋な実況ハイライト要約がブレてしまうため、配信者ご自身の生発話のみを抽出する。
            history = self.chat.get_history()
            user_texts = []
            for turn in history:
                if turn.role == "user":
                    part_text = " ".join([part.text for part in turn.parts if part.text])
                    if part_text:
                        user_texts.append(part_text)
            
            if not user_texts:
                duration = time.time() - api_start_time
                # Why not: なぜ corrected=要約本文, advice="summary" なのか？
                # main.py の _poll_queues が期待しているデータ構造（タプル要素の割り当て）に完璧に適合させ、
                # 前後の通信インターフェースとの整合性を保ち、描画バグを防ぐため。
                self.gemini_queue.put((True, "本日は沈黙を守られた配信でございましたね。静かな時間もまた一興でございます。", "summary", duration))
                return
                
            merged_history_text = "\n".join(user_texts)
            
            # 2. 要約用プロンプトの構築
            prompt = (
                f"あなたは{NAK_AI_ROLE}です。\n"
                "以下の【本日の配信者の実況発言履歴】を読み、本日の配信で配信者がどのようなプレイをし、何について語り、どのようなハイライトがあったかを、"
                "優しく温かみのある丁寧な言葉遣い（〜でございましたね、など）で、簡潔な箇条書き（3項目程度）で分かりやすく要約・サマリーしてください。\n\n"
                f"【本日の配信者の実況発言履歴】:\n{merged_history_text}"
            )
            
            print("[GeminiEngine] 配信要約をGeminiに要求中...")
            
            # 3. 通常のgenerate_contentで要約を取得
            # Why not: なぜchatsセッション上で要約を頼まないのか？
            # chatsセッション上で頼んでしまうと、「要約テキスト」自体がチャット履歴に含まれてしまい、
            # 次回のセッション開始や履歴追跡に悪影響を及ぼすため、要約は単発のgenerate_contentで完全に切り離して実行する。
            summary_response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7, # 要約の表現に豊かな温かみを持たせるため、少し高めに設定
                )
            )
            
            duration = time.time() - api_start_time
            summary_text = summary_response.text
            
            # キューに送信 (corrected=summary_text, advice="summary")
            self.gemini_queue.put((True, summary_text, "summary", duration))
            print(f"[GeminiEngine] 配信要約の生成が成功しました (所要時間: {duration:.2f}秒)。")
            
        except Exception as e:
            duration = time.time() - api_start_time
            print(f"[GeminiEngine] ERROR in _run_summary_async: {e}", file=sys.stderr)
            self.gemini_queue.put((True, f"本日の配信もお疲れ様でございました。要約がお作りできませんでしたが、素晴らしいお時間でございました。（エラー: {e}）", "summary", duration))
        finally:
            # 4. セッションを解放
            self.chat = None
            self.client = None
