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

# 配信のうまさの定義
NAK_AI_DEF_GOOD_COMMENTARY = """
第1層：基礎統制（非言語・物理的レイヤー）

- 音声表現力（パラランゲージの統制）： 発声、滑舌、声のトーン、テンポの維持。平時の「穏やかさ」を物理的な音波として維持する技術。
- 「間」と沈黙の統制（時間的空白）： 情報の空白を意図的にデザインし、視聴者の咀嚼を促し、次に発する言葉の価値を高める時間的コントロール。

第2層：情報処理（言語・認知レイヤー）

- 実況力と修辞力： 事象を遅延なく音声化し、必要に応じて適切な比喩や抽象化を用いて、視聴者の知的好奇心を刺激する表現力。
- 規範的省略（ネガティブ・コントロール/意味的空白）： 空間の秩序を乱すノイズ（ネタバレ、批判、無用な論争の種）を瞬時に察知し、あえて言語化せずに「捨てる」という知的な取捨選択。

第3層：感情と構成（エンターテインメント・レイヤー）

- 反応力とメタ認知： 予想外の事象に対して的確に反応し、客観的な視点から「フリとオチ」などの落差を生み出す技術。
- 構成力： 目標の設定や伏線の回収を通じて、その日の配信全体に一つの文脈（ストーリー）を持たせる技術。

第4層：相互作用（コミュニケーション・レイヤー）

- 代読と解釈（テキストへの文脈付与）： 無機質なテキストコメントに対し、自身の音声表現を通じて「自枠の空気に合った解釈（ニュアンス）」を与え、空間に安全に配置する翻訳技術。
- 役割の構築： コメントとの間でボケとツッコミなどの相互作用を生み出し、コミュニティの文化を共創する技術。

"""

# NakAI 配信アシスタント用システムプロンプト (システム指示)
NAK_AI_SYSTEM_INSTRUCTION = f"""
あなたは{NAK_AI_ROLE}です。
あなたの目的は、配信者がゲーム実況配信を上達できるように、適度に助言をすることです。

【前提条件】
・入力されるテキストは、配信者の実況音声をWhisperで文字起こししたものです。
・マイクのノイズやWhisperの誤変換により、同音異義語の誤字（例：「五時」←「誤字」）や聞き間違いが多く含まれています。前後の文脈を読み解き、自然で美しい漢字交じりの正しい日本語に補正してください。
・実況が一定時間(3-5秒)止まったタイミングで入力されます。

【実況のうまさ】
以下のような観点で、実況のうまさを評価することとします。

{NAK_AI_DEF_GOOD_COMMENTARY}

【タスク】
1. 【誤字・文脈補正】: 配信者の実況テキストを、自然な日本語に修正してください（corrected_text に格納）。
2. 【状況確認（助言）】: 直前までの実況文脈から、配信者が必要そうな助言をそっと提供してください（advice に格納）。
"""

# 要約用プロンプト
NAK_AI_SUMMERY_INSTRUCTION = f"""
あなたは{NAK_AI_ROLE}です。
あなたの目的は、配信者がゲーム実況配信を上達できるように、適度に助言をすることです。

以下の【本日の配信者の実況発言履歴】を読み、本日の配信での実況がどのくらいうまかったか評価してください。
その評価の根拠を具体的な発言にからめて説明してください。

【実況のうまさ】
以下のような観点で、実況のうまさを評価することとします。

{NAK_AI_DEF_GOOD_COMMENTARY}

【出力形式】
優しく温かみのある丁寧な言葉遣い（〜でございましたね、など）で、簡潔な箇条書き（3項目程度）で分かりやすく要約・サマリーしてください。
"""

class NakAIAnalysis(BaseModel):
    corrected_text: str = Field(description="前後の文脈を読み解き、自然な日本語に修正したテキスト")
    advice: str = Field(description="配信者がゲームに集中して無言になった際、配信者が必要そうな助言")

class GeminiEngine:
    def __init__(self, gemini_queue: queue.Queue):
        self.gemini_queue = gemini_queue  # 結果をUIスレッドに返すためのQueue
        self.client = None
        self.chat = None

    def start_session(self, api_key: str, model_name: str) -> bool:
        """
        配信開始時に新しいchatsセッションを起動する。
        Why not: なぜ初回メッセージでシステム設定を送らないのか？
        初回メッセージで設定を送ると、Structured Outputsのスキーマ制約により、APIが無理やりJSONを返そうとしてエラーになる。
        system_instruction（システム指示）に定義を渡すことで、初期メッセージ不要で安全にセッション全体へルールを永続適用できる。
        """
        print(f"\n[GeminiEngine] >>> 配信セッションを開始します。モデル: {model_name}")
        if not api_key:
            print("[GeminiEngine] ERROR: APIキーが指定されていません。")
            return False

        # Why not: なぜ model_name.strip() が空かどうかの判定だけで十分なのか？
        # ティオさんのご指摘通り、呼び出し側の Tkinter 制御部から渡される model_name は常に str であることが保証されており、
        # 冗長な None チェックを行わずとも安全かつシンプルに空値判定を行えるため。
        if not model_name.strip():
            print("[GeminiEngine] ERROR: 有効なLLMモデル名が指定されていません。")
            return False

        # Why not: なぜモデル名をインスタンス変数に保持するのか？
        # 配信中に使っているモデルと、配信終了時の「最終要約（サマリー）」で用いるモデルを完全に一致させ、
        # 異なるモデルが意図せず起動する挙動を防ぐと同時に、開発者が指定したクォータやリミットの枠内に安全に収めるため。
        self.model_name = model_name.strip()

        try:
            self.client = genai.Client(api_key=api_key)
            
            # chats.createを用いてセッションを開始
            self.chat = self.client.chats.create(
                model=self.model_name,
                config=types.GenerateContentConfig(
                    system_instruction=NAK_AI_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=NakAIAnalysis,
                    temperature=0.3, # 補正と助言のブレを抑えるために低めの創造性に設定
                )
            )
            print(f"[GeminiEngine] chatsセッション({self.model_name})が正常に初期化されました。")
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
                f"{NAK_AI_SUMMERY_INSTRUCTION}\n\n"
                f"【本日の配信者の実況発言履歴】:\n{merged_history_text}"
            )
            
            print("[GeminiEngine] 配信要約をGeminiに要求中...")
            
            # 3. 通常のgenerate_contentで要約を取得
            # Why not: なぜchatsセッション上で要約を頼まないのか？
            # chatsセッション上で頼んでしまうと、「要約テキスト」自体がチャット履歴に含まれてしまい、
            # 次回のセッション開始や履歴追跡に悪影響を及ぼすため、要約は単発のgenerate_contentで完全に切り離して実行する。
            # Why not: なぜここでも self.model_name を使用するのか？
            # 配信中に使っているモデルと要約に使用するモデルを完全に同期させ、別モデルが不要にロードされるのを防ぎ、
            # 開発者が指定したクォータや挙動を一元管理できるようにするため。
            summary_response = self.client.models.generate_content(
                model=self.model_name,
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
