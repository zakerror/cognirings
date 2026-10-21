# 一次安装请求，完整交付

本文件面向执行安装的Agent。用户不需要分别安装知年轮和last30days；你负责发现、安装和检查依赖。文件复制不是安装流程的终点。

## 完成本技能安装

用户从GitHub安装时，使用宿主的`skill-installer`及其官方helper。仓库为`zakerror/cognirings`，技能在根目录，所以helper使用 `--repo zakerror/cognirings --path . --name cognirings`。已有知年轮时先检查已装副本，不把安装器的“目标已存在”当作必须删除后重装。

读取已安装的`SKILL.md`，然后在同一请求中完成下面的依赖检查。新技能未进入宿主选择列表时，仍可直接读取文件和运行脚本，不必把后半段工作推给用户下一轮。

## 补齐last30days

在实际知年轮根目录运行：

```bash
python3 scripts/ensure_dependencies.py --install
```

默认只读检查（不加`--install`）用于诊断；安装收尾和首次使用应加`--install`，复用已有安装，缺失时通过官方helper补齐。脚本不包含下载实现或上游代码。

如果宿主已经加载了last30days，优先传它的实际根目录，避免在别处重复安装：

```bash
python3 scripts/ensure_dependencies.py --install --last30days-root /actual/last30days/root
```

路径中有空格时使用正确引用。需要指定目标技能目录时用`--skills-dir`；helper不在默认位置时用`--installer`传实际`install-skill-from-github.py`路径。脚本返回已选择的依赖位置，后续绑定这个位置，不猜路径。

新安装使用已验证的上游commit `832d09591568e37dd990ccb9d5c51fb4a403f5ed`（3.28.1），路径`skills/last30days`。已有有效安装直接复用，不强制降级或升级。用户以后要求升级时独立处理原版依赖，再做兼容性检查。

## 安装状态与研究状态分开

脚本输出JSON，说明依赖安装、当前Python运行时、选中的根目录及是否新安装。`research_readiness=not_checked`表示没有验证登录、平台连通性或真实研究；它不是失败，也不能被说成“全部来源就绪”。

- **已有可用文件与Python 3.12+**：安装检查完成。研究请求仍完整读取所选原版`SKILL.md`及其要求的runtime/setup/runbook，按上游执行来源准备。
- **缺失依赖**：在安装模式下自动调用官方helper，完成后重新核对文件。保持上游原样，不写配置或安装一个改版。
- **已有目录损坏**：不覆盖或删除；指出缺失文件，先找宿主已加载的其他有效副本或确认正确路径。需要替换已有内容时先检查本地更改。
- **Python不足**：先使用宿主提供的兼容解释器；再按原版`references/runtime.md`完成解释器选择和其支持的`uv`自动配置，使用选定解释器重新运行本脚本。若依赖还没装、该文件尚不存在，先通过宿主读取[固定版本的原版运行时说明](https://raw.githubusercontent.com/mvanhorn/last30days-skill/832d09591568e37dd990ccb9d5c51fb4a403f5ed/skills/last30days/references/runtime.md)，完成解释器准备后回到依赖安装。没有可用Python时由宿主执行这些步骤；不另造包管理器安装流程，不绕过环境权限。
- **helper缺失**：定位宿主的官方skill-installer，显式传入其helper；不能伪造一个同名helper或把旧路径写死。
- **网络或权限阻塞**：给出具体原因与已完成部分；不报告整体成功。修复后可重跑，已存在的依赖会复用。

安装本身不运行真实研究、创建个人档案或申请密钥。可选付费源、账户登录和浏览器凭据仍遵循用户已有授权与上游规则；它们不应变成每个用户必须完成的安装步骤。首次研究按上游允许的可用来源继续，并如实记录覆盖范围。

## 收尾与续用

向用户简短确认安装结果；按skill-installer约定说明技能下一轮可用。只有存在实际阻塞才展开排障细节。无需解释有几个依赖包或要求用户再执行一次安装。

手动复制本技能的用户在首次调用时走同一流程。后续咨询可复用本轮已确认的依赖位置，出现缺失、环境变化或升级后再检查；不为每条消息重复安装。
