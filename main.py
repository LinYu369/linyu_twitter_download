from url_utils import quote_url
from cache_gen import cache_gen
from md_gen import md_gen
from csv_gen import csv_gen
from user_info import User_info
import re
import time
from datetime import datetime, timedelta
import httpx
import asyncio
import os
import json
import sys
import signal

sys.path.append('.')

# 单实例锁: 防止多个进程同时运行导致日志/下载文件交错 (Linux 容器内生效, Windows 本地跳过)
try:
    import fcntl
    _lock_f = open(os.path.join(os.getcwd(), 'main.lock'), 'w')
    try:
        fcntl.flock(_lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('检测到已有 main.py 实例在运行, 本实例自动退出 (请先停止旧容器/进程)')
        sys.exit(0)
except ImportError:
    pass    # Windows 本地运行不启用单实例锁

# 日志输出包装: 每行自动加时间戳前缀, 并按整行原子写入 stdout
# (print() 默认分两次写入正文+换行, 整行写入可避免 Docker 日志驱动产生 partial 片段)
_MAX_LINE = 8000


def _clip_line(line):
    if len(line) > _MAX_LINE:
        return line[:_MAX_LINE] + f'...(该行共{len(line)}字符,超长已截断)'
    return line


class _LogWriter:
    def __init__(self, stream):
        self.stream = stream
        self._buf = ''

    def write(self, data):
        data = self._buf + data
        self._buf = ''
        while '\n' in data:
            line, data = data.split('\n', 1)
            if line.strip():    # 非空行加时间戳前缀
                line = f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] {line}'
            line = _clip_line(line) + '\n'
            self.stream.write(line)
            self.stream.flush()   # 每次写入立即刷新, 保证日志实时可见
        if data:
            self._buf = data    # 未带换行的尾巴, 等凑成完整行再写

    def flush(self):
        if self._buf:   # 刷出残留的不完整行
            self.stream.write(_clip_line(self._buf))
            self._buf = ''
        self.stream.flush()


sys.stdout = _LogWriter(sys.stdout)
sys.stderr = sys.stdout   # 合并到单流, 避免 docker logs 中 stdout/stderr 混排


def del_special_char(string):
    string = re.sub(
        r'[^\u4e00-\u9fa5\u0030-\u0039\u0041-\u005a\u0061-\u007a\u3040-\u31FF\.]', '', string)
    return string


def stamp2time(msecs_stamp: int) -> str:
    timeArray = time.localtime(msecs_stamp/1000)
    otherStyleTime = time.strftime("%Y-%m-%d %H-%M", timeArray)
    return otherStyleTime


def time2stamp(timestr: str) -> int:
    datetime_obj = datetime.strptime(timestr, "%Y-%m-%d")
    msecs_stamp = int(time.mktime(datetime_obj.timetuple())
                      * 1000.0 + datetime_obj.microsecond / 1000.0)
    return msecs_stamp


def time_comparison(now, start, end):
    start_label = True
    start_down = False
    # twitter : latest -> old
    if now >= start and now <= end:  # 符合时间条件，下载
        start_down = True
    elif now < start:  # 超出时间范围，结束
        start_label = False
    return [start_down, start_label]


# 读取配置
log_output = False
has_retweet = False
has_highlights = False
has_likes = False
has_video = False
csv_file = None
cache_data = None
down_log = False
autoSync = False

md_file = None
md_output = True
media_count_limit = 0
md_tweet_limit = 0  # 多md按年份分卷后不再按条数限制, 保留参数兼容旧调用
append_mode = True

start_time_stamp = 655028357000  # 1990-10-04
end_time_stamp = 2548484357000  # 2050-10-04
start_label = True
First_Page = True  # 首页提取内容时特殊处理

