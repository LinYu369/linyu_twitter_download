import io
import os
import re
import time
from datetime import datetime


def _md_quote(p: str) -> str:
    """Markdown 链接目标编码: 仅编码空格/括号/方括号/井号等特殊字符, 中文与 / 保持原样(否则编辑器无法解码预览本地文件)"""
    return p.replace(' ', '%20').replace('(', '%28').replace(')', '%29').replace(
        '[', '%5B').replace(']', '%5D').replace('#', '%23')


class md_gen():
    def __init__(self, save_path: str, user_name, screen_name, tweet_range, has_likes, media_count_limit, append_mode=False, md_mode='single', md_tweet_limit=0) -> None:
        self.append_mode = append_mode
        self.md_mode = md_mode
        self.md_tweet_limit = md_tweet_limit  # 多md模式: 每个md文件的最大小标题(推文)数, 0不限制
        # 追加式行为(单md append / 多md): 新内容缓存后统一插入文件头部, 并按 status id 跨运行去重
        self.is_append_like = append_mode or md_mode == 'multi'
        self.save_path = save_path
        self.user_name = user_name
        self.screen_name = screen_name
        self.tweet_range = tweet_range
        self.has_likes = has_likes

        self.media_count_limit = media_count_limit  # 从配置文件中读取到的 单个 Markdown 最大媒体数量。
        # 生成 md 时使用，用于合并多个媒体到一个推文和生成日期标题。0-当前推文的 status id, 1-当前推文互动数据(md文本), 2-当前推文年月日期(不含转推，获取likes时也不使用)
        self.current_tweet_info = ['', '', '']
        self.file_media_count = 0  # 当前文件中的媒体数量
        self.file_count = 1  # 已输出的文件数量
        # 图片/视频分文件流: 仅 multi 模式使用, 与总流并行写入, 非 multi 恒为 None
        self.f_img = None
        self.f_vid = None

        if md_mode == 'multi':      # 多md模式: 按月份分md文件(年份/月份/月份.md), 新内容插入对应月份文件头部; 另有 月份-图片.md / 月份-视频.md 按媒体类型分文件
            self.vol_files = {}         # 月份(YYYY-MM) -> 总md {'filename','header_lines','old_content'}
            self.img_files = {}         # 图片md (-图片.md)
            self.vid_files = {}         # 视频md (-视频.md)
            self.vol_buffers = {}       # 月份 -> 总md 本次新增内容缓存(StringIO)
            self.img_buffers = {}       # 月份 -> 图片md 本次新增内容缓存(StringIO)
            self.vid_buffers = {}       # 月份 -> 视频md 本次新增内容缓存(StringIO)
            self.written_ids = set()    # 所有md中已写入推文的 status id, 跨运行去重
            self.suffixes = ('', '-图片', '-视频')
            _month_re = re.compile(r'^(\d{4}-\d{2})(-图片|-视频)?\.md$')
            _targets = {'': (self.vol_files, self.vol_buffers),
                        '-图片': (self.img_files, self.img_buffers),
                        '-视频': (self.vid_files, self.vid_buffers)}
            # 跨月导航行(兼容新旧样式): 整行匹配, 避免误删以 **→→→ 开头的推文正文
            _nav_re = re.compile(r'^(?:\[→ [^\]]+\]\([^)]+\.md\)|\*\*→→→ \[[^\]]+\]\([^)]+\.md\) ←←←\*\*)$')
            for _root, _dirs, _names in os.walk(save_path):
                for _f in _names:
                    _m = _month_re.match(_f)
                    if not _m:
                        continue
                    _month, _suffix = _m.group(1), _m.group(2) or ''
                    if os.path.basename(_root) != _month[:4]:
                        # 非年份文件夹下的月份md(旧版放在月份文件夹内)仅参与去重, 保持原样不再更新
                        with open(os.path.join(_root, _f), 'r', encoding='utf-8-sig') as f:
                            self.written_ids |= set(re.findall(r'status/(\d+)', f.read()))
                        continue
                    _p = os.path.join(_root, _f)
                    with open(_p, 'r', encoding='utf-8-sig') as f:
                        _content = f.read()
                    _lines = _content.split('\n')
                    while _lines and not _lines[-1].strip():
                        _lines.pop()    # 去掉文件末尾空行, 便于识别底部导航行
                    if len(_lines) > 3 and _nav_re.match(_lines[-1].strip()):
                        _lines.pop()    # 剔除末尾的底部跨月导航行
                    _start = 3  # 跳过 header 后的空行与顶部跨月导航行, 正文从第一个内容行开始
                    while _start < len(_lines) and (not _lines[_start].strip() or _nav_re.match(_lines[_start].strip())):
                        _start += 1
                    _files, _buffers = _targets[_suffix]
                    _files[_month] = {
                        'filename': _p,
                        'header_lines': _lines[:3],
                        'old_content': '\n'.join(_lines[_start:]).lstrip('\n'),
                    }
                    self.written_ids |= set(re.findall(r'status/(\d+)', _content))
            # 旧按年结构(用户_年份.md)仅参与去重, 保持原样不再更新
            _pattern = re.compile(rf'^{re.escape(screen_name)}_(\d{{4}})\.md$')
            for _f in os.listdir(save_path):
                if _pattern.match(_f):
                    with open(os.path.join(save_path, _f), 'r', encoding='utf-8-sig') as f:
                        self.written_ids |= set(re.findall(r'status/(\d+)', f.read()))
            self.f = io.StringIO()      # 当前月份缓存, 推文分支时按推文月份切换
        elif append_mode:     # 追加模式: 写入固定文件, 本次新增内容在 md_close 时统一插入文件头部(最新在上, 与首次生成的格式一致)
            self.filename = f'{save_path}/{screen_name}.md'
            self.is_new_file = not os.path.exists(
                self.filename) or os.path.getsize(self.filename) == 0
            self.header_lines = None    # 已有文件的头部元信息(前3行)
            self.old_content = ''       # 已有文件的正文(不含头部)
            self.written_ids = set()    # 已写入推文的 status id, 跨运行去重
            if not self.is_new_file:
                with open(self.filename, 'r', encoding='utf-8-sig') as f:
                    _content = f.read()
                _lines = _content.split('\n')
                self.header_lines = _lines[:3]
                self.old_content = '\n'.join(_lines[3:]).lstrip('\n')
                # 解析已写入的推文 id, 新抓取时跳过, 避免重复写入
                self.written_ids = set(re.findall(r'status/(\d+)', _content))
                # 注意: 不初始化 current_tweet_info[2], 保证本次新增块自带 `## YYYY-MM` 日期标题
            self.f = io.StringIO()      # 本次新增内容先缓存到内存, 最后统一插入文件头部
        else:
            self.f = open(f'{save_path}/{screen_name}-{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}_1.md',
                          'w', encoding='utf-8-sig', newline='')
            self.f.write(f"{user_name} {screen_name}\n")
            self.f.write(f"Tweet Range: {tweet_range}\n")
            self.f.write(f"Save Path: {save_path}\n")

    def md_close(self):
        if len(self.current_tweet_info[1]) > 0:  # 本次运行未输出推文时不再写入互动数据
            # 输出最后一个推文的互动数据(媒体行已自带换行, 无需前导\n)
            for _s in self._streams():
                _s.write(self.current_tweet_info[1] + '\n')
        if self.is_append_like:    # 追加式: 本次新增内容统一插入文件头部
            if self.md_mode == 'multi':
                self._flush_to_multi_files()
            else:
                self._flush_to_file()
        else:
            self.f.close()

    def _flush_to_multi_files(self):
        """多md(按月)落盘: 对 总md/图片md/视频md 三个系列, 每个月份的新增内容插入对应文件头部(最新在上), 文件底部写跨月导航链接, 文件不存在则新建"""
        self._flush_series('', self.vol_files, self.vol_buffers)
        self._flush_series('-图片', self.img_files, self.img_buffers)
        self._flush_series('-视频', self.vid_files, self.vid_buffers)

    def _flush_series(self, suffix, _files, _buffers):
        """单个 md 系列(总/图片/视频)落盘: 月份集合=磁盘已有文件+本次新增缓存, 相邻月份之间生成"下一月"导航链接; 出现新月份时全量刷新"""
        _header = f"{self.user_name} {self.screen_name}\nTweet Range: {self.tweet_range}\nSave Path: {self.save_path}\n\n"
        # 全部月份(已有文件+本次新增)按时间排序, 相邻月份之间生成"下一月"导航链接
        _all_months = sorted(set(_files.keys()) | set(_buffers.keys()))
        # 出现新月份时各文件的"下一月"链接目标可能变化, 需刷新该系列所有文件; 否则仅重写有新增内容的文件
        _has_new_month = bool(set(_buffers.keys()) - set(_files.keys()))
        for _idx, _month in enumerate(_all_months):
            _buf = _buffers.get(_month)
            _info = _files.get(_month)
            new_content = _buf.getvalue() if _buf else ''
            if _info is None:
                if not new_content.strip():  # 该月份不存在且本次无新增, 跳过
                    continue
                _year_dir = os.path.join(self.save_path, _month[:4])
                os.makedirs(_year_dir, exist_ok=True)
                _filename = os.path.join(_year_dir, f'{_month}{suffix}.md')   # md 位于年份文件夹内
            else:
                if not new_content.strip() and not _has_new_month:
                    continue    # 无新增且无新月份出现, 导航链接不变, 无需重写
                _filename = _info['filename']
            # 跨月导航行: 顶部指向上一月(更早), 底部指向下一月(更晚); 跨年用相对路径 ../年份/月份.md; 无相邻月份则不写
            # 图片/视频系列链接文字带类型后缀(如 2026-09 图片), 与总 md 的纯月份文字区分
            _nav_top, _nav_bottom = '', ''
            _label = ' 图片' if suffix == '-图片' else ' 视频' if suffix == '-视频' else ''
            if _idx > 0:
                _prev_m = _all_months[_idx - 1]
                _target = os.path.join(self.save_path, _prev_m[:4], f'{_prev_m}{suffix}.md')
                _rel = os.path.relpath(_target, os.path.dirname(_filename)).replace('\\', '/')
                _nav_top = f'**→→→ [{_prev_m}{_label}]({_md_quote(_rel)}) ←←←**'
            if _idx + 1 < len(_all_months):
                _next_m = _all_months[_idx + 1]
                _target = os.path.join(self.save_path, _next_m[:4], f'{_next_m}{suffix}.md')
                _rel = os.path.relpath(_target, os.path.dirname(_filename)).replace('\\', '/')
                _nav_bottom = f'**→→→ [{_next_m}{_label}]({_md_quote(_rel)}) ←←←**'
            _top = f'{_nav_top}\n\n' if _nav_top else ''      # 文件开头(header 后)的导航
            _bottom = f'\n{_nav_bottom}\n' if _nav_bottom else ''   # 文件末尾的导航
            if _info is None:  # 该月份文件不存在: 新建
                _final = _header + _top + new_content + _bottom
            else:
                _final = '\n'.join(_info['header_lines']) + '\n\n' + _top + new_content + (\
                    '\n' + _info['old_content'].rstrip('\n') if _info['old_content'].strip() else '') + _bottom
            _tmp = _filename + '.tmp'
            try:
                with open(_tmp, 'w', encoding='utf-8-sig', newline='') as f:
                    f.write(_final)
                os.replace(_tmp, _filename)
            except Exception as e:
                print(f'更新 md 文件失败: {e}')
                try:
                    os.remove(_tmp)
                except OSError:
                    pass

    def _flush_to_file(self):
        new_content = self.f.getvalue()
        if self.is_new_file:
            _final = f"{self.user_name} {self.screen_name}\nTweet Range: {self.tweet_range}\nSave Path: {self.save_path}\n\n" + new_content
        else:
            if not new_content.strip():  # 本次无新增推文, 不改动已有文件
                return
            _header = '\n'.join(self.header_lines) + '\n\n'
            # new_content 末尾已带换行, 再补一个空行分隔新旧内容块
            _final = _header + new_content + ('\n' + self.old_content if self.old_content else '')
        # 先写临时文件再原子替换, 避免中途失败损坏原文件
        _tmp = self.filename + '.tmp'
        try:
            with open(_tmp, 'w', encoding='utf-8-sig', newline='') as f:
                f.write(_final)
            os.replace(_tmp, self.filename)
        except Exception as e:
            print(f'更新 md 文件失败: {e}')
            try:
                os.remove(_tmp)
            except OSError:
                pass

    def _streams(self):
        """当前推文月份的全部写入流(总md + 图片md + 视频md); 非 multi 模式只有总流"""
        _streams = [self.f]
        if self.f_img is not None:
            _streams.append(self.f_img)
        if self.f_vid is not None:
            _streams.append(self.f_vid)
        return _streams

    def stamp2time(self, msecs_stamp: int) -> str:
        timeArray = time.localtime(msecs_stamp/1000)
        otherStyleTime = time.strftime("%Y-%m-%d %H:%M", timeArray)
        return otherStyleTime

    def media_tweet_input(self, csv_info, prefix) -> None:
        fixed_timestr = csv_info[0] if type(
            csv_info[0]) == str else self.stamp2time(csv_info[0])
        # 链接文字用文件名(去掉路径); 链接目标仅编码特殊字符(中文/斜杠保持原样), 避免 Markdown 链接解析失败
        _display_name = os.path.split(csv_info[6])[1].replace('[', '\\[').replace(']', '\\]')
        _is_video = 'Video' in csv_info[4]
        # 多md按月: md 位于 年份/ 目录, 媒体在 年份/月份/图片|视频 子目录, 链接相对 md 所在目录(年份目录); 单md: 链接相对用户根目录
        if self.md_mode == 'multi':
            _media_link = fixed_timestr[:7] + ('/视频/' if _is_video else '/图片/') + os.path.split(csv_info[6])[1]
        else:
            _media_link = csv_info[6]
        fixed_filename = _md_quote(_media_link)
        currentDate = fixed_timestr[0:7]

        tweet_status_id = re.findall(r"status/(\d+)", csv_info[3])[0]
        # print(tweet_status_id)

        if self.is_append_like and tweet_status_id in self.written_ids and tweet_status_id != self.current_tweet_info[0]:
            return  # 该推文已写入过(且不是当前正在写入的推文), 整条跳过, 避免跨运行/分页重复

        if self.current_tweet_info[0] != tweet_status_id:  # 检测到现在正准备输出新的推文
            if self.is_append_like:
                self.written_ids.add(tweet_status_id)   # 记录本次写入的推文, 供同轮后续媒体/下轮去重
            # 输出上一个推文的互动数据(媒体行已自带换行, 无需前导\n); 此时各流仍是上一个推文所属月份的缓存
            _prev_stats = f'{self.current_tweet_info[1]}\n\n' if len(
                self.current_tweet_info[1]) > 0 else ''
            for _s in self._streams():
                _s.write(_prev_stats)
            if self.md_mode == 'multi':
                # 按推文月份切换三系列写入缓存 (多md: 一月一组 总/图片/视频 md)
                self.f = self.vol_buffers.setdefault(
                    fixed_timestr[:7], io.StringIO())
                self.f_img = self.img_buffers.setdefault(
                    fixed_timestr[:7], io.StringIO())
                self.f_vid = self.vid_buffers.setdefault(
                    fixed_timestr[:7], io.StringIO())

            # 超出媒体限制，新建文件 (仅旧的非追加模式)
            if not self.is_append_like and self.media_count_limit > 0 and self.file_media_count >= self.media_count_limit:
                self.f.close()
                self.file_media_count = 0
                self.file_count += 1
                if self.has_likes:
                    new_filename = f'{self.save_path}/{self.screen_name}-{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}_{self.file_count}.md'
                elif 'retweet' in prefix:
                    new_filename = f'{self.save_path}/{self.screen_name}-{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}_{self.file_count}_{self.current_tweet_info[2]}.md'
                else:
                    new_filename = f'{self.save_path}/{self.screen_name}-{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}_{self.file_count}_{currentDate}.md'
                self.f = open(new_filename, 'w',
                              encoding='utf-8-sig', newline='')
                self.f.write(f"{self.user_name} {self.screen_name}\n")
                self.f.write(f"Tweet Range: {self.tweet_range}\n")
                self.f.write(f"Save Path: {self.save_path}\n\n")

            if not self.has_likes and 'retweet' not in prefix and currentDate != self.current_tweet_info[2]:
                for _s in self._streams():
                    _s.write(f'## {currentDate}\n')  # 输出 年月 标题
                self.current_tweet_info[2] = currentDate

            # 转推注释(独立一行, 不进入标题); 后跟的英文单词提供强LTR锚点, 昵称含RTL文本也不会错位
            if 'retweet' in prefix:
                for _s in self._streams():
                    _s.write(f'*{self.user_name} retweeted*\n')
            # 推文小标题: 时间 · 昵称 [原文](推文链接)(不显示用户名)
            # 时间放昵称前面: 昵称若含RTL文本(阿拉伯语等), 紧跟其后的 [原文] 的汉字是强LTR锚点, 可避免bidi渲染重排错位;
            # 若昵称在前则其后的中性符号(·)和纯数字时间会被RTL化导致显示乱序(已用bidi算法验证)
            for _s in self._streams():
                _s.write(
                    f'### {fixed_timestr} · {csv_info[1]} [原文]({csv_info[3]})\n')
            # 推文文本信息(每行行尾加两空格实现硬换行, 否则多行会被渲染成同一段)
            if csv_info[7]:
                for _s in self._streams():
                    _s.write(csv_info[7].replace('\n', '  \n') + '  \n')
            self.current_tweet_info[0] = tweet_status_id
            self.current_tweet_info[1] = f'{csv_info[8]} Likes, {csv_info[9]} Retweets, {csv_info[10]} Replies'

        # 输出当前推文的媒体标签(其中一张)
        # 视频优先用封面图链接([![封面](封面)](视频)), 点击封面打开本地视频; 无封面时回退文本链接
        # 行尾加两个空格硬换行, 避免与互动数据/其他媒体渲染成同一段; 图片行写 总md+图片md, 视频行写 总md+视频md
        if 'Video' in csv_info[4]:
            if len(csv_info) > 11 and csv_info[11]:
                # 封面与视频同存于 月份/视频/ 目录(同名不同扩展名); 多md相对 md 所在目录(年份目录), 单md相对用户根目录; 用 / 分隔避免 Windows 反斜杠
                _cover = _md_quote(
                    (fixed_timestr[:7] + '/视频/' if self.md_mode == 'multi' else os.path.dirname(csv_info[6]) + '/') +
                    os.path.splitext(os.path.basename(csv_info[6]))[0] + '.jpg')
                # 封面图上一行单独输出 📹📹 视频名 📹📹 提示这是视频(否则和普通图片无法区分)
                self.f.write(f'📹📹 {os.path.basename(csv_info[6])} 📹📹  \n')
                self.f.write(f'[![{_display_name}]({_cover})]({fixed_filename})  \n')
                if self.f_vid is not None:
                    self.f_vid.write(f'📹📹 {os.path.basename(csv_info[6])} 📹📹  \n')
                    self.f_vid.write(f'[![{_display_name}]({_cover})]({fixed_filename})  \n')
            else:
                self.f.write(f'📹📹📹📹📹 [{_display_name}]({fixed_filename})  \n')
                if self.f_vid is not None:
                    self.f_vid.write(f'📹📹📹📹📹 [{_display_name}]({fixed_filename})  \n')
        else:
            self.f.write(f'[![]({fixed_filename})]({fixed_filename})  \n')
            if self.f_img is not None:
                self.f_img.write(f'[![]({fixed_filename})]({fixed_filename})  \n')
        self.file_media_count += 1
