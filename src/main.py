import tkinter as tk

def main():
    # メインウィンドウの作成
    root = tk.Tk()
    root.title("NakAI (仲居) - Prototype")
    root.geometry("400x200")
    
    # ウィンドウを画面中央に配置
    root.eval('tk::PlaceWindow . center')
    
    # ラベルの作成と配置
    label = tk.Label(
        root, 
        text="Hello, World! (NakAI Prototype)", 
        font=("Helvetica", 14, "bold"),
        fg="#1A73E8"  # スマートなGoogleブルー調の色を選択
    )
    label.pack(expand=True)
    
    # アプリケーションのメインループ開始
    root.mainloop()

if __name__ == "__main__":
    main()
