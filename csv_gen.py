import csv
import os
import time
from datetime import datetime


class csv_gen():
    def __init__(self, save_path: str, user_name, screen_name, tweet_range, append_mode=False) -> None:
        if append_mode:     # 追加模式: 写入固定文件, 新内容接在已有内容之后
            _filename = f'{save_path}/{screen_name}.csv'
            is_new_file = not os.path.exists(
                _filename) or os.path.getsize(_filename) == 0
            self.f = open(_filename, 'a', encoding='utf-8-sig', newline='')
        else:
            self.f = open(f'{save_path}/{screen_name}-{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}.csv',
                          'w', encoding='utf-8-sig', newline='')
        self.writer = csv.writer(self.f)

        if not append_mode or is_new_file:      # 仅在新建文件时写入表头, 否则会插入到数据中间
            # 初始化
            self.writer.writerow([user_name, screen_name])
            self.writer.writerow(['Tweet Range : ' + tweet_range])
            self.writer.writerow(['Save Path : ' + save_path])
            main_par = ['Tweet Date', 'Display Name', 'User Name', 'Tweet URL', 'Media Type', 'Media URL', 'Saved Filename', 'Tweet Content', 'Favorite Count',
                        'Retweet Count', 'Reply Count']
            self.writer.writerow(main_par)

        pass

    def csv_close(self):
        self.f.close()

    def stamp2time(self, msecs_stamp: int) -> str:
        timeArray = time.localtime(msecs_stamp/1000)
        otherStyleTime = time.strftime("%Y-%m-%d %H:%M", timeArray)
        return otherStyleTime

    def data_input(self, main_par_info: list) -> None:  # 数据格式参见 main_par
        main_par_info[0] = self.stamp2time(
            main_par_info[0])  # 传进来的是 int 时间戳, 故转换一下
        self.writer.writerow(main_par_info[:11])  # 仅写前11列(封面URL仅供md使用, 不入csv)
