# 《7HE CODE》Windows 复活版

<b>简体中文</b> ｜ <a href="README.en.md">English</a>
> **📦 完整可运行程序包 `7HE-CODE-PC-Revival.zip`（约 38.5 MB）已移入 [Releases](https://github.com/Barry-Wuu/7HE-CODE-Win11-Revival/releases)，不再存放在仓库根目录。需要 Windows 版请从发行版页面下载。**

把一款已下架的 iOS 游戏，重新在 Windows 上跑起来。

本仓库提供**完整可运行的 Windows 版本**，以及把它救活的全过程记录、所有修复脚本。

---

## 第一功：ipa 是怎么拿到的（ipatool）

这个项目能成立，前提是**拿到原始 ipa** —— 而《7HE CODE》早已下架，商店里搜不到、也买不到。

这一步用的是 [ipatool](https://github.com/majd/ipatool)（v2.6.0）：

1. `ipatool list-purchases` 把该 Apple ID 名下 **272 个历史已购应用**全量导出，再用 iTunes lookup API 做 CN / US / JP **三区在架核验** → 筛出 **77 个三大区全部下架**的应用。这些应用全球任何账号都无法再从商店获取，只剩「已购授权」这一条路。
2. `ipatool download -i <app-id> --purchase` 正是靠这份已购授权，在**已下架状态**下把 ipa 拉了下来。

《7HE CODE》就是验证这条通道可行的**第一个案例** —— 双版本 ipa（v1.0 65.6 MB / v2.0 51.9 MB）都由此而来。没有它就没有源文件，后面的引擎壳缝合、贴图转换、光照修复全部无从谈起。

> 复刻要点：`ipatool auth login -e <你的 Apple ID>`（密码为隐藏输入，2FA 验证码发到邮箱）；对已下架应用 `ipatool search` 会失败，属正常现象，**直接用数字 app-id 下载**即可；若返回 not owned，说明该账号从未购买过，这条路对你关闭。

---

## 这是什么

《7HE CODE》是一款 Unity 3.4.2 引擎、Mono 血统的 iOS 游戏。原版早已从 App Store 下架，官方渠道无法获得，属于典型的失传媒体（lost media）。

复活方式不是重写工程，而是**引擎壳缝合**：用同版本 Unity 自带的 Windows 独立播放器（windowsstandaloneplayer）当外壳，把原版 iOS 的 `Data/` 数据文件直接套进去，再逐个修掉平台差异带来的报错与渲染问题。最终产物就是一个双击即玩的 Windows 程序。

| 项目 | 说明 |
| --- | --- |
| 引擎 | Unity 3.4.2f3（Mono / .NET 2.0 血统） |
| 原平台 | iOS（原数据 `target_platform = 9`） |
| 现平台 | Windows standalone（`target_platform = 5`） |
| 运行架构 | 32 位 exe，32/64 位 Windows 均可运行 |
| 内部渲染分辨率 | 1440 × 960 虚拟分辨率 |

![修复前后对比](assets/before_after.png)

<sub>同一房间的三种状态：原始 iOS 画面 / 缝合移植后的过曝 / 修复后</sub>

---

## 三步跑起来

### 1. 拿到仓库

```bash
git clone https://github.com/Barry-Wuu/7HE-CODE-Win11-Revival.git
```

或者直接在网页上点右上角 **Code → Download ZIP**。

### 2. 解压游戏包

仓库里的 `7HE-CODE-PC-Revival.zip` 就是完整的游戏本体（约 27 MB，解压后约 190 MB）。

用 Windows 自带的解压（右键 → 全部解压缩）即可，WinRAR / 7-Zip 也可以。

> 注意：`resources.assets` 原始体积 115 MB，超过 GitHub 单文件 100 MB 的上限，所以游戏以压缩包形式提供。

### 3. 双击运行

进入解压出来的 `7HE-CODE-PC-Revival` 目录，双击 **`Play 7HE CODE.bat`**。

首次启动会黑屏几秒（引擎初始化 + 载入资源），属于正常现象。

---

## 操作方式

这个版本把原本的虚拟摇杆做成了键鼠两套并行输入，两套互不干扰，可以同时用。

| 动作 | 操作 |
| --- | --- |
| 前后左右移动 | `W` `A` `S` `D` 或 `↑` `↓` `←` `→` |
| 转动视角 | 按住**鼠标右键**拖动 |
| 拖左侧虚拟摇杆 | 按住**鼠标左键**拖动左摇杆 |
| 拖右侧虚拟摇杆 | 按住**鼠标左键**拖动右摇杆 |
| 边走边转视角 | 左手按 `WASD`，右手按住右键拖动 —— 可以同时进行 |
| 点击场景中的交互点 | 鼠标左键点击（需先站到对应位置） |

移动是**松键即停**的，没有滑行惯性。

---

## 系统要求

- Windows 7 SP1 / 8 / 10 / 11（32 位与 64 位都可以）
- DirectX 9.0c 运行库（Win 10 / 11 已内置兼容层，老系统可能需要单独安装）
- 显卡支持 DXT1 / DXT5 纹理压缩 —— 2005 年之后的主流独显与核显都支持
- 磁盘约 400 MB 空闲（解压后约 190 MB）

---

## 这个版本修了什么

从「数据套进去能启动」到「看起来像一款正常的 PC 游戏」，一共过了六道关。

| # | 问题 | 症状 | 修复 |
| --- | --- | --- | --- |
| 1 | 平台签证 | 提示资源是给另一个构建目标打的，直接拒绝加载 | 12 / 13 个数据文件的 `target_platform` 由 9（iOS）改为 5（Windows 独立播放器） |
| 2 | 内置资源缺失 | 刷屏报 `UnitySplash.png` / `Internal-GUITexture.shader` 找不到 | `Resources\` 与 `library\` 两处都补上 Unity 默认资源 |
| 3 | 引擎 API 缺失 | `MissingMethodException: iPhoneSettings.get_generation` | 换回 iOS 版 `UnityEngine.dll`，并为其中 45 个 InternalCall 打桩 |
| 4 | 触摸输入失效 | 菜单能画出来，按钮点不动 | 给 `Input.get_touchCount` / `Touch.get_phase` / `Touch.get_position` 三根桩补全语义，把鼠标当手指 |
| 5 | 贴图雪花 | 画面全是彩色噪点 | 237 张 PVRTC 贴图全部转为桌面 DXT1 / DXT5 |
| 6 | 全场景过曝 | 画面亮到发白、发青，62% 像素被打爆 | 光照贴图改按 RGBM 编码（见下） |
| 7 | 操控不可用 | 虚拟摇杆拖不动；键盘鼠标无从下手 | IL 级注入键鼠操控；左右摇杆按根判定互不干扰 |
| 8 | 停步跳变 | 松手停下时画面会往某个方向滑 / 跳一小段 | 第一人称云台的侧倾幅度归零 |

### 第 6 条值得单独说

Unity 内置着色器对光照贴图的解码是**分平台的**（源码见 `CGIncludes/UnityCG.cginc` 的 `DecodeLightmap`）：

```hlsl
inline fixed3 DecodeLightmap( fixed4 color )
{
#if defined(SHADER_API_GLES) && defined(SHADER_API_MOBILE)
    return 2.0 * color.rgb;              // iOS：Double-LDR，×2
#else
    return (8.0 * color.a) * color.rgb;  // 桌面 D3D9：RGBM，alpha 当倍率，×8
#endif
}
```

iOS 的光照贴图是无 alpha 的 RGB24 / PVRTC_RGB4，数据按 ×2 存；搬到桌面后 alpha 被读成 1.0，于是按 **×8** 解码 —— 全场景比原版亮了 4 倍。

修法是把 30 张光照贴图统一改成 RGBA32，RGB 保持原值不动，只把 alpha 写成补偿倍率，让桌面的 `8 × alpha` 精确还原 iOS 的亮度，同时不损失 RGB 精度。本版本采用 alpha = 92（倍率 2.886），是按实机截图与原始画面逐特征比对后定的值。

---

## 目录结构

```
7HE-CODE-PC-Revival/
├── README.md                     本文件（简体中文）
├── README.en.md                  English version
├── 7HE-CODE-PC-Revival.zip       完整可运行游戏（解压即玩）
├── assets/                       README 用图
├── docs/
│   ├── 复活战役进度.md             攻坚过程战报（逐关破法）
│   ├── 修复全记录.md               技术复盘（与 B 站专栏同源）
│   └── 挖掘报告.md                 早期资料考古（archive.org 等四路检索）
└── patches/
    ├── README.md                 脚本用途与使用顺序
    ├── il_internalcall_stub.py   .NET 元数据 / IL 注入工具
    ├── unity3x_touch_mouse_shim.py  触摸 API 三根桩
    ├── pc_pad_b.py               键鼠操控注入（含双通道解耦）
    ├── cam_nolean.py             第一人称云台侧倾归零
    └── lm_fix_global.py          光照贴图 RGBM 批量修复
```

---

## 常见问题

**启动后黑屏、几秒后自己关了？**
先在游戏目录下看 `output_log.txt`。最常见的两类原因：显卡驱动过旧（不支持 DXT 格式），或被杀毒软件拦截了老版本的 Unity 播放器（它是 2013 年的程序，容易被误报）。

**贴图全是彩色雪花？**
说明贴图转换没生效或文件被覆盖。请用压缩包里的原始文件，不要自行替换 `7HECODE_Data` 下的 `.assets`。

**画面一片惨白或发青？**
光照贴图解码问题。本仓库的版本已经修好，若你看到过曝，多半是用了没有打光照补丁的旧数据。

**虚拟摇杆拖不动 / 键盘没反应？**
确认你运行的是本仓库压缩包里的版本。原版数据直接套壳时，摇杆完全没有响应。

**想调鼠标灵敏度？**
改 `patches/pc_pad_b.py` 顶部的 `GAIN` 常量（默认 2.0），再重新注入。

**游戏里的按钮/菜单位置偏了？**
游戏内部按 1440 × 960 的虚拟分辨率布局，全屏或超大窗口下 GUI 会按比例拉伸，属正常现象。

---

## 想自己复刻一遍

`patches/` 下的脚本是可以复用的，但**必须按顺序执行**（后一个脚本在前一个的产物上继续改）：

```bash
python il_internalcall_stub.py         # 工具库
python unity3x_touch_mouse_shim.py     # 修触摸三根桩
python pc_pad_b.py --apply             # 注入键鼠操控（会从原始 DLL 整体重建）
python cam_nolean.py --apply           # 云台侧倾归零（必须在上一步之后）
python lm_fix_global.py --alpha 0.36 --apply   # 光照贴图 RGBM 修复
```

注意两点：

- `pc_pad_b.py` 是从**未经修改的原始 DLL** 整体重建的（PE 节表扩容不能叠加），所以它会覆盖掉之前对同一个 DLL 的其他改动，必须在它之后补跑 `cam_nolean.py`。
- 脚本里的路径是原作者机器上的绝对路径，复刻时请改成你自己的。

验证方式（离线、不用启动游戏）：用同版本的 Mono 运行时加载改过的程序集，强制 JIT 一遍即可查出 IL 是否合法。见 `docs/复活战役进度.md`。

---

## 来源与声明

- 原始数据来自原版 iOS 应用的 `Payload/7HE CODE.app/Data/`，本仓库**不提供 ipa 原始文件**。
- 本仓库的存在目的是**失传媒体保存与研究**：该游戏官方渠道已全部下架，商店页面与官方界面均已失效。
- 游戏本体的著作权归原作者与权利方所有。本仓库不对游戏内容主张任何权利，仅供学习、研究与个人保存使用，**禁止任何商业用途**。
- 若权利方认为本仓库不妥，请联系删除，会立即配合处理。

本仓库同时托管在两个地址，内容完全一致，哪个打得开就用哪个：

- <https://github.com/Barry-Wuu/7HE-CODE-Win11-Revival> （GitHub）
- <https://gitcode.com/Barry_Wu_/7HE-CODE-Win11-Revival> （GitCode，国内直连，GitHub 打不开时用它）

## 用到的工具

[UnityPy](https://github.com/K0lb3/UnityPy)（含 Unity 3.4 / 格式 v8 兼容补丁）、[texture2ddecoder](https://github.com/K0lb3/texture2ddecoder)、[AssetRipper](https://github.com/AssetRipper/AssetRipper)、Unity 3.4.2 自带的 Mono 2.0 运行时与 `gmcs`。

---

<sub>本仓库的修复记录同时以 B 站专栏形式发布，专栏见仓库主页链接。</sub>
