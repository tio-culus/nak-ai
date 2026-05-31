import os
import json
import sys

class ConfigManager:
    # Why not: なぜ設定ファイルのパスを設定ファイルのクラス変数として定義するのか？
    # 設定ファイルの保存場所を一元管理し、開発中やリリース後も同一のパス規則で確実に読み書きできるようにするため。
    CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

    @classmethod
    def save_model_name(cls, model_name: str) -> bool:
        """モデル名を通常のJSON設定ファイルに保存する"""
        # Why not: なぜ Keyring ではなく JSON ファイルに保存するのか？
        # ティオさんのご指摘通り、Keyring は機密資格情報（パスワードやAPIキー）のための保管庫であり、
        # 一般設定値（モデル名等）を格納するのはその設計意図に反するため、通常のJSON設定ファイルに切り分けて管理する。
        try:
            data = {}
            if os.path.exists(cls.CONFIG_FILE_PATH):
                try:
                    with open(cls.CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    # ファイルが壊れている場合は空データから再構築する
                    data = {}
            
            data["model_name"] = model_name
            
            with open(cls.CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"[ConfigManager] Error saving config: {e}", file=sys.stderr)
            return False

    @classmethod
    def load_model_name(cls) -> str:
        """保存されたモデル名をJSON設定ファイルから読み込む"""
        # Why not: なぜデフォルトで空文字を返すのか？
        # 呼び出し側（main.py）で空の場合のフォールバック値（例: gemini-3.1-flash-lite）や
        # モデル名選択UI上の無効状態を柔軟に制御できるようにし、このクラス側で特定のデフォルトモデル名に依存させないため。
        try:
            if os.path.exists(cls.CONFIG_FILE_PATH):
                with open(cls.CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("model_name", "")
            return ""
        except Exception as e:
            print(f"[ConfigManager] Error loading config: {e}", file=sys.stderr)
            return ""