with open('settings.json', 'r', encoding='utf8') as f:
    settings = json.load(f)
    if not settings['save_path']:
        # 容器内默认保存到 /download (宿主机通过 docker-compose 卷映射); 本地直接运行则存当前目录
        settings['save_path'] = '/download' if os.path.isdir(
            '/download') else os.getcwd()
    settings['save_path'] += os.sep
    if settings['has_retweet']:
        has_retweet = True
    if settings['high_lights']:
        has_highlights = True
        has_retweet = False
    if settings['time_range']:
        time_range = True
        start_time, end_time = settings['time_range'].split(':')
        start_time_stamp, end_time_stamp = time2stamp(
            start_time), time2stamp(end_time)
    if settings['autoSync']:
        autoSync = True
    if settings['down_log']:
        down_log = True
    if settings['likes']:  # likes的逻辑和retweet大致相同
        has_retweet = True
        has_likes = True
        has_highlights = False
        start_time_stamp = 655028357000  # 1990-10-04
        end_time_stamp = 2548484357000  # 2050-10-04
    if settings['has_video']:
        has_video = True
    if settings['log_output']:
        log_output = True
    if settings['max_concurrent_requests']:
        max_concurrent_requests = settings['max_concurrent_requests']
    else:
        max_concurrent_requests = 8
###### proxy ######
    if settings['proxy']:
        proxies = settings['proxy']
    else:
        proxies = None

############
    if settings['image_format'] == 'orig':
        orig_format = True
        img_format = 'jpg'
    else:
        orig_format = False
        img_format = settings['image_format']

    if not settings['md_output']:
        md_output = False

    # 追加模式: csv/md 写入固定文件并追加新内容, 不再每次生成新文件 (默认开启)
    append_mode = settings.get('append_mode', True)
    # md 写入模式: single-只写一个md文件 | multi-写多个md文件(按年份分, 一年一个 用户_年份.md)
    md_mode = settings.get('md_mode', 'single')
    # 定时运行: 每天固定时刻(HH:MM)自动运行, 留空则运行一次后退出
    schedule_time = settings.get('schedule_time', '').strip()
    if schedule_time:
        try:
            datetime.strptime(schedule_time, '%H:%M')
        except ValueError:
            print(
                f'schedule_time 格式错误: {schedule_time!r}, 应为 24小时制 HH:MM (如 03:00)')
            sys.exit(1)

    f.close()

backup_stamp = start_time_stamp

_headers = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
}
_headers['cookie'] = settings['cookie']

request_count = 0  # 请求次数计数
down_count = 0  # 下载图片数计数


def get_other_info(_user_info):
    url = 'https://twitter.com/i/api/graphql/xc8f1g7BYqr6VTzTbvNlGw/UserByScreenName?variables={"screen_name":"' + _user_info.screen_name + \
        '","withSafetyModeUserFields":false}&features={"hidden_profile_likes_enabled":false,"hidden_profile_subscriptions_enabled":false,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"subscriptions_verification_info_verified_since_enabled":true,"highlights_tweets_tab_ui_enabled":true,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"responsive_web_graphql_timeline_navigation_enabled":true}&fieldToggles={"withAuxiliaryUserLabels":false}'
    try:
        global request_count
        response = httpx.get(
            quote_url(url), headers=_headers, proxy=proxies).text
        request_count += 1
        raw_data = json.loads(response)
        _user_info.rest_id = raw_data['data']['user']['result']['rest_id']
        _user_info.name = raw_data['data']['user']['result']['legacy']['name']
        _user_info.statuses_count = raw_data['data']['user']['result']['legacy']['statuses_count']
        _user_info.media_count = raw_data['data']['user']['result']['legacy']['media_count']
    except Exception as e:
        print(f'获取信息失败: {_user_info.screen_name}')   # 输出用户填写的用户名, 便于定位是哪个用户
        try:    # 尝试解析错误响应中的具体原因 (账号被冻结/不存在等)
            _result = raw_data['data']['user']['result']
            _msg = _result.get('message') or _result.get('reason')
            if _msg:
                print(f'原因: {_msg}')
        except Exception:
            pass
        print(e)
        print(response)
        return False
    return True


def print_info(_user_info):
    print(
        f'''
        <======基本信息=====>
        昵称:{_user_info.name.encode('utf-8', errors='replace').decode('utf-8')}
        用户名:{_user_info.screen_name}
        数字ID:{_user_info.rest_id}
        总推数(含转推):{_user_info.statuses_count}
        含图片/视频/音频推数(不含转推):{_user_info.media_count}
        <==================>
        开始爬取...
        '''
    )


