"""导入任何生产模块前隔离测试进程的数据目录，退出时自动清理。"""
import os
import tempfile


# 必须覆盖继承的开发环境值；setdefault 无法保护已有的开发数据库。
_data_directory = tempfile.TemporaryDirectory(prefix="codebuddy-tests-")
os.environ["CODEBUDDY_DATA_DIR"] = _data_directory.name
