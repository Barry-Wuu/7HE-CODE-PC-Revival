# -*- coding: utf-8 -*-
"""全局修复：光照贴图按 RGBM 编码，抵消桌面与 iOS 的解码差异。

Unity 内置着色器 UnityCG.cginc 里 DecodeLightmap():
    #if defined(SHADER_API_GLES) && defined(SHADER_API_MOBILE)
        return 2.0 * color.rgb;                 // iOS：Double-LDR
    #else
        return (8.0 * color.a) * color.rgb;     // 桌面：RGBM（alpha 当倍率）
    #endif
本作光照贴图是 iOS 数据（RGB24 / PVRTC_RGB4，无 alpha），桌面端 a=1 被按 8x 解码
-> 全场景过曝约 4 倍（实测原生过曝像素 7.6%，本机 62%）。

修法：RGB 原样保留，把 alpha 设成 1/4 = 64。桌面解码即 8*(64/255)*RGB ≈ 2.0*RGB，
与 iOS 的 2.0*RGB 完全一致；RGB 精度不受影响。存储格式统一改 RGBA32（无损）。

用法:
  python lm_fix_global.py                演练 + 写出临时文件
  python lm_fix_global.py --alpha 0.25 --apply
"""
import sys, os, shutil, json
sys.path.insert(0, r'D:/B++/WorkBuddy/7he_out')
import unitypy_v8patch; unitypy_v8patch.apply_patch()
import UnityPy, numpy as np
from PIL import Image

PRISTINE_DIR = r'D:/7he Code/_raw/v1.0/Payload/7HE CODE.app/Data/'
DEPLOYED_DIR = r'D:/7he Code/run32/7HECODE_Data/'
ALPHA = 0.25
for i, a in enumerate(sys.argv):
    if a == '--alpha':
        ALPHA = float(sys.argv[i + 1])
AV = int(round(ALPHA * 255))
APPLY = '--apply' in sys.argv
RGBA32 = 4

print('alpha = %.4f -> 存 %d/255 = %.5f ; 桌面解码倍率 = 8*a = %.4f （iOS 为 2.0）'
      % (ALPHA, AV, AV / 255, 8 * AV / 255))

# 找出所有光照贴图所在文件
alltex = json.load(open(r'D:/B++/WorkBuddy/7he_out/alltex.json'))
by_file = {}
for t in alltex:
    if t['name'].lower().startswith('lightmap'):
        by_file.setdefault(t['file'], []).append(t['name'])
print('光照贴图分布:', {k: len(v) for k, v in by_file.items()})

for fn, names in sorted(by_file.items()):
    prist = PRISTINE_DIR + fn
    dep = DEPLOYED_DIR + fn
    if not (os.path.exists(prist) and os.path.exists(dep)):
        print('!! 跳过 %s（缺文件）' % fn); continue
    print('\n===== %s  (%d 张光照贴图)' % (fn, len(names)))

    # 原始像素源
    src = {}
    env0 = UnityPy.load(prist)
    sf0 = [f for f in env0.files.values() if f.__class__.__name__ == 'SerializedFile'][0]
    for o in sf0.objects.values():
        if o.type.name != 'Texture2D':
            continue
        d = o.read()
        if d.m_Name in names:
            src[d.m_Name] = (d.image.convert('RGB'), int(d.m_TextureFormat))

    env = UnityPy.load(dep)
    sf = [f for f in env.files.values() if f.__class__.__name__ == 'SerializedFile'][0]
    n = 0
    for o in sf.objects.values():
        if o.type.name != 'Texture2D':
            continue
        d = o.read()
        if d.m_Name not in names or d.m_Name not in src:
            continue
        rgbimg, fmt0 = src[d.m_Name]
        a = np.asarray(rgbimg).astype(np.uint8)
        rgba = np.dstack([a, np.full(a.shape[:2], AV, np.uint8)])
        d.set_image(Image.fromarray(rgba, 'RGBA'), target_format=RGBA32, mipmap_count=1)
        d.save()
        n += 1
    print('  改写 %d 张' % n)
    out = sf.save()
    if APPLY:
        bak = dep + '.bak_lmglobal'
        if not os.path.exists(bak):
            shutil.copy2(dep, bak); print('  备份 ->', os.path.basename(bak))
        open(dep, 'wb').write(out)
        print('  写回 %s  %d -> %d B' % (fn, os.path.getsize(bak), len(out)))
    else:
        p = r'D:/B++/WorkBuddy/7he_out/_gl_%s' % fn
        open(p, 'wb').write(out)
        print('  [dry] 写到', p, len(out))

# 回读校验
print('\n=== 回读校验 ===')
for fn, names in sorted(by_file.items()):
    dep = DEPLOYED_DIR + fn
    if not os.path.exists(dep):
        continue
    tgt = dep if APPLY else (r'D:/B++/WorkBuddy/7he_out/_gl_%s' % fn)
    env = UnityPy.load(tgt)
    sf = [f for f in env.files.values() if f.__class__.__name__ == 'SerializedFile'][0]
    cnt = 0; bad = 0
    for o in sf.objects.values():
        if o.type.name != 'Texture2D':
            continue
        d = o.read()
        if d.m_Name not in names:
            continue
        img = np.asarray(d.image.convert('RGBA'))
        if int(img[..., 3].min()) != AV or int(img[..., 3].max()) != AV:
            bad += 1
            print('  !! %s alpha 异常 (%d..%d)' % (d.m_Name, img[..., 3].min(), img[..., 3].max()))
        else:
            cnt += 1
            if cnt <= 2:
                print('  OK %-28s fmt=%d %s mean=%s alpha=%d'
                      % (d.m_Name, int(d.m_TextureFormat), img.shape[:2],
                         img[..., :3].reshape(-1, 3).mean(0).round(1), int(img[0, 0, 3])))
    print('  %s: %d 张 alpha= %d，异常 %d' % (fn, cnt, AV, bad))
