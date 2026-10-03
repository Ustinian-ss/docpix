"""``python -m app`` 入口，同时也是 PyInstaller 打包时的入口脚本。

用绝对导入（``from app.cli import main``）：PyInstaller 会把本文件当顶层脚本
执行，此时包内的相对导入会失败。
"""

from app.cli import main

if __name__ == "__main__":
    main()
