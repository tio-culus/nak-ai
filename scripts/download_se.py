#!/usr/bin/env python3
"""
通知音SE（神楽鈴02）のセットアップスクリプト。

OtoLogicから手動でダウンロードしたZIPファイルを検出・解凍し、
MP3をwinsoundで再生可能なWAV形式に変換します。

素材情報:
    名称: 神楽鈴02
    提供元: OtoLogic (https://otologic.jp)
    ライセンス: CC BY 4.0

前提条件:
    - ffmpeg がPATHに存在すること（WAV変換に使用）
    - OtoLogicから「神楽鈴02」のZIPをダウンロード済みであること

使用方法:
    1. OtoLogic (https://otologic.jp) から「神楽鈴02」をダウンロード
    2. 本スクリプトを実行:
       python scripts/download_se.py
"""

import os
import sys
import subprocess
import tempfile
import zipfile

ZIP_FILENAME = "Kagura_Suzu02-mp3.zip"
SOURCE_FILENAME = "Kagura_Suzu02-5.mp3"
OUTPUT_FILENAME = "Kagura_Suzu02-5.wav"


def _get_default_downloads_dir() -> str:
    """OSのデフォルトダウンロードフォルダのパスを返す。"""
    return os.path.join(os.path.expanduser("~"), "Downloads")


def _find_zip() -> str | None:
    """
    ZIPファイルを探索する。見つからなければユーザーにパスを入力させる。

    探索順序:
        1. デフォルトのダウンロードフォルダ (~/Downloads)
        2. ユーザーによるフルパス入力
    """
    # 1. デフォルトのダウンロードフォルダを確認
    downloads_dir = _get_default_downloads_dir()
    default_path = os.path.join(downloads_dir, ZIP_FILENAME)

    if os.path.exists(default_path):
        print(f"✅ ダウンロードフォルダにZIPを検出しました: {default_path}")
        return default_path

    # 2. 見つからない場合、ユーザーにパスを入力させる
    print(f"⚠️  デフォルトのダウンロードフォルダに {ZIP_FILENAME} が見つかりませんでした。")
    print(f"   確認先: {downloads_dir}")
    print()

    while True:
        user_input = input(f"📂 {ZIP_FILENAME} のフルパスを入力してください (終了: q): ").strip()

        if user_input.lower() == "q":
            return None

        # ドラッグ＆ドロップ時に付与される引用符を除去
        user_input = user_input.strip('"').strip("'")

        if os.path.isfile(user_input):
            return user_input

        print(f"   ❌ ファイルが見つかりません: {user_input}")
        print()


def _extract_mp3_from_zip(zip_path: str, output_dir: str) -> str | None:
    """
    ZIPファイルからMP3を解凍して指定ディレクトリに配置する。

    Returns:
        解凍されたMP3ファイルのパス。見つからなければNone。
    """
    print(f"📦 解凍中: {zip_path}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Why not: なぜZIP内のファイル一覧を走査するのか？
        # ZIPの内部構造はアーカイブ作成者の裁量でサブフォルダを含む場合があり、
        # ファイル名の末尾一致で検索することでどちらの構造でも確実に発見するため。
        target_entry = None
        for name in zf.namelist():
            if name.endswith(SOURCE_FILENAME):
                target_entry = name
                break

        if target_entry is None:
            print(f"   ❌ ZIP内に {SOURCE_FILENAME} が見つかりません。")
            return None

        # 対象ファイルのみを解凍して出力ディレクトリに配置
        extracted_path = os.path.join(output_dir, SOURCE_FILENAME)
        with zf.open(target_entry) as src, open(extracted_path, "wb") as dst:
            dst.write(src.read())

    print(f"   解凍完了: {extracted_path}")
    return extracted_path


def _convert_to_wav(mp3_path: str, wav_path: str) -> bool:
    """ffmpegでMP3をWAVに変換する。"""
    print(f"🔄 WAV変換中: {os.path.basename(mp3_path)} → {os.path.basename(wav_path)}")

    result = subprocess.run(
        [
            "ffmpeg",
            "-i", mp3_path,
            "-acodec", "pcm_s16le",  # 16bit PCM (winsound互換)
            "-ar", "44100",          # サンプリングレート 44.1kHz
            "-y",                    # 既存ファイルの上書き確認をスキップ
            wav_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("   ❌ ffmpegによるWAV変換に失敗しました。")
        print(f"   stderr: {result.stderr}")
        return False

    print(f"   変換完了: {wav_path}")
    return True


def main():
    # プロジェクトルートからの出力パスを解決
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    sounds_dir = os.path.join(project_root, "src", "assets", "sounds")
    wav_path = os.path.join(sounds_dir, OUTPUT_FILENAME)

    # 既に変換済みファイルが存在する場合はスキップ
    if os.path.exists(wav_path):
        print(f"✅ {OUTPUT_FILENAME} は既に存在します: {wav_path}")
        return

    # ffmpegの存在確認
    # Why not: なぜ最初にffmpegの存在を確認するのか？
    # ZIP探索やユーザー入力の手間を経た後にffmpegが無いと判明すると
    # ユーザー体験が悪化するため、事前に確認して早期に失敗させる。
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("❌ エラー: ffmpeg が見つかりません。")
        print("   インストールしてPATHに追加してください。")
        print()
        print("   Windows (winget):  winget install Gyan.FFmpeg")
        print("   公式サイト:        https://ffmpeg.org/download.html")
        sys.exit(1)

    # ZIPファイルの探索
    zip_path = _find_zip()
    if zip_path is None:
        print("中断しました。")
        sys.exit(1)

    # Why not: なぜ一時ディレクトリでMP3を処理するのか？
    # src/assets/sounds/ にMP3を配置すると、PyInstallerの datas 設定により
    # WAVとMP3の両方がビルドにバンドルされてしまうため。
    # 一時ディレクトリで解凍・変換し、WAVだけをsounds/に配置する。
    with tempfile.TemporaryDirectory() as tmpdir:
        extracted = _extract_mp3_from_zip(zip_path, tmpdir)
        if extracted is None:
            sys.exit(1)

        os.makedirs(sounds_dir, exist_ok=True)
        if not _convert_to_wav(extracted, wav_path):
            sys.exit(1)

    print()
    print("🎉 通知音SEのセットアップが完了しました！")


if __name__ == "__main__":
    main()
