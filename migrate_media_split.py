#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
媒体目录分离迁移脚本 (旧结构 -> 新结构)

旧结构:
    用户/年份/YYYY-MM/
        ├── 媒体/          (图片与视频混放)
        ├── 视频封面/      (封面 jpg, 与 媒体/ 平级)
        └── 年份/YYYY-MM.md  (顶部/底部跨月导航)

新结构:
    用户/年份/YYYY-MM/
        ├── 图片/          (仅图片)
        ├── 视频/          (仅视频)
        │   └── 视频封面/  (封面 jpg, 位于视频文件夹下)
        └── 年份/YYYY-MM.md        (总 md, 链接指向 图片|视频/)
            YYYY-MM-图片.md        (仅含图片推文条目)
            YYYY-MM-视频.md        (仅含视频推文条目)

兼容性说明:
    - 旧结构(媒体/ + 平级 视频封面/): 媒体按扩展名分流, 封面移入 视频/视频封面/
    - 中间态(已迁移但封面与视频同目录): 视频/ 根下非视频文件自动归位 视频/视频封面/
    - md 封面链接三种状态(平级 视频封面/、与视频同目录、已正确)均归一化到 月份/视频/视频封面/

脚本执行步骤(每用户):
    1. 移动 媒体/ 下文件到 图片/ 或 视频/ (按扩展名); 封面文件统一移入 视频/视频封面/
    2. 删除空的 媒体/ 视频封面/ 目录
    3. 重写总 md 中所有链接: 封面 -> 视频/视频封面/, 媒体/ -> 图片|视频/ (按行内扩展名)
    4. 从总 md 解析推文条目(### 标题..互动数据行), 按媒体类型拆分重建 月份-图片.md / 月份-视频.md
    5. 为两类分文件补跨月导航(顶部上一月/底部下一月, 链接文字带类型后缀)

用法:
    python migrate_media_split.py                     # 处理 settings.json save_path 下所有用户
    python migrate_media_split.py --user screen_name  # 只处理指定用户
    python migrate_media_split.py --save-path /path   # 指定保存根目录(不读 settings.json)

脚本幂等: 已迁移的文件不动, 分文件 md 每次由总 md 重新解析重建(内容以总 md 为准)
"""

import argparse
import json
import os
import re
import shutil
import sys

_VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.m4v', '.mkv'}

# md 文件命名: 总 md 无后缀, 分文件带 -图片 / -视频
_MONTH_MD_RE = re.compile(r'^(\d{4}-\d{2})(-图片|-视频)?\.md$')
# 跨月导航行(兼容新旧样式): 整行匹配
_NAV_RE = re.compile(r'^(?:\[→ [^\]]+\]\([^)]+\.md\)|\*\*→→→ \[[^\]]+\]\([^)]+\.md\) ←←←\*\*)$')
# 媒体行: 图片行 display 为空; 视频封面行 display 为文件名
_MEDIA_LINE_RE = re.compile(r'^\[!\[(.*?)\]\(([^)]*)\)\]\(([^)]*)\)  $')
# 视频提示行 (📹📹 视频名 📹📹) 与 无封面回退行 (📹📹📹📹📹 链接)
_VIDEO_HINT_RE = re.compile(r'^📹📹 .+ 📹📹  $')
_VIDEO_NOCAP_RE = re.compile(r'^📹📹📹📹📹 .+  $')
# 互动数据收尾行
_STATS_RE = re.compile(r'^[\d,]+ Likes, [\d,]+ Retweets, [\d,]+ Replies$')
_TITLE_RE = re.compile(r'^### .+ \[原文\]\(.+\)$')
_DATE_RE = re.compile(r'^## \d{4}-\d{2}$')


def _read_settings_save_path():
    try:
        with open('settings.json', 'r', encoding='utf-8') as f:
            _sp = json.load(f).get('save_path', '')
    except Exception:
        _sp = ''
    if not _sp:
        _sp = '/download' if os.path.isdir('/download') else os.getcwd()
    return _sp


def _migrate_month_dir(month_dir: str, stat: dict) -> None:
    """移动 媒体/ 与 旧平级 视频封面/ 中的文件到 图片/、视频/、视频/视频封面/; 兼容上版迁移残留(视频/根下封面归位), 并删除空目录"""
    _media_dir = os.path.join(month_dir, '媒体')
    _cover_dir = os.path.join(month_dir, '视频封面')      # 旧平级封面目录
    _img_dir = os.path.join(month_dir, '图片')
    _vid_dir = os.path.join(month_dir, '视频')
    _vid_cover_dir = os.path.join(_vid_dir, '视频封面')   # 新封面目录: 视频/视频封面/

    def _move_to(_src, _target_dir):
        """移动单文件到目标目录; 目标已存在同名文件则视为已迁移跳过"""
        os.makedirs(_target_dir, exist_ok=True)
        _dst = os.path.join(_target_dir, os.path.basename(_src))
        if os.path.exists(_dst):
            return
        shutil.move(_src, _dst)
        stat['moved'] += 1

    if os.path.isdir(_media_dir):
        for _name in sorted(os.listdir(_media_dir)):
            _src = os.path.join(_media_dir, _name)
            if not os.path.isfile(_src):
                continue
            _ext = os.path.splitext(_name)[1].lower()
            _move_to(_src, _vid_dir if _ext in _VIDEO_EXTS else _img_dir)
        try:
            os.rmdir(_media_dir)    # 仅当目录已空才删除
        except OSError:
            pass
    if os.path.isdir(_cover_dir):   # 旧平级 视频封面/ -> 视频/视频封面/
        for _name in sorted(os.listdir(_cover_dir)):
            _src = os.path.join(_cover_dir, _name)
            if os.path.isfile(_src):
                _move_to(_src, _vid_cover_dir)
        try:
            os.rmdir(_cover_dir)
        except OSError:
            pass
    if os.path.isdir(_vid_dir):     # 上版迁移残留: 视频/ 根下非视频文件(封面) -> 视频/视频封面/
        for _name in sorted(os.listdir(_vid_dir)):
            _src = os.path.join(_vid_dir, _name)
            if not os.path.isfile(_src):
                continue
            if os.path.splitext(_name)[1].lower() not in _VIDEO_EXTS:
                _move_to(_src, _vid_cover_dir)


def _fix_cover_path(cover: str) -> str:
    """封面链接路径归一化: 旧平级(月份/视频封面/x.jpg) 与 上版残留(月份/视频/x.jpg) -> 月份/视频/视频封面/x.jpg; 已正确则原样返回"""
    if '视频/视频封面/' in cover:      # 新结构已正确
        return cover
    if '视频封面/' in cover:           # 旧平级封面目录
        return cover.replace('视频封面/', '视频/视频封面/', 1)
    if '/视频/' in cover:              # 上版迁移残留(封面与视频同目录)
        return cover.replace('/视频/', '/视频/视频封面/', 1)
    return cover


def _fix_md_links(md_path: str, stat: dict) -> None:
    """重写 md 中媒体链接: 封面行路径统一到 月份/视频/视频封面/, 媒体/ -> 图片|视频/ (按行内媒体文件扩展名)"""
    with open(md_path, 'r', encoding='utf-8-sig') as f:
        _lines = f.read().split('\n')
    _changed = 0
    for _i, _line in enumerate(_lines):
        # 封面行 [![文件名](封面路径)](视频路径): 仅修正封面路径, 避免误改视频链接
        _m = re.match(r'^\[!\[[^\]]+\]\(([^)]*)\)\]\([^)]*\)  $', _line)
        if _m:
            _cover_old = _m.group(1)
            _cover_new = _fix_cover_path(_cover_old)
            if _cover_new != _cover_old:
                _line = _line.replace('(' + _cover_old + ')', '(' + _cover_new + ')', 1)
                _changed += 1
        if '媒体/' in _line:
            # 提取该行媒体/ 后链接中的文件名扩展名, 决定替换为 图片/ 还是 视频/
            _m = re.search(r'媒体/([^)\s]+?\.(\w+))', _line)
            _ext = '.' + _m.group(2).lower() if _m else ''
            _line = _line.replace('媒体/', '视频/' if _ext in _VIDEO_EXTS else '图片/')
            _changed += 1
        _lines[_i] = _line
    if _changed:
        _tmp = md_path + '.tmp'
        with open(_tmp, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('\n'.join(_lines))
        os.replace(_tmp, md_path)
        stat['links_fixed'] += _changed


def _parse_total_md(total_md_path: str):
    """解析总 md, 返回 (header_lines, img_body_lines, vid_body_lines)

    img_body_lines / vid_body_lines 为重建分文件用的原始行(含日期标题行与条目行),
    条目已按媒体类型过滤: 图片系列只留图片媒体行, 视频系列只留提示行+封面行
    """
    with open(total_md_path, 'r', encoding='utf-8-sig') as f:
        _lines = f.read().split('\n')
    while _lines and not _lines[-1].strip():
        _lines.pop()    # 去掉末尾空行
    if len(_lines) > 3 and _NAV_RE.match(_lines[-1].strip()):
        _lines.pop()    # 剔除底部导航行
    _header = _lines[:3]
    _body = _lines[3:]
    _start = 0
    while _start < len(_body) and (not _body[_start].strip() or _NAV_RE.match(_body[_start].strip())):
        _start += 1     # 跳过 header 后的空行与顶部导航行
    _body = _body[_start:]

    _img_blocks, _vid_blocks = [], []   # ('date', line) 或 ('item', [lines])
    _cur = []           # 当前条目行
    _cur_img = _cur_vid = False
    _pending_date = None

    def _flush():
        nonlocal _cur, _cur_img, _cur_vid, _pending_date
        if _cur:
            if _pending_date:
                _cur.insert(0, _pending_date)
                _pending_date = None
            if _cur_img:
                _img_blocks.append(('item', _cur))
            if _cur_vid:
                _vid_blocks.append(('item', _cur))
        _cur, _cur_img, _cur_vid = [], False, False

    for _line in _body:
        _s = _line.strip()
        if _DATE_RE.match(_s):          # 日期标题行: 挂起, 归属下一条目所在系列
            _flush()
            _pending_date = _line
            continue
        if _TITLE_RE.match(_s):         # 新推文条目开始
            _flush()
            _cur = [_line]
            continue
        if _STATS_RE.match(_s):         # 互动数据行: 条目结束
            _cur.append(_line)
            _flush()
            continue
        if _cur:                        # 条目内: 文本行 / 媒体行
            _cur.append(_line)
            _m = _MEDIA_LINE_RE.match(_line)
            if _m:
                if _m.group(1):         # 视频封面行 (display 为文件名)
                    _cur_vid = True
                else:                   # 图片行 (display 为空)
                    _cur_img = True
            elif _VIDEO_HINT_RE.match(_line) or _VIDEO_NOCAP_RE.match(_line):
                _cur_vid = True
    _flush()

    _img_body = _render_blocks(_img_blocks, 'img')
    _vid_body = _render_blocks(_vid_blocks, 'vid')
    return _header, _img_body, _vid_body


def _should_keep(line: str, series: str) -> bool:
    """按系列过滤异类媒体行: 图片系列去掉 📹提示行/封面行, 视频系列去掉图片行; 其他行(标题/文本/互动/日期)保留"""
    _m = _MEDIA_LINE_RE.match(line)
    if _m:
        if series == 'img':
            return not _m.group(1)      # 图片系列只留 display 为空的图片行
        return bool(_m.group(1))        # 视频系列只留 display 非空的封面行
    if series == 'img':
        return not (_VIDEO_HINT_RE.match(line) or _VIDEO_NOCAP_RE.match(line))
    return True


def _render_blocks(blocks, series) -> str:
    """条目块 -> 正文文本: 行原样保留(含硬换行两空格), 条目之间空行分隔; 按系列过滤异类媒体行"""
    _parts = []
    for _kind, _content in blocks:
        if _kind == 'date':
            _parts.append(_content)
        else:
            for _line in _content:
                if _should_keep(_line, series):
                    _parts.append(_line)
            _parts.append('')
    return '\n'.join(_parts).rstrip('\n') + '\n'


def _migrate_user(user_dir: str, stat: dict) -> None:
    """处理单个用户目录: 迁移文件、修复总 md 链接、重建两类分文件 md"""
    # 1. 收集该用户全部月份 md 文件信息 (跨年)
    _total_mds = {}     # 月份 -> 总 md 路径
    _suffix_mds = {s: set() for s in ('-图片', '-视频')}    # 已存在的分文件月份
    for _root, _dirs, _names in os.walk(user_dir):
        for _f in _names:
            _m = _MONTH_MD_RE.match(_f)
            if not _m:
                continue
            _month, _suffix = _m.group(1), _m.group(2) or ''
            if os.path.basename(_root) != _month[:4]:
                continue    # 非年份目录下的旧版 md 仅跳过(不动)
            if _suffix:
                _suffix_mds[_suffix].add(_month)
            else:
                _total_mds[_month] = os.path.join(_root, _f)

    if not _total_mds:
        return

    # 2. 迁移月份目录下的媒体文件
    for _month, _total_path in sorted(_total_mds.items()):
        _month_dir = os.path.join(user_dir, _month[:4], _month)
        if os.path.isdir(_month_dir):
            _migrate_month_dir(_month_dir, stat)

    # 3. 修复总 md 链接 (先于解析, 保证分文件中的链接直接可用)
    for _total_path in _total_mds.values():
        _fix_md_links(_total_path, stat)

    # 4. 解析总 md, 重建两类分文件 (内容以总 md 为准, 幂等覆盖)
    _new_mds = {s: {} for s in ('-图片', '-视频')}       # 月份 -> 正文
    _headers = {s: {} for s in ('-图片', '-视频')}       # 月份 -> header 行
    for _month in sorted(_total_mds.keys()):
        _header, _img_body, _vid_body = _parse_total_md(_total_mds[_month])
        if _img_body.strip():
            _new_mds['-图片'][_month] = _img_body
            _headers['-图片'][_month] = _header
        if _vid_body.strip():
            _new_mds['-视频'][_month] = _vid_body
            _headers['-视频'][_month] = _header

    # 5. 落盘分文件; 解析后某系列某月无内容且磁盘已有该文件 -> 删除(与"该月无内容不建文件"规则一致), 且该月不参与导航链
    for _suffix in ('-图片', '-视频'):
        for _file_month in sorted(_suffix_mds[_suffix]):
            if _file_month not in _new_mds[_suffix]:
                _old = os.path.join(user_dir, _file_month[:4], f'{_file_month}{_suffix}.md')
                if os.path.exists(_old):
                    os.remove(_old)
                    stat['type_files_dropped'] += 1
        _all = sorted(_new_mds[_suffix].keys())
        for _m in _all:
            _body = _new_mds[_suffix].get(_m)
            if not _body:
                continue    # 该月该类型无内容(磁盘也不存在该文件), 不建
            _filename = os.path.join(user_dir, _m[:4], f'{_m}{_suffix}.md')
            _nav_top, _nav_bottom = '', ''
            _label = ' 图片' if _suffix == '-图片' else ' 视频' if _suffix == '-视频' else ''
            _idx = _all.index(_m)
            if _idx > 0:
                _prev_m = _all[_idx - 1]
                _target = os.path.join(user_dir, _prev_m[:4], f'{_prev_m}{_suffix}.md')
                _rel = os.path.relpath(_target, os.path.dirname(_filename)).replace('\\', '/')
                _nav_top = f'**→→→ [{_prev_m}{_label}]({_rel}) ←←←**'
            if _idx + 1 < len(_all):
                _next_m = _all[_idx + 1]
                _target = os.path.join(user_dir, _next_m[:4], f'{_next_m}{_suffix}.md')
                _rel = os.path.relpath(_target, os.path.dirname(_filename)).replace('\\', '/')
                _nav_bottom = f'**→→→ [{_next_m}{_label}]({_rel}) ←←←**'
            _final = '\n'.join(_headers[_suffix][_m]) + '\n\n'
            if _nav_top:
                _final += _nav_top + '\n\n'
            _final += _body
            if _nav_bottom:
                _final += '\n' + _nav_bottom + '\n'
            os.makedirs(os.path.dirname(_filename), exist_ok=True)
            _tmp = _filename + '.tmp'
            with open(_tmp, 'w', encoding='utf-8-sig', newline='') as f:
                f.write(_final)
            os.replace(_tmp, _filename)
            stat['type_files'] += 1


def main():
    _parser = argparse.ArgumentParser(description='媒体目录分离迁移脚本 (旧 媒体/+视频封面/ -> 新 图片/+视频/)')
    _parser.add_argument('--save-path', default='', help='保存根目录(默认读 settings.json 的 save_path)')
    _parser.add_argument('--user', default='', help='仅处理指定用户(screen_name), 默认处理全部')
    _args = _parser.parse_args()

    _save_path = _args.save_path.strip() or _read_settings_save_path().rstrip(os.sep)
    if not os.path.isdir(_save_path):
        print(f'保存目录不存在: {_save_path}')
        sys.exit(1)

    _stat = {'moved': 0, 'links_fixed': 0, 'type_files': 0, 'type_files_dropped': 0}
    _users = sorted(_d for _d in os.listdir(_save_path)
                    if os.path.isdir(os.path.join(_save_path, _d)) and not _d.startswith('.'))
    if _args.user:
        _users = [_u for _u in _users if _u == _args.user]
        if not _users:
            print(f'未找到用户目录: {_args.user}')
            sys.exit(1)

    for _u in _users:
        _migrate_user(os.path.join(_save_path, _u), _stat)
        print(f'完成用户: {_u}')

    print(f'\n迁移完成: 移动媒体文件 {_stat["moved"]} 个, 修复 md 链接 {_stat["links_fixed"]} 处, '
          f'生成/更新分文件 md {_stat["type_files"]} 个, 删除空分文件 {_stat["type_files_dropped"]} 个')


if __name__ == '__main__':
    main()