def get_download_url(_user_info):

    def get_heighest_video_quality(variants) -> str:  # 找到最高质量的视频地址,并返回

        if len(variants) == 1:  # gif适配
            return variants[0]['url']

        max_bitrate = 0
        heighest_url = None
        for i in variants:
            if 'bitrate' in i:
                if int(i['bitrate']) > max_bitrate:
                    max_bitrate = int(i['bitrate'])
                    heighest_url = i['url']
        return heighest_url

    def get_url_from_content(content):
        global start_label
        _photo_lst = []
        if has_retweet or has_highlights:
            x_label = 'content'
        else:
            x_label = 'item'
        for i in content:
            try:
                if 'promoted-tweet' in i['entryId']:  # 排除广告
                    continue
                if 'tweet' in i['entryId']:  # 正常推文
                    if 'tweet' in i[x_label]['itemContent']['tweet_results']['result']:
                        # 适配限制回复账号
                        a = i[x_label]['itemContent']['tweet_results']['result']['tweet']['legacy']
                        frr = [a['favorite_count'],
                               a['retweet_count'], a['reply_count']]
                        tweet_msecs = int(i[x_label]['itemContent']['tweet_results']['result']
                                          ['tweet']['edit_control']['editable_until_msecs']) - 3600000
                    else:
                        a = i[x_label]['itemContent']['tweet_results']['result']['legacy']
                        frr = [a['favorite_count'],
                               a['retweet_count'], a['reply_count']]
                        tweet_msecs = int(i[x_label]['itemContent']['tweet_results']
                                          ['result']['edit_control']['editable_until_msecs']) - 3600000
                    timestr = stamp2time(tweet_msecs)

                    # 我知道这边代码很烂
                    # 但我实在不想重构 ( º﹃º )

                    _result = time_comparison(
                        tweet_msecs, start_time_stamp, end_time_stamp)
                    if _result[0]:  # 符合时间限制
                        if 'retweeted_status_result' not in a:  # 判断是否为转推,以及是否获取转推
                            name = _user_info.name
                            screen_name = _user_info.screen_name
                            if has_likes:
                                a2 = i[x_label]['itemContent']['tweet_results']['result']['core']['user_results']['result']['legacy']
                                name = a2['name']
                                screen_name = a2['screen_name']
                            if 'extended_entities' in a:
                                _photo_lst += [(get_heighest_video_quality(_media['video_info']['variants']), f'{timestr}-vid', [tweet_msecs, name, f'@{screen_name}', _media['expanded_url'], 'Video', get_heighest_video_quality(_media['video_info']['variants']), '', a['full_text']] + frr + [(_media['video_info'].get('poster') or _media.get('media_url_https', ''))]) if 'video_info' in _media and has_video else (
                                    _media['media_url_https'], f'{timestr}-img', [tweet_msecs, name, f'@{screen_name}', _media['expanded_url'], 'Image', _media['media_url_https'], '', a['full_text']] + frr + ['']) for _media in a['extended_entities']['media']]

                        elif has_retweet:
                            name = a['retweeted_status_result']['result']['core']['user_results']['result']['legacy']['name']
                            screen_name = a['retweeted_status_result']['result']['core']['user_results']['result']['legacy']['screen_name']
                            full_text = a['retweeted_status_result']['result']['legacy']['full_text']
                            id_str = a['retweeted_status_result']['result']['legacy']['id_str']

                            if 'extended_entities' in a['retweeted_status_result']['result']['legacy'] and screen_name != _user_info.screen_name:
                                _photo_lst += [(get_heighest_video_quality(_media['video_info']['variants']), f'{timestr}-vid-retweet', [tweet_msecs, name, f"@{screen_name}", _media['expanded_url'], 'Video', get_heighest_video_quality(_media['video_info']['variants']), '', full_text] + frr + [(_media['video_info'].get('poster') or _media.get('media_url_https', ''))]) if 'video_info' in _media and has_video else (
                                    _media['media_url_https'], f'{timestr}-img-retweet', [tweet_msecs, name, f"@{screen_name}", _media['expanded_url'], 'Image', _media['media_url_https'], '', full_text] + frr + ['']) for _media in a['retweeted_status_result']['result']['legacy']['extended_entities']['media']]

                    elif not _result[1]:  # 已超出目标时间范围
                        start_label = False
                        break

                elif 'profile-conversation' in i['entryId']:  # 回复的推文(对话线索)
                    if 'tweet' in i[x_label]['items'][0]['item']['itemContent']['tweet_results']['result']:
                        a = i[x_label]['items'][0]['item']['itemContent']['tweet_results']['result']['tweet']['legacy']
                        frr = [a['favorite_count'],
                               a['retweet_count'], a['reply_count']]
                        tweet_msecs = int(i[x_label]['items'][0]['item']['itemContent']['tweet_results']
                                          ['result']['tweet']['edit_control']['editable_until_msecs']) - 3600000
                    else:
                        a = i[x_label]['items'][0]['item']['itemContent']['tweet_results']['result']['legacy']
                        frr = [a['favorite_count'],
                               a['retweet_count'], a['reply_count']]
                        tweet_msecs = int(i[x_label]['items'][0]['item']['itemContent']['tweet_results']
                                          ['result']['edit_control']['editable_until_msecs']) - 3600000
                    timestr = stamp2time(tweet_msecs)

                    _result = time_comparison(
                        tweet_msecs, start_time_stamp, end_time_stamp)
                    if _result[0]:  # 符合时间限制
                        if 'extended_entities' in a:
                            _photo_lst += [(get_heighest_video_quality(_media['video_info']['variants']), f'{timestr}-vid', [tweet_msecs, _user_info.name, f'@{_user_info.screen_name}', _media['expanded_url'], 'Video', get_heighest_video_quality(_media['video_info']['variants']), '', a['full_text']] + frr + [(_media['video_info'].get('poster') or _media.get('media_url_https', ''))]) if 'video_info' in _media and has_video else (
                                _media['media_url_https'], f'{timestr}-img', [tweet_msecs, _user_info.name, f'@{_user_info.screen_name}', _media['expanded_url'], 'Image', _media['media_url_https'], '', a['full_text']] + frr + ['']) for _media in a['extended_entities']['media']]
                    elif not _result[1]:  # 已超出目标时间范围
                        start_label = False
                        break

            except Exception as e:
                continue
            if 'cursor-bottom' in i['entryId']:  # 更新下一页的请求编号(含转推模式&亮点模式)
                _user_info.cursor = i['content']['value']

        return _photo_lst

    print(f'已下载图片/视频:{_user_info.count}')
    if has_highlights:  # 2024-01-05 #适配[亮点]标签
        url_top = 'https://twitter.com/i/api/graphql/w9-i9VNm_92GYFaiyGT1NA/UserHighlightsTweets?variables={"userId":"' + \
            _user_info.rest_id + '","count":20,'
        url_bottom = '"includePromotedContent":true,"withVoice":true}&features={"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"c9s_tweet_anatomy_moderator_badge_enabled":true,"tweetypie_unmention_optimization_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":false,"tweet_awards_web_tipping_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"rweb_video_timestamps_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_media_download_video_enabled":false,"responsive_web_enhance_cards_enabled":false}'
    elif has_likes:
        url_top = 'https://twitter.com/i/api/graphql/-fbTO1rKPa3nO6-XIRgEFQ/Likes?variables={"userId":"' + \
            _user_info.rest_id + '","count":200,'
        url_bottom = '"includePromotedContent":false,"withClientEventToken":false,"withBirdwatchNotes":false,"withVoice":true,"withV2Timeline":true}&features={"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"c9s_tweet_anatomy_moderator_badge_enabled":true,"tweetypie_unmention_optimization_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":false,"tweet_awards_web_tipping_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"rweb_video_timestamps_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_media_download_video_enabled":false,"responsive_web_enhance_cards_enabled":false}'
    elif has_retweet:  # 包含转推调用[UserTweets]的API(调用一次上限返回20条)
        url_top = 'https://twitter.com/i/api/graphql/2GIWTr7XwadIixZDtyXd4A/UserTweets?variables={"userId":"' + \
            _user_info.rest_id + '","count":20,'
        url_bottom = '"includePromotedContent":false,"withQuickPromoteEligibilityTweetFields":true,"withVoice":true,"withV2Timeline":true}&features={"rweb_lists_timeline_redesign_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"tweetypie_unmention_optimization_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":false,"tweet_awards_web_tipping_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_media_download_video_enabled":false,"responsive_web_enhance_cards_enabled":false}&fieldToggles={"withAuxiliaryUserLabels":false,"withArticleRichContentState":false}'
    else:  # 不包含转推则调用[UserMedia]的API(返回条数貌似无上限/改count) ##2023-12-11#此模式API返回值变动
        url_top = 'https://twitter.com/i/api/graphql/Le6KlbilFmSu-5VltFND-Q/UserMedia?variables={"userId":"' + \
            _user_info.rest_id + '","count":500,'
        url_bottom = '"includePromotedContent":false,"withClientEventToken":false,"withBirdwatchNotes":false,"withVoice":true,"withV2Timeline":true}&features={"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"tweetypie_unmention_optimization_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":false,"tweet_awards_web_tipping_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_media_download_video_enabled":false,"responsive_web_enhance_cards_enabled":false}'

    if _user_info.cursor:
        url = url_top + '"cursor":"' + _user_info.cursor + '",' + url_bottom
    else:
        url = url_top + url_bottom  # 第一页,无cursor
    try:
        global request_count
        response = httpx.get(
            quote_url(url), headers=_headers, proxy=proxies).text
        request_count += 1
        try:
            raw_data = json.loads(response)
        except Exception:
            if 'Rate limit exceeded' in response:
                print('API次数已超限')
            else:
                print('获取数据失败')
            print(response)
            return
        if has_highlights:  # 亮点模式
            raw_data = raw_data['data']['user']['result']['timeline']['timeline']['instructions'][-1]['entries']
        elif has_retweet:  # 与likes共用
            raw_data = raw_data['data']['user']['result']['timeline_v2']['timeline']['instructions'][-1]['entries']
        else:  # usermedia模式
            raw_data = raw_data['data']['user']['result']['timeline_v2']['timeline']['instructions']
        # 含转推模式 所有推文已全部下载完成
        if (has_retweet or has_highlights) and 'cursor-top' in raw_data[0]['entryId']:
            return False

        if not has_retweet and not has_highlights:  # usermedia模式下的下一页请求编号
            for i in raw_data[-1]['entries']:
                if 'bottom' in i['entryId']:
                    _user_info.cursor = i['content']['value']
            # _user_info.cursor = raw_data[-1]['entries'][0]['content']['value']

        if start_label:  # 判断是否超出时间范围
            if not has_retweet and not has_highlights:
                global First_Page
                if First_Page:  # 第一页的返回值需特殊处理
                    raw_data = raw_data[-1]['entries'][0]['content']['items']
                    First_Page = False
                else:
                    # usermedia新模式，所有推文已全部下载完成
                    if 'moduleItems' not in raw_data[0]:
                        return False
                    else:
                        raw_data = raw_data[0]['moduleItems']
            photo_lst = get_url_from_content(raw_data)
        else:
            return False

        if not photo_lst:
            photo_lst.append(True)
    except Exception as e:
        print('获取推文信息错误')
        print(e)
        print(response)
        return False
    return photo_lst


