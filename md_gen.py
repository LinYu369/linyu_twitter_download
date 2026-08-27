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

        if md_mode == 'multi':      # 多md模式: 按年份分md文件(用户_年份.md), 新内容插入对应年份文件头部
            self.vol_files = {}         # 年份 -> {'filename','header_lines','old_content'}
            self.vol_buffers = {}       # 年份 -> 本次新增内容缓存(StringIO)
            self.written_ids = set()    # 所有年份md中已写入推文的 status id, 跨运行去重
            _pattern = re.compile(rf'^{re.escape(screen_name)}_(\d{{4}})\.md$')
            for _f in os.listdir(save_path):
                _m = _pattern.match(_f)
                if _m:
                    _p = os.path.join(save_path, _f)
                    with open(_p, 'r', encoding='utf-8-sig') as f:
                        _content = f.read()
                    _lines = _content.split('\n')
                    self.vol_files[_m.group(1)] = {
                        'filename': _p,
                        'header_lines': _lines[:3],
                        'old_content': '\n'.join(_lines[3:]).lstrip('\n'),
                    }
                    self.written_ids |= set(re.findall(r'status/(\d+)', _content))
            self.f = io.StringIO()      # 当前年份缓存, 推文分支时按推文年份切换
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
            self.f.write(self.current_tweet_info[1] + '\n')
        if self.is_append_like:    # 追加式: 本次新增内容统一插入文件头部
            if self.md_mode == 'multi':
                self._flush_to_multi_files()
            else:
                self._flush_to_file()
        else:
            self.f.close()

    def _flush_to_multi_files(self):
        """多md(按年)落盘: 每个年份的新增内容插入对应年份文件头部(最新在上), 文件不存在则新建"""
        _header = f"{self.user_name} {self.screen_name}\nTweet Range: {self.tweet_range}\nSave Path: {self.save_path}\n\n"
        for _year, _buf in self.vol_buffers.items():
            new_content = _buf.getvalue()
            if not new_content.strip():  # 该年份本次无新增, 跳过
                continue
            _info = self.vol_files.get(_year)
            if _info is None:  # 该年份文件不存在: 新建
                _filename = f'{self.save_path}/{self.screen_name}_{_year}.md'
                _tmp = _filename + '.tmp'
                try:
                    with open(_tmp, 'w', encoding='utf-8-sig', newline='') as f:
                        f.write(_header + new_content)
                    os.replace(_tmp, _filename)
                except Exception as e:
                    print(f'更新 md 文件失败: {e}')
                    try:
                        os.remove(_tmp)
                    except OSError:
                        pass
                continue
            # 插入对应年份文件头部
            _final = '\n'.join(_info['header_lines']) + '\n\n' + new_content + (\
                '\n' + _info['old_content'] if _info['old_content'] else '')
            _tmp = _info['filename'] + '.tmp'
            try:
                with open(_tmp, 'w', encoding='utf-8-sig', newline='') as f:
                    f.write(_final)
                os.replace(_tmp, _info['filename'])
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

    def stamp2time(self, msecs_stamp: int) -> str:
        timeArray = time.localtime(msecs_stamp/1000)
        otherStyleTime = time.strftime("%Y-%m-%d %H:%M", timeArray)
        return otherStyleTime

    def media_tweet_input(self, csv_info, prefix) -> None:
        # 链接文字用文件名(去掉年份路径); 链接目标仅编码特殊字符(中文/斜杠保持原样), 避免 Markdown 链接解析失败
        _display_name = os.path.split(csv_info[6])[1].replace('[', '\\[').replace(']', '\\]')
        fixed_filename = _md_quote(csv_info[6])
        fixed_timestr = csv_info[0] if type(
            csv_info[0]) == str else self.stamp2time(csv_info[0])
        currentDate = fixed_timestr[0:7]

        tweet_status_id = re.findall(r"status/(\d+)", csv_info[3])[0]
        # print(tweet_status_id)

        if self.is_append_like and tweet_status_id in self.written_ids and tweet_status_id != self.current_tweet_info[0]:
            return  # 该推文已写入过(且不是当前正在写入的推文), 整条跳过, 避免跨运行/分页重复

        if self.current_tweet_info[0] != tweet_status_id:  # 检测到现在正准备输出新的推文
            if self.is_append_like:
                self.written_ids.add(tweet_status_id)   # 记录本次写入的推文, 供同轮后续媒体/下轮去重
            # 输出上一个推文的互动数据(媒体行已自带换行, 无需前导\n); 此时 self.f 仍是上一个推文所属年份的缓存
            self.f.write(f'{self.current_tweet_info[1]}\n\n' if len(
                self.current_tweet_info[1]) > 0 else '')
            if self.md_mode == 'multi':
                # 按推文年份切换写入缓存 (多md: 一年一个md文件)
                self.f = self.vol_buffers.setdefault(
                    fixed_timestr[:4], io.StringIO())

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
                self.f.write(f'## {currentDate}\n')  # 输出 年月 标题
                self.current_tweet_info[2] = currentDate

            # 转推注释(独立一行, 不进入标题); 后跟的英文单词提供强LTR锚点, 昵称含RTL文本也不会错位
            if 'retweet' in prefix:
                self.f.write(f'*{self.user_name} retweeted*\n')
            # 推文小标题: 时间 · 昵称 [原文](推文链接)(不显示用户名)
            # 时间放昵称前面: 昵称若含RTL文本(阿拉伯语等), 紧跟其后的 [原文] 的汉字是强LTR锚点, 可避免bidi渲染重排错位;
            # 若昵称在前则其后的中性符号(·)和纯数字时间会被RTL化导致显示乱序(已用bidi算法验证)
            self.f.write(
                f'### {fixed_timestr} · {csv_info[1]} [原文]({csv_info[3]})\n')
            # 推文文本信息(每行行尾加两空格实现硬换行, 否则多行会被渲染成同一段)
            if csv_info[7]:
                self.f.write(csv_info[7].replace('\n', '  \n') + '  \n')
            self.current_tweet_info[0] = tweet_status_id
            self.current_tweet_info[1] = f'{csv_info[8]} Likes, {csv_info[9]} Retweets, {csv_info[10]} Replies'

        # 输出当前推文的媒体标签(其中一张)
        # 视频优先用封面图链接([![封面](封面)](视频)), 点击封面打开本地视频; 无封面时回退文本链接
        # 行尾加两个空格硬换行, 避免与互动数据/其他媒体渲染成同一段
        if 'Video' in csv_info[4]:
            if len(csv_info) > 11 and csv_info[11]:
                # 封面存于 年份/视频封面/ 子目录, 链接相对 md 文件所在目录(用户目录); 用 / 分隔避免 Windows 反斜杠
                _cover = _md_quote(
                    os.path.dirname(csv_info[6]) + '/视频封面/' +
                    os.path.splitext(os.path.basename(csv_info[6]))[0] + '.jpg')
                # 封面图上一行单独输出 📹📹 视频名 📹📹 提示这是视频(否则和普通图片无法区分)
                self.f.write(f'📹📹 {os.path.basename(csv_info[6])} 📹📹  \n')
                self.f.write(f'[![{_display_name}]({_cover})]({fixed_filename})  \n')
            else:
                self.f.write(f'📹📹📹📹📹 [{_display_name}]({fixed_filename})  \n')
        else:
            self.f.write(f'[![]({fixed_filename})]({csv_info[5]})  \n')
        self.file_media_count += 1
