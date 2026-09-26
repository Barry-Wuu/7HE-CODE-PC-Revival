#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pc_pad_b.py — 给 7HE CODE 加「键盘移动 + 右键鼠标视角」（PC 端操控补丁 B）

钩子选在 Joystick.Update() 的最末尾（每帧必到、且改在 position 重算之后），
直接把键盘/鼠标轴写进 Joystick.position —— PlayerRelativeControl 下一帧读它：

    左摇杆 moveJoystick   <- WASD / 方向键            GetAxisRaw("Horizontal"/"Vertical")
                            （GetAxisRaw = 松键瞬停；改 GetAxis 则恢复 gravity=3 的滑行惯性）
    右摇杆 rotateJoystick <- 按住鼠标右键 + 移动鼠标   GetAxis("Mouse X"/"Mouse Y") * GAIN

  · 按住鼠标左键时整段跳过：让补丁 A（UnityEngine.dll 把左键当触摸）继续负责
    「鼠标拖拽虚拟摇杆」，两套操控互不打架。
  · 不按任何键 -> GetAxis 返回 0 -> 摇杆回中，与原始逻辑一致。

左右摇杆的区分（由场景实测得到，level2/3/4 一致）：
    Joystick.Start():  defaultRect.x = gui.pixelInset.x + transform.position.x * Screen.width
        左摇杆 -50 + 0.15*1440 = 166.0
        右摇杆 -50 + 0.85*1440 = 1174.0
    所以 defaultRect.x < Screen.width*0.5  ==  左摇杆。

空间问题与解法
    .text 内部没有任何可用零填充（尾部那 368B 落在节虚拟尺寸之外，Mono 不映射，
    写进去等于没写）。做法：
      1) 把 .text 的 VirtualSize / SizeOfRawData 从 0x1A090 抬到 0x1C000
         （虚拟末尾 0x1E000 正好顶到 .reloc，不重叠；SizeOfImage 0x20000 不变）
      2) 把 .reloc 的 raw 数据从 0x1A400 搬到 0x1C200
      => file [0x1A290, 0x1C200) = 8047B  ↔  VA [0x1C090, 0x1E000) 合法可执行空间
      3) 把 Joystick.Update 的整个方法体（12B fat 头 + 1317B IL + 注入 ~149B）
         搬过去，改写 MethodDef 的 RVA 指过去。
    全程不改动任何原有字节、不新增元数据行、不做跨方法跳转。

用法：
    python pc_pad_b.py            # 演练
    python pc_pad_b.py --apply    # 实战（备份 *.bak_pcpad）