def download_control(_user_info):
    async def _main():
        async def down_save(url, prefix, csv_info, order: int):
            # 推文时间戳 -> 年月: 媒体按类型分目录存放(图片/视频), md 链接用相对路径(年份/月份/类型/文件名)
            _ym = time.strftime('%Y-%m', time.localtime(csv_info[0] / 1000)) if type(
                csv_info[0]) != str else csv_info[0][:7]
            _subdir = '视频' if '.mp4' in url else '图片'
            _media_dir = os.path.join(_user_info.save_path, _ym[:4], _ym, _subdir)
            os.makedirs(_media_dir, exist_ok=True)
            if '.mp4' in url:
                _file_name = f'{_media_dir + os.sep}{prefix}_{_user_info.count + order}.mp4'
            else:
                try:
                    if orig_format:
                        url += f'?name=orig'
                        # 根据图片 url 获取原始格式
                        _file_name = f'{_media_dir + os.sep}{prefix}_{_user_info.count + order}.{csv_info[5][-3:]}'
                    else:  # 指定格式时，先使用 name=orig，404 则切回 name=4096x4096，以保证最大尺寸
                        _file_name = f'{_media_dir + os.sep}{prefix}_{_user_info.count + order}.{img_format}'
                        if img_format != 'png':
                            url += f'?format=jpg&name=4096x4096'
                        else:
                            url += f'?format=png&name=4096x4096'
                except Exception as e:
                    print(url)
                    return False

            # 文件名中去掉空格, 否则 md 链接需 %20 编码, 编辑器无法预览图片/视频
            _file_name = _file_name.replace(' ', '-')
            # 第6位存相对路径: 年份/月份/图片|视频/文件名 (md链接用/分隔, 不用 os.path.join 避免反斜杠, 不用负索引避免 poster 扩展后错位)
            csv_info[6] = f'{_ym[:4]}/{_ym}/{_subdir}/' + os.path.split(_file_name)[1]
            if md_output:  # 在下载完毕之前先输出到 Markdown，以尽可能保证高并发下载也能得到正确的推文顺序。
                md_file.media_tweet_input(csv_info, prefix)
            count = 0
            while True:
                try:
                    async with semaphore:
                        async with httpx.AsyncClient(proxy=proxies) as client:
                            global down_count
                            # 如果出现第五次或以上的下载失败,且确认不是网络问题,可以适当降低最大并发数量
                            # 流式下载(边收边写): 内存占用恒定, 不随媒体大小增长; 1MB 块 + 1MB 缓冲, 落盘为连续顺序写
                            async with client.stream("GET", quote_url(url), timeout=(3.05, 16)) as response:
                                if response.status_code == 404:
                                    raise Exception('404')
                                with open(_file_name, 'wb', buffering=1024*1024) as f:
                                    async for chunk in response.aiter_bytes(chunk_size=1024*1024):
                                        f.write(chunk)
                                down_count += 1
                                # 下载视频封面图到 视频/ 目录与视频文件同目录同名 (失败不影响视频)
                                _poster = csv_info[11] if len(csv_info) > 11 else ''
                                if _poster:
                                    try:
                                        os.makedirs(_media_dir, exist_ok=True)
                                        _cover_name = os.path.splitext(
                                            os.path.split(_file_name)[1])[0] + '.jpg'
                                        async with client.stream("GET", quote_url(_poster), timeout=(3.05, 16)) as _response:
                                            if _response.status_code == 200:
                                                with open(os.path.join(_media_dir, _cover_name), 'wb', buffering=1024*1024) as f:
                                                    async for chunk in _response.aiter_bytes(chunk_size=1024*1024):
                                                        f.write(chunk)
                                    except Exception:
                                        pass  # 封面缺失只影响 md 预览, 不阻塞视频下载

                    csv_file.data_input(csv_info)

                    if log_output:
                        print(f'{_file_name}=====>下载完成')

                    break
                except Exception as e:
                    if '.mp4' in url or orig_format or str(e) != "404":
                        count += 1
                        if count >= 50:
                            print(f'{_file_name}=====>第{count}次下载失败，已跳过该文件。')
                            print(url)
                            if os.path.exists(_file_name):  # 清理流式下载失败残留的半截文件
                                os.remove(_file_name)
                            break
                        print(f'{_file_name}=====>第{count}次下载失败,正在重试')
                        print(url)
                    else:
                        url = url.replace('name=orig', 'name=4096x4096')

        while True:
            if stop_flag:   # 收到停止信号, 结束下载
                print('已收到停止信号, 下载中止')
                break
            photo_lst = get_download_url(_user_info)
            if not photo_lst:
                break
            elif photo_lst[0] == True:
                continue
            # 最大并发数量，默认为8，对自己网络有自信的可以调高
            semaphore = asyncio.Semaphore(max_concurrent_requests)
            if down_log:
                await asyncio.gather(*[asyncio.create_task(down_save(url[0], url[1], url[2], order)) for order, url in enumerate(photo_lst) if cache_data.is_present(url[0])])
            else:
                await asyncio.gather(*[asyncio.create_task(down_save(url[0], url[1], url[2], order)) for order, url in enumerate(photo_lst)])
            _user_info.count += len(photo_lst)  # 更新计数

    asyncio.run(_main())


