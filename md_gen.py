import io
import os
import re
import time
import urllib.parse
from datetime import datetime


class md_gen():
    def __init__(self, save_path: str, user_name, screen_name, tweet_range, has_likes, media_count_limit, append_mode=False) -> None:
        self.append_mode = append_mode
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

        if append_mode:     # 追加模式: 写入固定文件, 本次新增内容在 md_close 时统一插入文件头部(最新在上, 与首次生成的格式一致)
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
            # 输出最后一个推文的互动数据
            self.f.write('\n' + self.current_tweet_info[1] + '\n')
        if self.append_mode:    # 追加模式: 本次新增内容统一插入文件头部
            self._flush_to_file()
        else:
            self.f.close()

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
        # 链接文字用原始文件名; 链接目标做百分号编码(空格/括号等), 避免 Markdown 链接解析失败
        _display_name = csv_info[6].replace('[', '\\[').replace(']', '\\]')
        fixed_filename = urllib.parse.quote(csv_info[6])
        fixed_timestr = csv_info[0] if type(
            csv_info[0]) == str else self.stamp2time(csv_info[0])
        currentDate = fixed_timestr[0:7]

        tweet_status_id = re.findall(r"status/(\d+)", csv_info[3])[0]
        # print(tweet_status_id)

        if self.append_mode and tweet_status_id in self.written_ids:
            return  # 该推文已写入过, 其全部媒体一并跳过, 避免跨运行重复

        if self.current_tweet_info[0] != tweet_status_id:  # 检测到现在正准备输出新的推文
            if self.append_mode:
                self.written_ids.add(tweet_status_id)   # 记录本次写入的推文, 供同轮后续媒体/下轮去重
            self.f.write(f'\n{self.current_tweet_info[1]}\n\n' if len(
                self.current_tweet_info[1]) > 0 else '')  # 输出上一个推文的互动数据

            # 超出媒体限制，新建文件 (追加模式下不限制)
            if not self.append_mode and self.media_count_limit > 0 and self.file_media_count >= self.media_count_limit:
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

            # 转推注释(独立一行, 不进入标题)
            if 'retweet' in prefix:
                self.f.write(f'*{self.user_name} retweeted*\n')
            # 推文小标题: 用户名与昵称 · 时间 [src](推文链接)
            self.f.write(
                f'### {csv_info[1]} {csv_info[2]} · {fixed_timestr} [src]({csv_info[3]})\n')
            self.f.write(csv_info[7] + '\n')  # 推文文本信息
            self.current_tweet_info[0] = tweet_status_id
            self.current_tweet_info[1] = f'{csv_info[8]} Likes, {csv_info[9]} Retweets, {csv_info[10]} Replies'

        # 输出当前推文的媒体标签(其中一张)
        # 视频优先用封面图链接([![封面](封面)](视频)), 点击封面打开本地视频; 无封面时回退文本链接
        if 'Video' in csv_info[4]:
            if len(csv_info) > 11 and csv_info[11]:
                _cover = urllib.parse.quote(
                    os.path.splitext(csv_info[6])[0] + '.jpg')
                self.f.write(f'[![{_display_name}](视频封面/{_cover})]({fixed_filename})')
            else:
                self.f.write(f'📹 [{_display_name}]({fixed_filename})')
        else:
            self.f.write(f'[![]({fixed_filename})]({csv_info[5]})')
        self.file_media_count += 1