"""
import importlib.util
import os
import shutil
import struct
import sys

TOOL = r"D:\B++\WorkBuddy\il_internalcall_stub.py"
spec = importlib.util.spec_from_file_location("ilstub", TOOL)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

DLL = r'D:/7he Code/run32/7HECODE_Data/Managed/Assembly-UnityScript.dll'
# 未打补丁的原始 dll。补丁会改写 PE 节表（.text 扩容 + .reloc 搬迁），不可重复叠加，
# 所以每次都从原始文件重建，再整体写到 DLL。
SRC = DLL + '.bak_pcpad'
GAIN = 2.0      # 鼠标视角灵敏度：position = GetAxis("Mouse X") * GAIN
                #   原版 rotationSpeed=(50,25)，position=1 即 50°/s；GAIN=2 时
                #   ≈300px/s 的鼠标速度就顶到原版最大转速。嫌慢就调大这个数。
RAW_AXES = True  # 移动轴用 GetAxisRaw（无 gravity 衰减）= 松键瞬停、「人类模式」；
                 # False 用 GetAxis（gravity=3，约 0.3s 归零）= 原「滚球滑行」手感。
                 # 鼠标视角轴始终用 GetAxis（鼠标位移型轴无衰减，Raw 会丢掉 sensitivity）。


# ------------------------------------------------------------ 小汇编器
class Asm:
    def __init__(self):
        self.items = []

    def b(self, *bs):
        self.items.append(('b', bytes(bs))); return self

    def ldc_i4(self, v):
        if v == -1: return self.b(0x15)
        if 0 <= v <= 8: return self.b(0x16 + v)
        return self.b(0x20, *struct.pack('<i', v))

    def ldc_r4(self, f): return self.b(0x22, *struct.pack('<f', f))
    def call(self, t):   return self.b(0x28, *struct.pack('<I', t))
    def ldstr(self, t):  return self.b(0x72, *struct.pack('<I', t))
    def ldfld(self, t):  return self.b(0x7B, *struct.pack('<I', t))
    def ldflda(self, t): return self.b(0x7C, *struct.pack('<I', t))
    def stfld(self, t):  return self.b(0x7D, *struct.pack('<I', t))
    def ldarg0(self):    return self.b(0x02)
    def conv_r4(self):   return self.b(0x6B)
    def mul(self):       return self.b(0x5A)
    def clt(self):       return self.b(0xFE, 0x04)
    def pop(self):       return self.b(0x26)
    def ret(self):       return self.b(0x2A)

    def br(self, op, label, long=False):
        """op 用短形式码（0x2B/0x2C/0x2D）；long=True 时自动换成 0x38/0x39/0x3A 长形式"""
        if long:
            op = {0x2B: 0x38, 0x2C: 0x39, 0x2D: 0x3A}[op]
        self.items.append(('br', (op, label))); return self

    def label(self, n):
        self.items.append(('lbl', n)); return self

    def _sz(self, op):
        return 5 if op in (0x38, 0x39, 0x3A) else 2

    def assemble(self):
        pos, off = {}, 0
        for kind, v in self.items:
            if kind == 'lbl':
                pos.setdefault(v, off); continue
            off += self._sz(v[0]) if kind == 'br' else len(v)
        out, cur = bytearray(), 0
        for kind, v in self.items:
            if kind == 'lbl':
                continue
            if kind == 'br':
                op, lab = v
                base = cur + self._sz(op)
                d = pos[lab] - base
                if op in (0x38, 0x39, 0x3A):
                    out += bytes([op]) + struct.pack('<i', d); cur += 5
                else:
                    if not (-128 <= d <= 127):
                        raise ValueError('短分支超距 %s=%d（请用 long=True）' % (lab, d))
                    out += bytes([op, d & 0xFF]); cur += 2
            else:
                out += v; cur += len(v)
        return bytes(out)


# ------------------------------------------------------------ 元数据工具
def table_off(md, table):
    cur = md.table_start
    for t in range(64):
        n = md.rows.get(t, 0)
        if n == 0:
            continue
        if t == table:
            return cur
        cur += _rs(md, t) * n
    return None


def _rs(md, t):
    if t == 0x09:          # InterfaceImpl 行宽实为 2+2（工具 TABLES 写成 6，会错位）
        return 4
    return md._row_size(t)


def read_mref(md, i):
    p = table_off(md, 0x0A) + i * _rs(md, 0x0A)
    return (int.from_bytes(md.d[p:p + 2], 'little'),
            int.from_bytes(md.d[p + 2:p + 4], 'little'),
            int.from_bytes(md.d[p + 4:p + 6], 'little'))


def typeref_name(md, row1):
    p = table_off(md, 0x01) + (row1 - 1) * _rs(md, 0x01)
    nidx = int.from_bytes(md.d[p + 2:p + 4], 'little')
    nsidx = int.from_bytes(md.d[p + 4:p + 6], 'little')
    ns, nm = md.str_at(nsidx), md.str_at(nidx)
    return (ns + '.' + nm) if ns else nm


def find_mref(md, parent, name):
    for i in range(md.rows.get(0x0A, 0)):
        cls, nidx, _s = read_mref(md, i)
        if (cls & 7) != 1:
            continue
        if md.str_at(nidx) != name:
            continue
        if typeref_name(md, cls >> 3) == parent:
            return 0x0A000000 | (i + 1)
    return None


def us_find(md, s):
    """在 #US 堆里定位字符串，返回相对堆起始的偏移（token = 0x70000000 | off）。
       坑：M.read_compressed(b,p) 返回 (值, 下一个字节的绝对位置)，不是 (值, 前缀字节数)。"""
    base, size = md.streams['#US']
    end = base + size
    p = base + 1                       # 堆首是 0 哨兵字节
    while p < end:
        val, nxt = M.read_compressed(md.d, p)
        n = nxt - p
        if val <= 0 or n < 1 or n > 4 or nxt + val > end:
            break
        try:
            if md.d[nxt:nxt + val - 1].decode('utf-16-le') == s:
                return p - base
        except Exception:
            pass
        p = nxt + val
    return None


# ------------------------------------------------------------ PE 空间扩展
FREE_VA = None
FREE_FILE = None


def section_hdr_off(md, idx):
    e = struct.unpack_from('<I', md.d, 0x3C)[0]
    coff = e + 4
    opt_size = struct.unpack_from('<H', md.d, coff + 16)[0]
    return coff + 20 + opt_size + 40 * idx


def extend_text(data, md):
    """把 .text 撑到最大合法虚拟尺寸，并把 .reloc 的 raw 挪到后面腾地方。
       返回 (新字节流, 空区起始 VA, 空区起始 file offset, 可用字节数)。"""
    pe = md.pe
    txt = pe.secs[0]
    assert txt['name'] == '.text', txt['name']
    rel = pe.secs[1]
    assert rel['name'] == '.reloc', rel['name']

    new_vsize = rel['va'] - txt['va']              # 虚拟末尾刚好顶到 .reloc
    old_end_file = txt['roff'] + txt['rsize']      # 老 raw 末尾（.reloc 的 raw 起点）
    new_rel_roff = txt['roff'] + new_vsize         # .reloc raw 新位置
    assert new_rel_roff % 0x200 == 0

    # 搬迁：保留原 raw 尾部 slack -> 补零 -> 把 .reloc raw 放最后
    rel_raw = data[rel['roff']:rel['roff'] + rel['rsize']]
    new = bytearray(data[:old_end_file])
    new += b'\x00' * (new_rel_roff - old_end_file)
    new += rel_raw

    # 改节头
    th = section_hdr_off(md, 0)
    struct.pack_into('<I', new, th + 8, new_vsize)
    struct.pack_into('<I', new, th + 16, new_vsize)
    rh = section_hdr_off(md, 1)
    struct.pack_into('<I', new, rh + 20, new_rel_roff)

    free_file = txt['roff'] + txt['vsize']         # 老虚拟末尾 = 空区起点
    free_va = txt['va'] + txt['vsize']
    avail = new_rel_roff - free_file
    print('  .text vsize 0x%X -> 0x%X (rsize 同), .reloc roff 0x%X -> 0x%X'
          % (txt['vsize'], new_vsize, rel['roff'], new_rel_roff))
    print('  新空区 VA 0x%06X (file 0x%06X) 可用 %d B' % (free_va, free_file, avail))
    return bytes(new), free_va, free_file, avail


# ------------------------------------------------------------ 主体
def build_injection(md, MR, US, F_DR, TOK_FD):
    """TOK_FD = Joystick.IsFingerDown() 的 MethodDef 令牌。
       闸门按「根」判定：某根摇杆正被鼠标拖着（IsFingerDown 为真）就让给拖拽逻辑，
       另一根照常由键盘/鼠标驱动 —— 于是 WASD 与「拖右摇杆看视角」可以同时进行。"""
    a = Asm()
    # isLeft = defaultRect.x < Screen.width * 0.5f
    a.ldarg0().ldflda(F_DR).call(MR['get_x'])          # [dr.x]
    a.call(MR['Screen_width']).conv_r4().ldc_r4(0.5).mul()
    a.clt()                                            # [dr.x < w*0.5f]
    a.br(0x2C, 'RIGHT')                     # brfalse.s RIGHT
    # ---- 左摇杆：没被鼠标拖时才用键盘
    a.ldarg0().call(TOK_FD)
    a.br(0x2D, 'SKIP', long=True)           # brtrue SKIP（跨距约 126B，用长形式）
    mv = MR['GetAxisRaw'] if RAW_AXES else MR['GetAxis']
    for axis, comp in ((US['Horizontal'], 'x'), (US['Vertical'], 'y')):
        a.ldarg0().ldflda(F_POS).ldstr(axis).call(mv).stfld(MR[comp])
    a.br(0x2B, 'SKIP')                      # br.s SKIP
    # ---- 右摇杆：没被鼠标拖时才用右键+鼠标
    a.label('RIGHT')
    a.ldarg0().call(TOK_FD)
    a.br(0x2D, 'SKIP')                      # brtrue.s SKIP
    for axis, comp in ((US['Mouse X'], 'x'), (US['Mouse Y'], 'y')):
        a.ldarg0().ldflda(F_POS).ldstr(axis).call(MR['GetAxis'])
        a.ldc_i4(1).call(MR['GetMouseButton']).conv_r4().ldc_r4(GAIN).mul()
        a.mul().stfld(MR[comp])
    a.label('SKIP')
    return a.assemble()


F_POS = None


def build():
    print('  源（未打补丁）: %s' % SRC)
    if not os.path.exists(SRC):
        raise SystemExit('!! 找不到原始 dll：%s' % SRC)
    data = open(SRC, 'rb').read()
    md = M.Metadata(data)
    ti = M.find_type(md, 'Joystick')

    # --- FieldDef ---
    r = md.row(0x02, ti - 1)
    start = r['FieldList'] - 1
    end = md.rows.get(0x04, 0)
    if ti < md.rows.get(0x02, 0):
        nr = md.row(0x02, ti)
        if nr['FieldList']:
            end = nr['FieldList'] - 1
    FT = {}
    for fi in range(start, end):
        FT[md.str_at(md.row(0x04, fi)['Name'])] = 0x04000000 | (fi + 1)

    # --- MemberRef ---
    MR, want = {}, [('UnityEngine.Input', 'GetAxis'), ('UnityEngine.Input', 'GetAxisRaw'),
                    ('UnityEngine.Input', 'GetMouseButton'),
                    ('UnityEngine.Screen', 'get_width'), ('UnityEngine.Rect', 'get_x'),
                    ('UnityEngine.Vector2', 'x'), ('UnityEngine.Vector2', 'y')]
    for parent, name in want:
        t = find_mref(md, parent, name)
        if t is None:
            raise SystemExit('!! 缺 MemberRef %s::%s' % (parent, name))
        MR[name] = t
    MR['Screen_width'] = MR.pop('get_width')

    US = {}
    for s in ('Horizontal', 'Vertical', 'Mouse X', 'Mouse Y'):
        o = us_find(md, s)
        if o is None:
            raise SystemExit('!! #US 里没有 %r' % s)
        US[s] = 0x70000000 | o

    global F_POS
    F_POS = FT['position']
    # --- MethodDef 令牌：本类内部的 IsFingerDown()，用来判断某根摇杆是否正被鼠标拖
    TOK_FD = None
    for x in md.methods_of_type(ti)[0]:
        if md.str_at(md.row(0x06, x)['Name']) == 'IsFingerDown':
            TOK_FD = 0x06000000 | (x + 1)
            break
    if TOK_FD is None:
        raise SystemExit('!! 没找到 Joystick.IsFingerDown')
    print('  MethodDef IsFingerDown = 0x%08X' % TOK_FD)
    print('  字段 position=0x%08X defaultRect=0x%08X' % (F_POS, FT['defaultRect']))
    for s in ('Horizontal', 'Vertical', 'Mouse X', 'Mouse Y'):
        print('  #US %-10s tok=0x%08X' % (s, US[s]))
    for k in ('GetAxis', 'GetMouseButton', 'Screen_width', 'get_x', 'x', 'y'):
        print('  MemberRef %-16s 0x%08X' % (k, MR[k]))

    inj = build_injection(md, MR, US, FT['defaultRect'], TOK_FD)
    print('\n  注入 IL %d B:\n    %s' % (len(inj), inj.hex(' ')))

    # --- 找 Update ---
    ms, _ = md.methods_of_type(ti)
    mi = None
    for x in ms:
        if md.str_at(md.row(0x06, x)['Name']) == 'Update':
            mi = x; break
    if mi is None:
        raise SystemExit('!! 没找到 Joystick.Update')
    row = md.row(0x06, mi)
    off = md.pe.rva2off(row['RVA'])
    u16 = data[off] | (data[off + 1] << 8)
    assert (u16 & 3) == 3, '不是 fat 头'
    maxstack = struct.unpack_from('<H', data, off + 2)[0]
    codesize = struct.unpack_from('<I', data, off + 4)[0]
    lv = struct.unpack_from('<I', data, off + 8)[0]
    il = data[off + 12:off + 12 + codesize]
    assert il[-1] == 0x2A, 'Update 末尾不是 ret'
    new_il = il[:-1] + inj + b'\x2A'
    body = struct.pack('<HHII', (u16 & 0x0FFF) | 0x3000, maxstack, len(new_il), lv) + new_il
    print('  Update 原体 %dB (IL %d) -> 新体 %dB (IL %d, maxstack %d)'
          % (12 + codesize, codesize, len(body), len(new_il), maxstack))
    return data, md, row, body, inj


def install(apply=False):
    data, md, row, body, inj = build()
    blob = body + b'\x00' * ((-len(body)) % 4)
    new, free_va, free_file, avail = extend_text(data, md)
    print('  新体需 %d B / 可用 %d B -> %s' % (len(blob), avail, 'OK' if len(blob) <= avail else '不够!'))
    if len(blob) > avail:
        raise SystemExit('!! 空间不足，未写入')
    new = bytearray(new)
    new[free_file:free_file + len(blob)] = blob

    # 改 Update 的 RVA（元数据区没动过，直接用原 data 里记录的字段偏移）
    rva_off = row['_off_RVA']
    old_rva = struct.unpack_from('<I', data, rva_off)[0]
    struct.pack_into('<I', new, rva_off, free_va)
    print('  Update RVA 0x%06X -> 0x%06X' % (old_rva, free_va))

    # ---------------- 自检：重新按 PE+元数据解析新字节流
    chk = M.Metadata(bytes(new))
    t_s = chk.pe.secs[0]
    r_s = chk.pe.secs[1]
    assert t_s['name'] == '.text' and r_s['name'] == '.reloc'
    assert chk.pe.rva2off(free_va) == free_file, '空区 RVA<->文件偏移不符'
    ti = M.find_type(chk, 'Joystick')
    ms, _ = chk.methods_of_type(ti)
    ok = False
    for x in ms:
        if chk.str_at(chk.row(0x06, x)['Name']) != 'Update':
            continue
        r = chk.row(0x06, x)
        o = chk.pe.rva2off(r['RVA'])
        u = chk.d[o] | (chk.d[o + 1] << 8)
        cs = struct.unpack_from('<I', chk.d, o + 4)[0]
        mv = struct.unpack_from('<H', chk.d, o + 2)[0]
        body_il = chk.d[o + 12:o + 12 + cs]
        assert r['RVA'] == free_va
        assert body_il[-1] == 0x2A and body_il[-1 - len(inj):-1] == inj
        print('  自检: Update RVA=0x%06X fat头=0x%04X maxstack=%d codesize=%d 末字节=0x%02X'
              % (r['RVA'], u, mv, cs, body_il[-1]))
        print('  自检: .text vsize=0x%X rsize=0x%X | .reloc VA=0x%X roff=0x%X | SizeOfImage=0x%X'
              % (t_s['vsize'], t_s['rsize'], r_s['va'], r_s['roff'],
                 struct.unpack_from('<I', chk.d, struct.unpack_from('<I', chk.d, 0x3C)[0] + 24 + 56)[0]))
        ok = True
    assert ok, '自检没能读回 Update'

    if not apply:
        print('  [dry] 未写入')
        return True
    bak = DLL + '.bak_pcpad'
    if not os.path.exists(bak):
        shutil.copy2(DLL, bak); print('  备份 ->', os.path.basename(bak))
    else:
        print('  备份已存在（保留首版）->', os.path.basename(bak))
    open(DLL, 'wb').write(bytes(new))
    print('  写回', os.path.basename(DLL), '%d -> %d B' % (len(data), len(new)))
    return True


if __name__ == '__main__':
    print('=== PC 键盘/鼠标视角补丁 %s ===' % ('APPLY' if '--apply' in sys.argv else 'dry-run'))
    install(apply='--apply' in sys.argv)
