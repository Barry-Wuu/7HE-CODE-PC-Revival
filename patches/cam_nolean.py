# -*- coding: utf-8 -*-
"""人类模式（终版）：把摄像机云台的「侧倾/前后倾」幅度归零。

背景：PlayerRelativeControl.Update 里
    zero.z = moveJoystick.position.y * 0.75f;      // 前进时镜头后仰
    zero.x = (0f - moveJoystick.position.x) * 0.5f; // 横移时镜头侧倾
    localPosition.x = Mathf.SmoothDamp(localPosition.x, zero.x, ref camVel.x, 0.3f);
    localPosition.z = Mathf.SmoothDamp(localPosition.z, zero.z, ref camVel.z, 0.5f);
因为 position 现在是 GetAxisRaw（瞬间 1→0），云台目标也会瞬间归零：
    · 平滑时间大(0.3/0.5) -> 镜头慢慢滑回 = 用户说的「惯性」
    · 平滑时间小(0.05)    -> 镜头猛地弹回 = 用户说的「跳变」
两头都不对，所以直接把幅度系数改成 0：云台恒在静止位，既不滑也不跳。
（同时把 SmoothDamp 的时间恢复成原版 0.3/0.5 —— 幅度为 0 后它已无作用，恢复只为减少改动面。）

定位全部靠「ldc.r4 <float> 紧跟特定 call/stfld 模式」，不碰其它常数：
    5a 7d 47 00 00 0a = mul; stfld Vector3::z   -> 0.75 那处
    5a 7d 46 00 00 0a = mul; stfld Vector3::x   -> 0.5  那处
    28 af 00 00 0a    = call Mathf::SmoothDamp  -> 恢复 0.3 / 0.5

用法: python cam_nolean.py          演练
      python cam_nolean.py --apply  实战（备份 *.bak_nolean）
"""
import sys, os, shutil, struct, importlib.util

TOOL = r"D:\B++\WorkBuddy\il_internalcall_stub.py"
spec = importlib.util.spec_from_file_location("ilstub", TOOL)
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

DLL = r'D:/7he Code/run32/7HECODE_Data/Managed/Assembly-UnityScript.dll'
P_STF_Z = bytes.fromhex('5a7d4700000a')   # mul ; stfld Vector3::z   （后仰幅度 0.75）
P_STF_X = bytes.fromhex('5a7d4600000a')   # mul ; stfld Vector3::x   （侧倾幅度 0.5）
P_SMOOTH = bytes.fromhex('28af00000a')    # call Mathf::SmoothDamp
RESTORE = [0.3, 0.5]                      # 按出现顺序还原平滑时间

data = bytearray(open(DLL, 'rb').read())
md = M.Metadata(bytes(data))
ti = M.find_type(md, 'PlayerRelativeControl')
mdef = None
for x in md.methods_of_type(ti)[0]:
    r = md.row(0x06, x)
    if md.str_at(r['Name']) == 'Update':
        mdef = r
        break
assert mdef is not None, '没找到 PlayerRelativeControl.Update'
o = md.pe.rva2off(mdef['RVA'])
u = data[o] | (data[o + 1] << 8)
hl = (u >> 12) * 4 if (u & 3) == 3 else 1
cs = struct.unpack_from('<I', data, o + 4)[0] if hl == 12 else (u >> 2)
base = o + hl
il = bytes(data[base:base + cs])
print('PlayerRelativeControl.Update IL=%d @file 0x%X' % (cs, base))

def scan(pat):
    out = []
    for k in range(len(il) - 10):
        if il[k] == 0x22 and il[k + 5:k + 5 + len(pat)] == pat:
            out.append((k, struct.unpack_from('<f', il, k + 1)[0]))
    return out

n = 0
# 只归零「走路侧倾」的两处：按数值精确匹配，避免误伤 zero.z = -jumpSpeed*0.25（空中云台下沉）
for pat, want, label in ((P_STF_Z, 0.75, '后仰幅度(走路)'), (P_STF_X, 0.5, '侧倾幅度(横移)')):
    for k, f in scan(pat):
        if abs(f - want) > 1e-6:
            print('  跳过 %s off=%4d  %.3f（非目标值 %.2f，保留）' % (label, k, f, want))
            continue
        print('  %s off=%4d  %.3f -> 0.000' % (label, k, f))
        struct.pack_into('<f', data, base + k + 1, 0.0); n += 1
for i, (k, f) in enumerate(scan(P_SMOOTH)):
    v = RESTORE[i] if i < len(RESTORE) else f
    print('  平滑时间 off=%4d  %.3f -> %.3f' % (k, f, v))
    struct.pack_into('<f', data, base + k + 1, v); n += 1
print('共改写 %d 处' % n)

chk = M.Metadata(bytes(data))
c = chk.d[base:base + cs]
def scan2(pat):
    return [round(struct.unpack_from('<f', c, k + 1)[0], 3)
            for k in range(len(c) - 10) if c[k] == 0x22 and c[k + 5:k + 5 + len(pat)] == pat]
print('回读: 后仰幅度=%s  侧倾幅度=%s  平滑时间=%s'
      % (scan2(P_STF_Z), scan2(P_STF_X), scan2(P_SMOOTH)))

if '--apply' not in sys.argv:
    print('[dry] 未写入'); sys.exit(0)
bak = DLL + '.bak_nolean'
if not os.path.exists(bak):
    shutil.copy2(DLL, bak); print('备份 ->', os.path.basename(bak))
else:
    print('备份已存在 ->', os.path.basename(bak))
open(DLL, 'wb').write(bytes(data))
print('写回', os.path.basename(DLL), len(data), 'B')
