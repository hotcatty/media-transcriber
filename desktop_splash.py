#!/usr/bin/env python3
"""First-launch splash while Python/ffmpeg are still being prepared. Stdlib only."""
import sys
import tkinter as tk


def main() -> None:
    family = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
    root = tk.Tk()
    root.title("猫听转文字")
    root.configure(bg="#000000")
    root.minsize(520, 220)
    root.geometry("640x320")
    wrap = tk.Frame(root, bg="#ffffff", padx=40, pady=32)
    wrap.place(relx=0.5, rely=0.5, anchor="center")
    tk.Label(
        wrap,
        text="正在准备本机组件",
        font=(family, 18, "normal"),
        bg="#ffffff",
        fg="#000000",
        anchor="w",
        justify="left",
    ).pack(fill="x")
    tk.Label(
        wrap,
        text="第一次需要自动下载运行环境，大约几分钟。",
        font=(family, 16, "normal"),
        bg="#ffffff",
        fg="#00000099",
        wraplength=440,
        anchor="w",
        justify="left",
    ).pack(fill="x", pady=(12, 0))
    root.mainloop()


if __name__ == "__main__":
    main()
