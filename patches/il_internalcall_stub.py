#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
il_internalcall_stub.py — 纯 Python 的 .NET 程序集 InternalCall 方法打桩器

用途：把 iOS 版 UnityEngine.dll 里 [MethodImpl(InternalCall)] 的方法
（如 iPhoneSettings.get_generation）替换成返回默认值的 IL 方法体，
使桌面 Unity 播放器不再抛 MissingMethodException。

原理：
  1. 解析 PE + CLI metadata（#~ / #Strings / #Blob）表，定位 MethodDef 行
  2. 在目标节（.text）的 raw 松量（rsize - vsize）里注入一段 IL 方法体
  3. 把 MethodDef 行的 RVA 指向注入体，并把 ImplFlags 清 0（去掉 InternalCall 标志）

无第三方依赖。仅供老 Unity 项目跨平台复活使用。

用法：
  python il_internalcall_stub.py <dll> --list <TypeFullName>      # 列出某类型的方法及其标志
  python il_internalcall_stub.py <dll> --list-internalcall        # 全局列出 InternalCall 方法
  python il_internalcall_stub.py <dll> --stub iPhoneSettings iPhoneInput   # 打桩（原地改，先自行备份）
"""
import struct
import sys
import os
import shutil

# ---------------------------------------------------------------- PE


class PEImage:
    def __init__(self, data):
        self.d = data
        if data[:2] != b'MZ':
            raise ValueError('不是 PE 文件')
        e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
        if data[e_lfanew:e_lfanew + 4] != b'PE\0\0':
            raise ValueError('PE 签名缺失')
        coff = e_lfanew + 4
        nsec = struct.unpack_from('<H', data, coff + 2)[0]
        opt_size = struct.unpack_from('<H', data, coff + 16)[0]
        opt = coff + 20
        magic = struct.unpack_from('<H', data, opt)[0]
        self.pe32plus = (magic == 0x20B)
        self.dd = opt + (112 if self.pe32plus else 96)
        self.secs = []
        so = opt + opt_size
        for i in range(nsec):
            b = so + 40 * i
            name = data[b:b + 8].rstrip(b'\0').decode('latin1')
            vsize, vaddr, rsize, roff = struct.unpack_from('<IIII', data, b + 8)
            self.secs.append({'name': name, 'va': vaddr, 'vsize': vsize,
                              'rsize': rsize, 'roff': roff})

    def sec_of(self, rva):
        for s in self.secs:
            if s['va'] <= rva < s['va'] + max(s['vsize'], s['rsize']):
                return s
        return None

    def rva2off(self, rva):
        s = self.sec_of(rva)
        if s is None:
            raise ValueError('RVA 0x%X 不在任何节中' % rva)
        return s['roff'] + (rva - s['va'])

    def data_dir(self, i):
        return struct.unpack_from('<II', self.d, self.dd + 8 * i)


# ---------------------------------------------------------------- 元数据

# 表定义（仅需 id <= 0x06 的；其余按 row size 求和跳过即可，但为定位必须知道每张存在表的行宽）
U2, U4, STR, GUIDX, BLOB = 'u2', 'u4', 'str', 'guid', 'blob'


def coded(tables, bits):
    return ('coded', tuple(tables), bits)


TABLES = {
    0x00: [('Generation', U2), ('Name', STR), ('Mvid', GUIDX), ('EncId', GUIDX), ('EncBaseId', GUIDX)],
    0x01: [('ResolutionScope', coded((0x00, 0x1A, 0x23, 0x01), 2)), ('Name', STR), ('Namespace', STR)],
    0x02: [('Flags', U4), ('Name', STR), ('Namespace', STR),
           ('Extends', coded((0x02, 0x01, 0x1B), 2)), ('FieldList', ('idx', 0x04)), ('MethodList', ('idx', 0x06))],
    0x03: [('Field', ('idx', 0x04))],
    0x04: [('Flags', U2), ('Name', STR), ('Signature', BLOB)],
    0x05: [('Method', ('idx', 0x06))],
    0x06: [('RVA', U4), ('ImplFlags', U2), ('Flags', U2), ('Name', STR), ('Signature', BLOB), ('ParamList', ('idx', 0x08))],
    # 以下仅为了能跳过（行宽按同构推断；未列出的表若存在会报错提醒）
    0x07: [('Class', ('idx', 0x02)), ('Name', STR), ('Signature', BLOB)],
    0x08: [('Flags', U2), ('Sequence', U2), ('Name', STR)],
    0x09: [('Class', ('idx', 0x02)), ('Name', STR), ('Type', BLOB)],
    0x0A: [('Class', ('idx', 0x02)), ('Name', STR), ('Type', BLOB)],
    0x0B: [('Flags', U2), ('Name', STR), ('Type', BLOB)],
    0x0C: [('CustomAttributeType', coded((0x00, 0x00, 0x06, 0x0A, 0x00), 3)), ('Parent', coded((0x06, 0x04, 0x01, 0x02, 0x08, 0x09, 0x0A, 0x00, 0x0E, 0x17, 0x14, 0x11, 0x1A, 0x1B, 0x20, 0x23, 0x26, 0x27, 0x28, 0x2A, 0x2B), 5)), ('Type', BLOB), ('Value', BLOB)],
    0x0D: [('Parent', coded((0x02, 0x06, 0x20), 2)), ('Action', U2), ('PermissionSet', BLOB)],
    0x0E: [('Flags', U4), ('Name', STR), ('Namespace', STR), ('Extends', coded((0x02, 0x01, 0x1B), 2)), ('FieldList', ('idx', 0x04)), ('MethodList', ('idx', 0x06))],
    0x0F: [('Class', ('idx', 0x02)), ('Name', STR), ('Type', BLOB)],
    0x10: [('Offset', U4), ('Name', STR)],
    0x11: [('Flags', U4), ('Name', STR), ('Value', BLOB)],
    0x12: [('Flags', U2), ('Name', STR)],
    0x13: [('Class', ('idx', 0x02)), ('Name', STR)],
    0x14: [('EventFlags', U2), ('Name', STR), ('EventType', coded((0x02, 0x01, 0x1B), 2))],
    0x15: [('Flags', U2), ('Name', STR), ('Type', BLOB)],
    0x16: [('Method', ('idx', 0x06))],
    0x17: [('Flags', U4), ('Name', STR)],
    0x18: [('Name', STR), ('HashValue', BLOB)],
    0x19: [('Assembly', ('idx', 0x20))],
    0x1A: [('Name', STR), ('ModuleName', STR)],
    0x1B: [('Name', STR)],
    0x1C: [('Implementation', coded((0x26, 0x23, 0x27), 2)), ('Flags', U4), ('Name', STR), ('Namespace', STR)],
    0x1D: [('Class', ('idx', 0x02)), ('Name', STR), ('Type', BLOB)],
    0x1E: [('Flags', U4), ('Type', ('idx', 0x04)), ('Value', STR)],
    0x1F: [('Type', ('idx', 0x06))],
    0x20: [('HashAlgId', U4), ('MajorVersion', U2), ('MinorVersion', U2), ('BuildNumber', U2),
           ('RevisionNumber', U2), ('Flags', U4), ('PublicKey', BLOB), ('Name', STR), ('Culture', STR)],
    0x21: [('MajorVersion', U2), ('MinorVersion', U2), ('BuildNumber', U2), ('RevisionNumber', U2),
           ('Flags', U4), ('PublicKeyOrToken', BLOB), ('Name', STR), ('Culture', STR), ('HashValue', BLOB)],
    0x22: [('Flags', U4), ('Name', STR), ('HashValue', BLOB)],
    0x23: [('Flags', U4), ('Name', STR), ('Culture', STR), ('HashValue', BLOB)],
    0x24: [('Offset', U4), ('Flags', U4), ('Name', STR), ('Implementation', ('idx', 0x26))],
    0x25: [('Number', U2), ('Flags', U2), ('Owner', coded((0x02, 0x06), 1)), ('Name', STR), ('Namespace', STR)],
    0x26: [('Number', U2), ('Flags', U2), ('Owner', coded((0x02, 0x06), 1)), ('Name', STR), ('Namespace', STR)],
    0x27: [('Flags', U2), ('MajorVersion', U2), ('MinorVersion', U2), ('BuildNumber', U2), ('RevisionNumber', U2),
           ('PublicKeyOrToken', BLOB), ('Name', STR), ('Culture', STR), ('HashValue', BLOB)],
    0x28: [('Flags', U4), ('RefType', coded((0x02, 0x01, 0x1B), 2))],
    0x29: [('MajorVersion', U2), ('MinorVersion', U2), ('Name', STR), ('Culture', STR),
           ('PublicKeyOrToken', BLOB), ('HashValue', BLOB), ('Flags', U4)],
    0x2A: [('Class', ('idx', 0x02)), ('Name', STR), ('Signature', BLOB)],
    0x2B: [('Owner', coded((0x02, 0x06), 1)), ('Name', STR)],
}


class Metadata:
    def __init__(self, data):
        self.pe = PEImage(data)
        self.d = data
        cli_rva, cli_size = self.pe.data_dir(14)
        cli = self.pe.rva2off(cli_rva)
        md_rva, md_size = struct.unpack_from('<II', data, cli + 8)
        md = self.pe.rva2off(md_rva)
        self.md_root = md
        sig = struct.unpack_from('<I', data, md)[0]
        if sig != 0x424A5342:
            raise ValueError('元数据签名不符: %08X' % sig)
        ver_len = struct.unpack_from('<I', data, md + 12)[0]
        p = md + 16 + ((ver_len + 3) // 4) * 4
        nstreams = struct.unpack_from('<H', data, p + 2)[0]
        p += 4
        self.streams = {}
        for _ in range(nstreams):
            off, size = struct.unpack_from('<II', data, p)
            p += 8
            end = data.index(b'\0', p)
            name = data[p:end].decode('latin1')
            p = end + 1
            p = (p + 3) & ~3
            self.streams[name] = (md + off, size)
        # #~ 头
        tb, tsize = self.streams['#~'] if '#~' in self.streams else self.streams['#-']
        heap = data[tb + 6]
        valid = struct.unpack_from('<Q', data, tb + 8)[0]
        self.tables_off = tb + 24
        self.rows = {}
        # HeapSizes 标志位：置位 = 该堆索引为 4 字节，未置位 = 2 字节
        idx_sz = {0: 4 if heap & 1 else 2, 1: 4 if heap & 2 else 2, 2: 4 if heap & 4 else 2}
        self.str_sz, self.guid_sz, self.blob_sz = idx_sz[0], idx_sz[1], idx_sz[2]
        p = self.tables_off
        for t in range(64):
            if valid & (1 << t):
                n = struct.unpack_from('<I', data, p)[0]
                p += 4
                self.rows[t] = n
            else:
                self.rows[t] = 0
        self.table_start = p
        # 计算每张表的行宽与偏移
        self.row_size = {}
        self.table_off = {}
        cur = self.table_start
        for t in range(64):
            n = self.rows.get(t, 0)
            if n == 0 or t > 0x06:
                continue   # 只需定位 TypeDef(0x02)/MethodDef(0x06)；其偏移只由 id<=0x06 的表决定
            rs = self._row_size(t)
            self.row_size[t] = rs
            self.table_off[t] = cur
            cur += rs * n
        # 堆
        self.strings = self.streams.get('#Strings', (0, 0))[0]
        self.blob = self.streams.get('#Blob', (0, 0))[0]

    def _col_size(self, col):
        kind = col if isinstance(col, str) else col[0]
        if kind == 'u2':
            return 2
        if kind == 'u4':
            return 4
        if kind == STR:
            return self.str_sz
        if kind == GUIDX:
            return self.guid_sz
        if kind == BLOB:
            return self.blob_sz
        if kind == 'idx':
            return 2 if self.rows.get(col[1], 0) < 65536 else 4
        if kind == 'coded':
            tabs, bits = col[1], col[2]
            mx = max([self.rows.get(x, 0) for x in tabs] or [0])
            return 2 if mx < (1 << (16 - bits)) else 4
        raise ValueError('未知列类型 %r' % (col,))

    def _row_size(self, t):
        cols = TABLES.get(t)
        if cols is None:
            raise ValueError('元数据里存在未定义行宽的表 0x%02X，需补充 TABLES' % t)
        return sum(self._col_size(c) for _n, c in cols)

    def row(self, table, i):
        """返回第 i 行（0-based）的 {列名: 值}"""
        cols = TABLES[table]
        off = self.table_off[table] + i * self.row_size[table]
        out = {}
        for name, col in cols:
            size = self._col_size(col)
            kind = col[0]
            if kind == 'u2':
                out[name] = struct.unpack_from('<H', self.d, off)[0]
            elif kind == 'u4':
                out[name] = struct.unpack_from('<I', self.d, off)[0]
            else:
                out[name] = int.from_bytes(self.d[off:off + size], 'little')
            out['_off_' + name] = off
            off += size
        return out

    def str_at(self, idx):
        p = self.strings + idx
        e = self.d.index(b'\0', p)
        return self.d[p:e].decode('utf-8', 'replace')

    def blob_at(self, idx):
        p = self.blob + idx
        b0 = self.d[p]
        if b0 & 0x80 == 0:
            ln, n = b0, 1
        elif b0 & 0xC0 == 0x80:
            ln, n = ((b0 & 0x3F) << 8) | self.d[p + 1], 2
        else:
            ln, n = ((b0 & 0x1F) << 24) | (self.d[p + 1] << 16) | (self.d[p + 2] << 8) | self.d[p + 3], 4
        return self.d[p + n:p + n + ln]

    def type_name(self, tidx):
        if tidx == 0:
            return None
        r = self.row(0x02, tidx - 1)
        ns = self.str_at(r['Namespace'])
        nm = self.str_at(r['Name'])
        return (ns + '.' + nm) if ns else nm

    def type_of_token(self, tok):
        """TypeDefOrRef 压缩令牌 -> (表号, 1-based 行号)"""
        tag = tok & 3
        row = tok >> 2
        return {0: (0x02, row), 1: (0x01, row), 2: (0x1B, row)}.get(tag)

    def is_enum_token(self, tok):
        """判 TypeDefOrRef 令牌指向的是不是枚举（TypeDef 里有 value__ 字段）"""
        t = self.type_of_token(tok)
        if not t:
            return False
        table, row = t
        if table != 0x02 or not row:
            return False
        r = self.row(0x02, row - 1)
        start = (r['FieldList'] - 1) if r['FieldList'] else 0
        end = self.rows.get(0x04, 0)
        if row < self.rows.get(0x02, 0):
            nr = self.row(0x02, row)
            if nr['FieldList']:
                end = nr['FieldList'] - 1
        for fi in range(start, end):
            if self.str_at(self.row(0x04, fi)['Name']) == 'value__':
                return True
        return False

    def methods_of_type(self, tidx):
        """返回该 TypeDef（1-based）的 MethodDef 行号列表（0-based）"""
        r = self.row(0x02, tidx - 1)
        start = r['MethodList'] - 1 if r['MethodList'] else 0
        # 下一个 TypeDef 的 MethodList 作为上界
        end = self.rows.get(0x06, 0)
        if tidx < self.rows.get(0x02, 0):
            nr = self.row(0x02, tidx)   # 下一个类型
            if nr['MethodList']:
                end = nr['MethodList'] - 1
        return list(range(start, end)), r


# ---------------------------------------------------------------- IL 编码

def read_compressed(b, p):
    """读 ECMA-335 压缩无符号整数"""
    b0 = b[p]
    if b0 & 0x80 == 0:
        return b0, p + 1
    if b0 & 0xC0 == 0x80:
        return ((b0 & 0x3F) << 8) | b[p + 1], p + 2
    return ((b0 & 0x1F) << 24) | (b[p + 1] << 16) | (b[p + 2] << 8) | b[p + 3], p + 4


def elem_kind(e):
    """返回类型元素码 -> 默认值种类"""
    if e == 0x01:
        return 'void'
    if e in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09):   # bool/char/i1..u4
        return 'i4'
    if e in (0x0A, 0x0B):                                       # i8/u8
        return 'i8'
    if e == 0x0C:
        return 'r4'
    if e == 0x0D:
        return 'r8'
    if e in (0x0E, 0x12, 0x1C, 0x1D):                           # string/class/object/数组
        return 'null'
    return None


_BODY_CODE = {
    'void': b'\x2A',                                  # ret
    'i4':   b'\x16\x2A',                              # ldc.i4.0 ; ret
    'i8':   b'\x16\x69\x2A',                          # ldc.i4.0 ; conv.i8 ; ret
    'r4':   b'\x22\x00\x00\x00\x00\x2A',              # ldc.r4 0 ; ret
    'r8':   b'\x23' + b'\x00' * 8 + b'\x2A',          # ldc.r8 0 ; ret
    'null': b'\x14\x2A',                              # ldnull ; ret
}


def make_body(kind):
    """按默认值种类生成 tiny 格式的 IL 方法体"""
    code = _BODY_CODE.get(kind)
    if code is None:
        return None
    assert len(code) <= 63, '方法体过大，需改用 fat 头'
    return bytes([(len(code) << 2) | 0x02]) + code


def method_ret_kind(md, sigblob):
    """从方法签名解出返回类型对应的默认值种类；无法处理时返回 'unsupported'"""
    b = sigblob
    if not b:
        return None
    cc = b[0]
    p = 1
    if cc & 0x10:                       # GENERIC：先跳过泛型参数个数
        _gc, p = read_compressed(b, p)
    _pc, p = read_compressed(b, p)      # 形参个数（这一步之前漏了，导致返回元素错位）
    if p >= len(b):
        return None
    e = b[p]
    k = elem_kind(e)
    if k:
        return k
    if e == 0x11:                       # VALUETYPE：结构化返回，仅枚举可安全返回 0
        tok, _ = read_compressed(b, p + 1)
        return 'i4' if md.is_enum_token(tok) else 'unsupported'
    return 'unsupported'


# ---------------------------------------------------------------- 主逻辑

def find_type(md, fullname):
    want_ns, _, want_nm = fullname.rpartition('.')
    for i in range(1, md.rows.get(0x02, 0) + 1):
        r = md.row(0x02, i - 1)
        if md.str_at(r['Name']) == want_nm and md.str_at(r['Namespace']) == want_ns:
            return i
    return None


def list_type(md, fullname):
    tidx = find_type(md, fullname)
    if tidx is None:
        print('  找不到类型', fullname)
        return
    ms, r = md.methods_of_type(tidx)
    print('类型 %s (TypeDef#%d) 方法 %d 个:' % (fullname, tidx, len(ms)))
    for mi in ms:
        m = md.row(0x06, mi)
        nm = md.str_at(m['Name'])
        elem = method_ret_kind(md, md.blob_at(m['Signature']))
        flag = []
        if m['ImplFlags'] & 0x1000:
            flag.append('InternalCall')
        if m['ImplFlags'] & 0x0001:
            flag.append('Native')
        if m['ImplFlags'] & 0x0002:
            flag.append('Unmanaged')
        if m['RVA'] == 0:
            flag.append('RVA=0')
        print('   %-42s RVA=0x%06X Impl=0x%04X Flags=0x%04X 返回=%s  %s'
              % (nm, m['RVA'], m['ImplFlags'], m['Flags'], elem, ','.join(flag)))


def list_internalcall(md):
    n = md.rows.get(0x06, 0)
    print('全程序集 InternalCall 方法：')
    for i in range(n):
        m = md.row(0x06, i)
        if m['ImplFlags'] & 0x1000:
            print('   MethodDef#%-5d %-46s RVA=0x%X' % (i + 1, md.str_at(m['Name']), m['RVA']))


def stub_types(path, type_names, dry=True):
    data = bytearray(open(path, 'rb').read())
    md = Metadata(bytes(data))
    jobs = []           # (MethodDef行号, 方法名, 类型名)
    for tn in type_names:
        tidx = find_type(md, tn)
        if tidx is None:
            print('  !! 找不到类型', tn)
            continue
        ms, _ = md.methods_of_type(tidx)
        for mi in ms:
            m = md.row(0x06, mi)
            if m['ImplFlags'] & 0x1000:      # 仅处理 InternalCall
                jobs.append((mi, md.str_at(m['Name']), tn))
    if not jobs:
        print('  没有需要打桩的 InternalCall 方法')
        return 0
    # 按默认值种类去重：同类方法共用一个方法体（IL 体只读，共享完全安全）
    kinds = {}
    for mi, nm, tn in jobs:
        m = md.row(0x06, mi)
        kind = method_ret_kind(md, md.blob_at(m['Signature']))
        if kind is None or make_body(kind) is None:
            print('  -- 跳过 %s.%s（返回类型 %s 无法简单默认化）' % (tn, nm, kind))
            continue
        kinds.setdefault(kind, []).append((mi, nm, tn))
    if not kinds:
        print('  没有可打桩的方法')
        return 0
    layout, blob = {}, b''
    for kind in sorted(kinds):
        pad = (-len(blob)) % 4
        blob += b'\x00' * pad
        layout[kind] = len(blob)
        blob += make_body(kind)
    need = len(blob)
    # 选节：优先 .text；跳过 .reloc（其尾部紧跟重定位终止块，写入会被当成新重定位块）
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
            if slack_len >= need and bytes(data[slack_off:slack_off + need]) == b'\x00' * need:
                target = (s, slack_off, slack_len)
                break
        if target:
            break
    if target is None:
        print('  !! 找不到足够大的安全零填充松量（需要 %d B）' % need)
        return 0
    s, base_off, slack_len = target
    print('  方法体去重: %d 个方法 -> %d 种方法体, 共 %d B' % (sum(len(v) for v in kinds.values()), len(kinds), need))
    print('  注入区: 节 %s 文件偏移 0x%X（松量 %d B）' % (s['name'], base_off, slack_len))
    for kind in sorted(kinds):
        print('     %-5s 体 %s' % (kind, make_body(kind).hex(' ')))
    if dry:
        for kind in sorted(kinds):
            for mi, nm, tn in kinds[kind]:
                print('   [dry] %-20s %-38s -> %s' % (tn, nm, kind))
        return sum(len(v) for v in kinds.values())
    # 写入方法体
    data[base_off:base_off + need] = blob
    n = 0
    for kind in sorted(kinds):
        va = s['va'] + (base_off + layout[kind] - s['roff'])
        for mi, nm, tn in kinds[kind]:
            row = md.row(0x06, mi)
            struct.pack_into('<I', data, row['_off_RVA'], va)
            struct.pack_into('<H', data, row['_off_ImplFlags'], (row['ImplFlags'] & ~0x1000) & 0xFFFF)
            print('   %-20s %-38s VA=0x%06X ImplFlags 0x%04X->0x%04X'
                  % (tn, nm, va, row['ImplFlags'], (row['ImplFlags'] & ~0x1000) & 0xFFFF))
            n += 1
    open(path, 'wb').write(bytes(data))
    print('  已写回 %s（共 %d 个方法）' % (path, n))
    return n


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    path = sys.argv[1]
    mode = sys.argv[2]
    if mode == '--list':
        md = Metadata(open(path, 'rb').read())
        for tn in sys.argv[3:]:
            list_type(md, tn)
    elif mode == '--list-internalcall':
        md = Metadata(open(path, 'rb').read())
        list_internalcall(md)
    elif mode == '--stub':
        dry = '--apply' not in sys.argv
        names = [a for a in sys.argv[3:] if not a.startswith('--')]
        print('打桩目标:', names, '(dry=%s)' % dry)
        stub_types(path, names, dry=dry)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
