# patches —— 修复脚本

这些脚本用于把原版 iOS 数据修成可运行的 Windows 版本。**必须按顺序执行** —— 后一步建立在前一步的产物之上。

| 顺序 | 脚本 | 作用 | 改动的文件 |
| --- | --- | --- | --- |
| 0 | `il_internalcall_stub.py` | 工具库：.NET 元数据解析 + IL 注入（PE 节扩容、code cave、微型汇编器） | 被其他脚本 import，不单独运行 |
| 1 | `unity3x_touch_mouse_shim.py` | 给 `Input.get_touchCount`、`Touch.get_phase`、`Touch.get_position` 三根 InternalCall 补全语义，把鼠标当手指 | `UnityEngine.dll` |
| 2 | `pc_pad_b.py --apply` | 改写 `Joystick.Update`，注入键盘移动（`GetAxisRaw`）+ 鼠标视角 + 左右摇杆按根判定 | `Assembly-UnityScript.dll` |
| 3 | `cam_nolean.py --apply` | 把 `PlayerRelativeControl.Update` 里第一人称云台的侧倾 / 后仰幅度常数归零 | `Assembly-UnityScript.dll` |
| 4 | `lm_fix_global.py --alpha 0.36 --apply` | 30 张光照贴图统一改 RGBA32 并写 alpha 补偿，还原 iOS 端亮度 | `sharedassets3.assets`、`sharedassets4.assets` |

## 顺序陷阱

`pc_pad_b.py` 是从**未经修改的原始 DLL** 整体重建的（PE 节表的扩容不可重复叠加），因此它会覆盖掉施加在同一个 DLL 上的其他改动。

固定写法：

```bash
python pc_pad_b.py --apply      # 先
python cam_nolean.py --apply    # 后（补跑）
```

改完用 Mono 探针统一回读，确认两处补丁同时在线。

## 路径

脚本内的路径（`D:/7he Code/...`、`D:/B++/...`）是原作者机器上的绝对路径，复刻时请替换成你自己的。

## 通用注入手法

1. 先把目标方法体整体搬到 `.text` 节尾部新腾出的合法空间：抬高该节 VirtualSize（与下一节不重叠），再把 `.reloc` 段挪到文件末尾腾地方。
2. 在原 IL 的末尾、`ret` 之前追加注入代码。
3. 只改该 MethodDef 的 RVA —— 不动任何原有字节、不新增元数据行、不做跨方法跳转，栈平衡与分支距离就不会出问题。

## 离线验证

用同代 Mono 运行时（Unity 3.4.2 自带，`Editor/Data/Mono/bin/mono.exe`）加载改过的程序集并强制 JIT 一遍，即可在**不启动游戏**的前提下验证 IL 是否合法：

- 能加载 ⇒ PE 头、节表、元数据表、CLI 头全部合法
- 能 JIT ⇒ 分支目标、栈平衡、所有令牌与方法签名全部合法
