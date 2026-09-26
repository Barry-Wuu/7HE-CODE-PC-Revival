#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unity3x_touch_mouse_shim.py — 让老 Unity 桌面播放器「把鼠标当成一根手指」

背景：
  iOS 时代的 Unity 游戏（2012 前后）几乎清一色用触摸 API 驱动按钮：
      if (Input.touchCount == 1 && Input.GetTouch(0).phase == TouchPhase.Began
          && guiTexture.HitTest(Input.GetTouch(0).position)) { ... }
  而桌面播放器的 Input.touchCount 恒为 0，条件永远短路 ——
  于是「菜单画得出来、按钮点不动」。

解法（不需要构造 Touch 结构体，只打三个小桩）：
  1. Input.get_touchCount  ->  Input.GetMouseButtonDown(0)   （按下瞬间 = 恰好 1 根手指）
  2. Touch.get_phase       ->  ldc.i4.0                      （TouchPhase.Began == 0）
  3. Touch.get_position    ->  Input.mousePosition           （同为左下原点，坐标系天然对齐）
  这样每一帧鼠标左键按下 = 一次「单指刚触及」，HitTest 也拿到正确屏幕坐标。

可行性前提（已实证）：
  - Touch.get_position / get_phase 是普通托管属性（RVA!=0，有真 IL 体）→ 可直接改 RVA
  - Input.get_touchCount 是 InternalCall（RVA=0）→ 补一个托管体并清掉 InternalCall 标志
  - GetMouseButtonDown / get_mousePosition 是同程序集内的 InternalCall，桌面原生实现良好

用法：
  python unity3x_touch_mouse_shim.py <UnityEngine.dll>            # 演练
  python unity3x_touch_mouse_shim.py <UnityEngine.dll> --apply    # 实改（自动备份到同目录 *.bak_shim）