def main(_user_info: object):
    re_token = 'ct0=(.*?);'
    _headers['x-csrf-token'] = re.findall(re_token, _headers['cookie'])[0]
    _headers['referer'] = 'https://twitter.com/' + _user_info.screen_name
    if not get_other_info(_user_info):
        return False
    print_info(_user_info)
    _path = settings['save_path'] + _user_info.screen_name
    if not os.path.exists(_path):  # 创建文件夹
        os.makedirs(settings['save_path']+_user_info.screen_name)  # 用户名建文件夹
        _user_info.save_path = settings['save_path']+_user_info.screen_name
    else:
        _user_info.save_path = _path

    global csv_file
    csv_file = csv_gen(_user_info.save_path, _user_info.name,
                       _user_info.screen_name, settings['time_range'], append_mode)

    if md_output:
        global md_file
        md_file = md_gen(_user_info.save_path, _user_info.name, _user_info.screen_name,
                         settings['time_range'], has_likes, media_count_limit, append_mode, md_mode, md_tweet_limit)

    if down_log:
        global cache_data
        cache_data = cache_gen(_user_info.save_path)

    if autoSync:
        files = []
        # 媒体文件已按年份存放到年份子目录, 需递归查找
        for _root, _dirs, _names in os.walk(_user_info.save_path):
            for _n in _names:
                if '-img_' in _n or '-vid_' in _n:
                    files.append(os.path.join(_root, _n))
        if len(files) > 0:
            global start_time_stamp
            re_rule = r'\d{4}-\d{2}-\d{2}'
            files.sort()  # 按文件名(含日期)排序, 最后一个为最新媒体
            start_time_stamp = time2stamp(re.findall(re_rule, files[-1])[0])
        else:
            start_time_stamp = backup_stamp

    download_control(_user_info)

    csv_file.csv_close()

    if md_output:
        md_file.md_close()

    if down_log:
        del cache_data
    print(f'{_user_info.name}下载完成\n\n')


