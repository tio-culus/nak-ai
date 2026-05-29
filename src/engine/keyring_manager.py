import keyring
import sys

class KeyringManager:
    SERVICE_NAME = "NakAI"
    KEY_NAME = "GeminiAPIKey"

    @classmethod
    def save_api_key(cls, api_key: str) -> bool:
        """APIキーをWindows資格情報マネージャーに安全に保存する"""
        try:
            keyring.set_password(cls.SERVICE_NAME, cls.KEY_NAME, api_key)
            return True
        except Exception as e:
            print(f"[KeyringManager] Error saving API key: {e}", file=sys.stderr)
            return False

    @classmethod
    def load_api_key(cls) -> str:
        """Windows資格情報マネージャーからAPIキーを読み込む"""
        try:
            key = keyring.get_password(cls.SERVICE_NAME, cls.KEY_NAME)
            return key if key else ""
        except Exception as e:
            print(f"[KeyringManager] Error loading API key: {e}", file=sys.stderr)
            return ""

    @classmethod
    def delete_api_key(cls) -> bool:
        """Windows資格情報マネージャーからAPIキーを削除する"""
        try:
            keyring.delete_password(cls.SERVICE_NAME, cls.KEY_NAME)
            return True
        except keyring.errors.PasswordDeleteError:
            # すでにキーが登録されていない場合は正常終了とする
            return True
        except Exception as e:
            print(f"[KeyringManager] Error deleting API key: {e}", file=sys.stderr)
            return False
