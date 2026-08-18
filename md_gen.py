import os
import time
import re
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

        if append_mode:     # 追加模式: 写入固定文件, 新内容接在已有内容之后
            _filename = f'{save_path}/{screen_name}.md'
            is_new_file = not os.path.exists(
                _filename) or os.path.getsize(_filename) == 0
            self.f = open(_filename, 'a', encoding='utf-8-sig', newline='')
            if is_new_file:
                self.f.write(f"{user_name} {screen_name}\n")
                self.f.write(f"Tweet Range: {tweet_range}\n")
                self.f.write(f"Save Path: {save_path}\n\n")
            else:
                with open(_filename, 'r', encoding='utf-8-sig') as f:
                    _content = f.read()
                _dates = re.findall(r'## (\d{4}-\d{2})', _content)
                if _dates:      # 初始化年月标题状态, 避免跨运行重复输出 `## YYYY-MM`
                    self.current_tweet_info[2] = _dates[-1]
                if not _content.endswith('\n\n'):   # 上次运行未以空行结尾时先补一个空行
                    self.f.write('\n')
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
        self.f.close()

    def stamp2time(self, msecs_stamp: int) -> str:
        timeArray = time.localtime(msecs_stamp/1000)
        otherStyleTime = time.strftime("%Y-%m-%d %H:%M", timeArray)
        return otherStyleTime

    def media_tweet_input(self, csv_info, prefix) -> None:
        fixed_filename = csv_info[6].replace(' ', '%20')
        fixed_timestr = csv_info[0] if type(
            csv_info[0]) == str else self.stamp2time(csv_info[0])
        currentDate = fixed_timestr[0:7]

        tweet_status_id = re.findall(r"status/(\d+)", csv_info[3])[0]
        # print(tweet_status_id)

        if self.current_tweet_info[0] != tweet_status_id:  # 检测到现在正准备输出新的推文
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

            # 转推注释
            prefix_retweet = f'*{self.user_name} retweeted*\n' if 'retweet' in prefix else ''
            self.f.write(
                # 推文用户名与昵称
                f'{prefix_retweet}{csv_info[1]} {csv_info[2]} · {fixed_timestr} [src]({csv_info[3]})\n')
            self.f.write(csv_info[7] + '\n')  # 推文文本信息
            self.current_tweet_info[0] = tweet_status_id
            self.current_tweet_info[1] = f'{csv_info[8]} Likes, {csv_info[9]} Retweets, {csv_info[10]} Replies'

        # 输出当前推文的媒体标签(其中一张)
        self.f.write(
            f'<video src="{fixed_filename}" controls></video>' if 'Video' in csv_info[4] else f'[![]({fixed_filename})]({csv_info[5]})')
        self.file_media_count += 1
