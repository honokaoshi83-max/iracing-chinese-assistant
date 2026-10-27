# 发布脚本

按版本号参数运行，不再为每个版本复制脚本。路径在 `release_common.py` 中统一定义：

- 源码：本仓库根目录。
- Inno Setup：仓库旁的 `development/tools/inno-portable/tools/ISCC.exe`。
- 构建目录：`D:/iRacing安装构建/<版本>`；安装文件、GitHub 分卷和增量补丁：`D:/iRacing中文播报助手安装备份/`。

| 步骤 | 命令 |
| --- | --- |
| 完整安装版 | `release_build.py 0.3.0 --base 0.2.8` |
| 增量补丁 | `release_patches.py 0.3.0 --from 0.2.4 0.2.5 0.2.6 0.2.7 0.2.8` |
| 隔离安装验收 | `release_accept.py 0.3.0 full`，`release_accept.py 0.3.0 patches --from …` |
| 安装后启停验收 | `release_lifecycle.py 0.3.0 <安装目录>` |
| 汇总记录 | `release_finalize.py 0.3.0 --commit … --backend … --desktop … --ci …` |
| GitHub 附件核对 | `release_verify_github.py 0.3.0` |

旧的按版本复制脚本与历史日志保存在 `development/_archive/`。

`scripts/stage_offline_release.py` 会把仓库中所有被跟踪的文件放进安装包，本目录也包括在内。