stop_flag = False


def stop_handler(signum, frame):
    global stop_flag
    stop_flag = True
    print(f'收到停止信号({signal.Signals(signum).name}), 正在退出...')


signal.signal(signal.SIGTERM, stop_handler)     # docker stop / 容器停止时发送 SIGTERM
signal.signal(signal.SIGINT, stop_handler)      # Ctrl+C


def run_once():
    global start_label, First_Page
    _start = time.time()
    for i in settings['user_lst'].split(','):
        try:
            main(User_info(i))
        except Exception as e:
            print(f'用户 {i} 处理出错: {e}')
        start_label = True
        First_Page = True
    print(
        f'共耗时:{time.time()-_start}秒\n共调用{request_count}次API\n共下载{down_count}份图片/视频')


if __name__ == '__main__':
    if not schedule_time:   # 未配置定时: 运行一次后退出 (原逻辑)
        run_once()
    else:   # 已配置定时: 启动后立即运行一轮, 之后每天在指定时刻自动运行
        _round = 0
        while True:
            try:
                _round += 1
                request_count = 0
                down_count = 0
                print(
                    f'===== 第{_round}轮运行开始 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} =====')
                run_once()
                if stop_flag:
                    break
                now = datetime.now()
                _target_dt = datetime.combine(
                    now.date(), datetime.strptime(schedule_time, '%H:%M').time())
                if _target_dt <= now:   # 今日时刻已过, 顺延到明天
                    _target_dt += timedelta(days=1)
                print(
                    f'本轮完成, 下次运行时间: {_target_dt.strftime("%Y-%m-%d %H:%M:%S")} (停止容器或 Ctrl+C 退出)')
                while not stop_flag:    # 分段等待(1秒粒度), 及时响应停止信号
                    _remain = (_target_dt - datetime.now()).total_seconds()
                    if _remain <= 0:
                        break
                    time.sleep(min(1, _remain))
                if stop_flag:
                    print('收到停止信号, 程序退出')
                    break
            except KeyboardInterrupt:   # 兜底: 注册信号处理器后正常情况不会走到这里
                print('用户中断, 程序退出')
                break