"""
import importlib.util
import os
import shutil
import struct
import sys

TOOL = r"D:\B++\WorkBuddy\il_internalcall_stub.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("ilstub", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = load_tool()


# ------------------------------------------------------------------ 元数据辅助

def sig_ret(md, blob):
    """解出返回类型 (元素码, TypeDefOrRef 令牌 or None, 形参个数)"""
    if not blob:
        return None, None, 0
    cc = blob[0]
    p = 1
    if cc & 0x10:
        _g, p = M.read_compressed(blob, p)
    pc, p = M.read_compressed(blob, p)
    if p >= len(blob):
        return None, None, pc
    e = blob[p]
    p += 1
    tok = None
    if e in (0x11, 0x12):                      # VALUETYPE / CLASS
        tok, p = M.read_compressed(blob, p)
    return e, tok, pc


def tok_type_name(md, tok):
    if tok is None:
        return None
    t = md.type_of_token(tok)
    if not t:
        return None
    table, row = t
    if table != 0x02 or not row:
        return None
    return md.type_name(row)


def sig_param_types(md, blob):
    """返回形参类型名列表（仅处理 VALUETYPE/CLASS，其余给元素码）"""
    if not blob:
        return []
    cc = blob[0]
    p = 1
    if cc & 0x10:
        _g, p = M.read_compressed(blob, p)
    pc, p = M.read_compressed(blob, p)
    if p >= len(blob):
        return []
    e = blob[p]
    p += 1
    if e in (0x11, 0x12):
        _t, p = M.read_compressed(blob, p)
    elif e == 0x1D:                            # SZARRAY
        _e2, p = M.read_compressed(blob, p)
    out = []
    for _ in range(pc):
        if p >= len(blob):
            break
        e = blob[p]
        p += 1
        if e in (0x11, 0x12):
            t, p = M.read_compressed(blob, p)
            out.append(tok_type_name(md, t) or ('elem%02X' % e))
        elif e == 0x1D:
            _e2, p = M.read_compressed(blob, p)
            out.append('array')
        elif e == 0x0E:
            out.append('string')
        else:
            out.append('elem%02X' % e)
    return out


def find_method(md, tidx, name, want_ret=None, want_params=None):
    """在类型里按名字(可选返回类型/形参)定位方法，返回 0-based MethodDef 行号"""
    ms, _ = md.methods_of_type(tidx)
    for mi in ms:
        r = md.row(0x06, mi)
        if md.str_at(r['Name']) != name:
            continue
        blob = md.blob_at(r['Signature'])
        _e, tok, _pc = sig_ret(md, blob)
        if want_ret is not None and tok_type_name(md, tok) != want_ret:
            continue
        if want_params is not None and sig_param_types(md, blob) != want_params:
            continue
        return mi
    return None


def mdef_token(mi0):
    """0-based MethodDef 行号 -> 令牌"""
    return 0x06000000 | (mi0 + 1)


# ------------------------------------------------------------------ 主体

def build_bodies(md):
    tin = M.find_type(md, 'UnityEngine.Input')
    tou = M.find_type(md, 'UnityEngine.Touch')
    vec2 = M.find_type(md, 'UnityEngine.Vector2')
    if None in (tin, tou, vec2):
        raise SystemExit('找不到 UnityEngine.Input / Touch / Vector2 类型')

    t_touchcount = find_method(md, tin, 'get_touchCount')
    t_mousedown = find_method(md, tin, 'GetMouseButtonDown')
    t_mousepos = find_method(md, tin, 'get_mousePosition')
    t_gettouch = find_method(md, tin, 'GetTouch')
    t_touches = find_method(md, tin, 'get_touches')
    t_phase = find_method(md, tou, 'get_phase')
    t_position = find_method(md, tou, 'get_position')
    # Vector2 有两个 op_Implicit，取「返回 Vector2」的那个
    t_implicit = find_method(md, vec2, 'op_Implicit', want_ret='UnityEngine.Vector2')

    need = {'Input.get_touchCount': t_touchcount, 'Input.GetMouseButtonDown': t_mousedown,
            'Input.get_mousePosition': t_mousepos, 'Input.GetTouch': t_gettouch,
            'Input.get_touches': t_touches, 'Touch.get_phase': t_phase,
            'Touch.get_position': t_position, 'Vector2.op_Implicit(Vector3)': t_implicit}
    missing = [k for k, v in need.items() if v is None]
    if missing:
        raise SystemExit('定位失败: ' + ', '.join(missing))

    print('  定位结果（0-based MethodDef 行号 / 令牌）:')
    for k, v in need.items():
        print('    %-32s row=%-5d tok=0x%08X' % (k, v, mdef_token(v)))

    tok_md = struct.pack('<I', mdef_token(t_mousedown))
    tok_mp = struct.pack('<I', mdef_token(t_mousepos))
    tok_im = struct.pack('<I', mdef_token(t_implicit))
    tok_gt = struct.pack('<I', mdef_token(t_touches))
    # IL 指令里的类型令牌是「完整元数据令牌」（实测 08 00 00 02 = TypeDef#8）
    tok_touch_typedef = struct.pack('<I', 0x02000000 | M.find_type(md, 'UnityEngine.Touch'))
    print('    Touch 完整令牌 = 0x%08X' % (0x02000000 | M.find_type(md, 'UnityEngine.Touch')))

    body_touchcount = b'\x16' + b'\x28' + tok_md + b'\x2A'      # ldc.i4.0; call GetMouseButtonDown; ret
    body_phase = b'\x16\x2A'                                    # ldc.i4.0; ret
    body_position = b'\x28' + tok_mp + b'\x28' + tok_im + b'\x2A'   # call get_mousePosition; call op_Implicit; ret
    # touchCount 已被改成 1（鼠标按下那帧），所以 GetTouch 必须返回一个「类型正确」的 Touch，
    # 否则 Mono 验证器会报 InvalidProgramException（栈上是 int32 却要返回 32 字节结构体）。
    # 不用局部变量（拿不到 LocalVarSig 令牌）的迂回方案：
    #   get_touches -> newarr = 1 个全零 Touch 的数组（Touch 全零 => phase=0=Began、position=(0,0)）
    #   GetTouch    -> 取该数组第 0 个（ldelem.any），类型完全合法
    body_touches = b'\x17' + b'\x8D' + tok_touch_typedef + b'\x2A'          # ldc.i4.1; newarr Touch; ret
    body_gettouch = (b'\x28' + tok_gt + b'\x16' + b'\xA3' + tok_touch_typedef + b'\x2A')

    targets = [
        ('Input.get_touchCount', t_touchcount, body_touchcount, True),   # True: 需清 InternalCall
        ('Input.get_touches', t_touches, body_touches, False),
        ('Input.GetTouch', t_gettouch, body_gettouch, True),
        ('Touch.get_phase', t_phase, body_phase, False),
        ('Touch.get_position', t_position, body_position, False),
    ]
    return targets


def apply_patch(path, dry=True):
    data = bytearray(open(path, 'rb').read())
    md = M.Metadata(bytes(data))
    print('  程序集: %s (%d B)' % (os.path.basename(path), len(data)))
    targets = build_bodies(md)

    layout, blob = {}, b''
    for label, mi, body, _clear in targets:
        pad = (-len(blob)) % 4
        blob += b'\x00' * pad
        layout[label] = len(blob)
        blob += bytes([(len(body) << 2) | 0x02]) + body     # tiny 头
        print('    %-24s IL=%s' % (label, body.hex(' ')))
    need_len = len(blob)

    pe = md.pe
    target = None
    for pref in ('.text', None):
        for s in pe.secs:
            if s['name'] == '.reloc':
                continue
            if pref and s['name'] != pref:
                continue
            slack_off = s['roff'] + s['vsize']
            slack_len = (s['roff'] + s['rsize']) - slack_off
            if slack_len < need_len:
                continue
            # 在整段松量里扫一段连续零（前面可能已被其它补丁占用）
            window = bytes(data[slack_off:slack_off + slack_len])
            run = 0
            for i, b in enumerate(window):
                run = run + 1 if b == 0 else 0
                if run == need_len:
                    target = (s, slack_off + i - need_len + 1, slack_len)
                    break
            if target:
                break
        if target:
            break
    if target is None:
        print('  !! 找不到 %d B 的零填充松量' % need_len)
        return 0
    s, base_off, slack_len = target
    print('  注入区: 节 %s 文件偏移 0x%X（该节松量 %d B，本次用 %d B）'
          % (s['name'], base_off, slack_len, need_len))
    if dry:
        print('  [dry] 未写入。加 --apply 实际打补丁。')
        return len(targets)

    bak = path + '.bak_shim'
    if os.path.exists(bak):
        print('  备份已存在（保留首次补丁前的版本）-> %s' % os.path.basename(bak))
    else:
        shutil.copy2(path, bak)
        print('  已备份 -> %s' % os.path.basename(bak))
    data[base_off:base_off + need_len] = blob
    for label, mi, body, clear_flag in targets:
        row = md.row(0x06, mi)
        va = s['va'] + (base_off + layout[label] - s['roff'])
        struct.pack_into('<I', data, row['_off_RVA'], va)
        old_impl = row['ImplFlags']
        new_impl = (old_impl & ~0x1000) & 0xFFFF if clear_flag else old_impl
        if clear_flag:
            struct.pack_into('<H', data, row['_off_ImplFlags'], new_impl)
        print('    %-24s VA=0x%06X  RVA 0x%06X->0x%06X  ImplFlags 0x%04X->0x%04X'
              % (label, va, row['RVA'], va, old_impl, new_impl))
    open(path, 'wb').write(bytes(data))
    print('  已写回 %s（%d 处）' % (os.path.basename(path), len(targets)))
    return len(targets)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    path = sys.argv[1]
    dry = '--apply' not in sys.argv
    print('=== 鼠标模拟触摸补丁 %s ===' % ('(dry-run)' if dry else '(APPLY)'))
    apply_patch(path, dry=dry)


if __name__ == '__main__':
    main()
