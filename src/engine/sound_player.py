"""
通知音再生モジュール。

仲居助言がタイムラインに表示される際に、通知音（神楽鈴SE）を非同期再生する。
疎結合設計に従い、UIライブラリ（Tkinter等）への依存は一切持たない。

素材情報:
    名称: 神楽鈴02 (Kagura_Suzu02-5.wav)
    提供元: OtoLogic (https://otologic.jp)
    ライセンス: CC BY 4.0
"""

import os
import sys
import winsound


# 通知音WAVファイルのパス（アセットディレクトリからの相対パス）
_SE_RELATIVE_PATH = os.path.join("assets", "sounds", "Kagura_Suzu02-5.wav")


def _resolve_asset_path() -> str:
    """
    通知音WAVファイルの絶対パスを解決する。

    PyInstaller onefile モードでは実行時に一時ディレクトリ（sys._MEIPASS）に
    アセットが展開されるため、開発時とバンドル時の両方で正しいパスを返す。
    """
    # Why not: なぜ sys._MEIPASS を優先的にチェックするのか？
    # PyInstaller の onefile モードでは、バンドルされたデータファイルは
    # sys._MEIPASS 配下の一時ディレクトリに展開される。
    # 開発時には _MEIPASS 属性が存在しないため、__file__ ベースのパスにフォールバックする。
    if getattr(sys, "_MEIPASS", None):
        base_path = sys._MEIPASS
    else:
        # 開発時: このファイルは src/engine/ にあるため、2階層上が src/
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    return os.path.join(base_path, _SE_RELATIVE_PATH)


class SoundPlayer:
    """通知音の再生を管理するクラス。"""

    def __init__(self):
        self._wav_path = _resolve_asset_path()
        self._available = os.path.exists(self._wav_path)

        if not self._available:
            print(
                f"[SoundPlayer] 警告: 通知音ファイルが見つかりません: {self._wav_path}\n"
                f"              'python scripts/download_se.py' を実行してセットアップしてください。"
            )
        else:
            print(f"[SoundPlayer] 通知音を読み込みました: {self._wav_path}")

    def play(self):
        """
        通知音を非同期で再生する。

        SND_ASYNC フラグにより、再生完了を待たずに即座に制御を返す。
        ファイルが存在しない場合は何もしない（クラッシュさせない）。
        """
        if not self._available:
            return

        try:
            # Why not: なぜ SND_ASYNC | SND_FILENAME を使うのか？
            # SND_ASYNC: UIスレッドをブロックせずバックグラウンドで再生するため。
            # SND_FILENAME: メモリ上のリソースではなくファイルパスを指定して再生するため。
            winsound.PlaySound(
                self._wav_path,
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )
        except Exception as e:
            # Why not: なぜ例外を握りつぶしてログだけ出すのか？
            # 通知音は補助的な機能であり、再生失敗でアプリ全体をクラッシュさせるべきではないため。
            print(f"[SoundPlayer] 通知音の再生に失敗しました: {e}")
