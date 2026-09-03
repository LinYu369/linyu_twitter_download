# 推特图片下载   &nbsp; (๑¯◡¯๑) 
推特 图片 & 视频 & 文本 下载，以用户名为参数，爬取该用户推文中的图片与视频(含gif)

支持排除转推内容 & 多用户爬取 & 时间范围限制 & 按Tag获取 & 纯文本获取 & 高级搜索 & 评论区下载

--- 
## Disclaimer / 免责声明

> **EN**
> 
> 1. This project is strictly for programming learning, academic research, and personal practice.
> 
> 2. The intellectual property of all media content (images, videos, etc.) downloaded using this tool belongs to the original authors and the respective platforms. Please respect relevant copyrights.
> 
> 3. Users must comply with applicable laws, the target platform's Terms of Service, and relevant copyright regulations. Do not use this tool for malicious scraping, copyright infringement, illegal distribution, or other unlawful activities.
> 
> 4. The developer assumes no responsibility for any violations, legal disputes, or direct/indirect losses caused by the improper use of this tool. Use at your own risk.
> 

<br>

> **ZH**
> 
> 1. 本项目仅供编程学习交流、学术研究及个人练习使用。
> 
> 2. 使用本工具下载的所有媒体内容（图片、视频等）的知识产权均归原作者及所属平台所有，请尊重相关版权。
> 
> 3. 请勿将本工具及所获取的数据用于恶意抓取、侵权传播或其他违法用途。
> 
> 4. 开发者不对任何因不当使用本工具而导致的违规行为、法律纠纷或直接/间接损失承担责任。请风险自担。
> 
---
**目前老马加了API的请求次数限制** 
``` 
当程序抛出：Rate limit exceeded 
即表示该账号当日的API调用次数已耗尽

if 选择包含转推:
  爬完一个用户需要调用的API次数约为:总推数(含转推) / 19
elif 不包含:
  会大大减少API调用次数

下载不计入次数 
```

---

目录结构
---

下载内容按 `用户/年份/` 组织 (multi 模式, 默认), 每月一个总 md + 两个按媒体类型划分的分文件, 媒体文件分目录存放:

```
用户/
└── {screen_name}/
    └── 年份/                    (如 2024/)
        ├── 2024-01.md           总 md: 该月全部推文条目
        ├── 2024-01-图片.md      仅图片推文条目 (格式与总 md 一致, 只含图片媒体行)
        ├── 2024-01-视频.md      仅视频推文条目 (含 📹📹 提示行与封面图链接)
        └── 2024-01/             该月媒体文件目录
            ├── 图片/            图片文件
            └── 视频/
                └── 视频封面/    视频封面 jpg (与视频文件同名)
```

- **跨月导航**: 每月 md 顶部/底部生成高亮跳转链接 `**→→→ [2024-02 图片](2024-02-图片.md) ←←←**`; 出现新月份时自动刷新所有文件的链接; 图片/视频分文件各有独立的导航链, 无该类媒体的月份不建文件、链上自动跳过
- **媒体链接**: 使用相对路径, 中文不编码, 如 `2024-01/图片/xxx.jpg`、`2024-01/视频/xxx.mp4`、封面 `2024-01/视频/视频封面/xxx.jpg`
- **视频条目**: 一条 📹📹 视频名 📹📹 提示行 + 封面嵌套链接, 点击封面用系统默认播放器打开本地视频
- **single 模式**: 全部内容写入用户根目录单个 {screen_name}.md, 媒体仍按 `年份/月份/图片|视频/` 存放

文件说明
---

| 文件 | 说明 |
|---|---|
| `main.py` | 主程序: 下载指定用户的 图片 & 视频 & 文本, 支持 转推/亮点/喜欢/多用户/时间范围/定时运行/增量同步 |
| `settings.json` | 主程序配置 (cookie、用户列表、下载开关、md 模式等), 每个配置项都有 `xxx_info` 注释 |
| `tag_down.py` | 按 Tag / 高级搜索下载, 内置文本模式, 万金油工具 |
| `reply_down.py` | 下载评论区 (指定用户或推文链接, 支持批量) |
| `text_down.py` | 指定用户纯文本推文获取 (不下载媒体) |
| `profile_down.py` | 获取用户主页信息 (头像 & banner & 简介) |
| `migrate_media_split.py` | 历史数据迁移脚本: 旧 `媒体/` 结构 -> 新 `图片/视频/` 结构 (幂等) |
| `md_gen.py` | 内部模块: Markdown 生成 (single/multi, 三系列分文件, 跨月导航刷新) |
| `csv_gen.py` | 内部模块: CSV 统计报表生成 |
| `cache_gen.py` | 内部模块: 已下载内容记录 (避免重复下载) |
| `url_utils.py` / `user_info.py` / `transaction_generate.py` | 内部模块: URL 转义 / 用户信息 / X-Client-Transaction-ID 生成 |

md 生成模式 (settings.json, 需 `md_output: true`)

| 配置 | 说明 |
|---|---|
| `md_mode` | `single`: 单个 md 文件; `multi`: 按月分文件 + 图片/视频分文件 (默认) |
| `append_mode` | 开启: 写入固定文件追加新内容 (新内容在最上方); 关闭: 每次运行生成新文件 |
| `schedule_time` | 定时运行 (HH:MM), 配合 `autoSync` + `append_mode` 实现全自动增量更新 |

旧数据迁移
---

媒体按类型拆分目录与 图片/视频分文件 md 功能上线前, 历史数据存放在 `媒体/` (图片视频混放) 与平级 `视频封面/` 目录。迁移脚本一键升级到新结构:

``` 
python migrate_media_split.py                    # 迁移 settings.json save_path 下全部用户
python migrate_media_split.py --user screen_name # 只处理指定用户
python migrate_media_split.py --save-path /path  # 指定保存根目录(不读 settings.json)
```

脚本执行内容:

1. 将 `媒体/` 下文件按扩展名移入 `图片/` 或 `视频/`; 封面统一移入 `视频/视频封面/` 子目录
2. 重写总 md 中的媒体链接, 指向新目录
3. 从总 md 解析推文条目, 重建 月份-图片.md / 月份-视频.md 并补上跨月导航

脚本幂等, 重复运行无副作用; 已迁移过一半的用户 (封面仍混在 `视频/` 根目录) 再次运行会自动归位封面并修正 md 链接; 迁移完成后建议再跑一轮增量下载验证 (或设置 `autoSync` 自动同步)。

# Change Log 
* **2026-09-03** 
  * 媒体文件按类型分目录存储: `图片/` 与 `视频/`, 视频封面统一存于 `视频/视频封面/` 子目录 (与视频文件同名)
  * multi 模式每月额外生成 月份-图片.md (仅图片) 与 月份-视频.md (仅视频) 两个分文件, 格式与总 md 一致, 各带独立跨月导航
  * 新增 migrate_media_split.py 迁移脚本, 一键升级旧 `媒体/` + `视频封面/` 结构到新结构 (幂等, 兼容迁移中途状态)

* **2026-08-18-二改** 
  * 增加只写入到一个md里面的参数配置，关闭就会像原版一样分开多个md
  * 增加定时运行功能，填具体时间，每天都会在这个时间运行
  * 增加docker相关文件，可自行构建镜像运行
  * 打印输出的日志添加了时间戳，修改了保存本地的文件名，优化了下载过程中的内存占用过大问题。

* **2025-08-09** 
  * 支持获取用户主页内容(头像&banner&简介)--**请直接配置profile_down.py文件并运行**

* **2025-04-26** 
  * 替换部分失效接口 
  * `tag_down reply_down`增加`X-Client-Transaction-ID`校验, 请重新运行`pip install -r requirements.txt`安装依赖 
  * // 目前生成的`transaction-id`仍有小概率失效, 当程序抛出`获取数据失败`时可以尝试重新运行 
  * 目前`main text_down`似乎未受`X-Client-Transaction-ID`校验影响 
  * Reference: `https://github.com/iSarabjitDhiman/XClientTransaction`

* **2025-03-03** 
  * 支持下载评论区(指定用户或推文链接)--**请直接配置reply_down.py文件并运行**

* **2024-05-24** 
  * 按Tag获取支持保存文本内容 

* **2024-05-11**
  * 支持获取纯文本推文--**请直接配置text_down.py文件并运行**
    
    // (下方有预览) 注意，此功能会大量消耗API次数(参考上方公式)，默认排除转推内容
* **2024-05-10**
  * 支持按Tag获取--**请直接配置tag_down.py文件并运行**
  
    // 保存格式 (下方有预览)：. / {#Tag} / {datetime} \_ {@username} \_ { md5( media_url )[:4] } . { png / mp4 }

* **2024-03-09**
  * 支持记录已下载内容,避免重复下载 (如有问题请发issue)
  * 支持自动同步最新内容
* **2024-01-16**
  * 适配 [ **喜欢(Likes)** ] 标签页 
* **2024-01-10**
  * 新增统计数据 [ **Favorite, Retweet, Reply** ]
* **2024-01-05**
  * 适配Twieer新标签页 [ **亮点(HighLights)** ]
* **2023-12-12**
  * 适配Twitter新API
* **2023-10-12**
  * 添加 生成爬取信息 功能
* **2023-10-06**
  * 添加 时间范围限制 功能
  * 统一文件保存格式
    * 文件夹：用户id (@后面的)
    * 文件：推文日期-[img/vid]_下载计数.文件后缀
      
* **2023-09-15**
  * 添加 视频下载 功能
 

---

<div align="center"> 

| ![e53923662b627a645fcd2b0b3feadb3b](https://github.com/caolvchong-top/twitter_download/assets/57820488/39da9658-f40f-40d6-8480-9dff850076da) |
|:--:| 
| **(๑´ڡ`๑)** | 

</div>

部署
--- 

**Linux** : 
``` 
git clone https://github.com/caolvchong-top/twitter_download.git 
cd twitter_download 
pip3 install -r requirements.txt

#Python版本须>=3.8  httpx==0.28.1
``` 
**运行** : 
``` 
配置settings.json文件
python3 main.py 
``` 
**Windows** 和上面的一样，配置完setting.json后运行main.py即可 

**docker自行部署** : 
``` 
# 命令运行
sudo docker build -t my-twitter-download .

sudo docker run -it --name=my-twitter-download -v "/vol1/1000/docker/twitter_download:/app" my-twitter-download

# 重新构建
docker compose up -d --build

# 部署
docker compose up -d

``` 


注意事项
---

**按Tag下载&高级搜索 --> tag_down.py** 

**下载评论区 --> reply_down.py** 

**指定用户纯文本推文获取 --> text_down.py** 

**指定用户媒体文件获取&转推&亮点&喜欢(只能本人账号)等 --> main.py + settings.json** 

**历史数据目录迁移 --> migrate_media_split.py** 

其余各种不能解决的需求建议试试tag_down的高级搜索, 或是提交Issue 


Tag_Down 功能扩展 (高级搜索) &nbsp;&nbsp; <sub>//万金油</sub> 
---
~~其实按功能应该叫`search_down`~~

对于部分主程序难以实现的需求可以尝试配置`tag_down.py`的`filter`来曲线解决: 

|部分例子|
|:--:|
|大批量下载 -> 分批下载|
|指定时间范围|
|各类关键词搜索/排除|
|指定/排除目标用户|
|指定大于互动量的推文|
|指定推文语言|
|......| 

``` 
// 配置

tag = '#ヨルクラ'
# 填入tag 带上#号 可留空
_filter = ""
# (可选项) 高级搜索
# 请在 https://x.com/search-advanced 中组装搜索条件，复制搜索栏的内容填入_filter
# 注意，_filter中所有出现的双引号都需要改为单引号或添加转义符 例如 "Monika" -> 'Monika'

# 当tag选项留空时，将尝试以_filter的内容作为文件夹名称
``` 
推特高级搜索：https://x.com/search-advanced 

实例参考：https://github.com/caolvchong-top/twitter_download/issues/63#issuecomment-2351039320 & https://github.com/caolvchong-top/twitter_download/issues/106


效果预览
---
![20230720134231](https://github.com/caolvchong-top/twitter_download/assets/57820488/ee6a1c13-2b0c-47e9-a260-1ac529bec678) 


**↑↑老版本的图，仅效果参考**


![20230720134253](https://github.com/caolvchong-top/twitter_download/assets/57820488/6e5ba42f-2dc4-4fa1-8cf6-152246378756)


**评论区下载 Reply_down.py** 

![asehniubnsiebfi](https://github.com/user-attachments/assets/43708c8f-528d-4000-bf45-409a53ee3bc7)

 
**按Tag获取 Tag_down.py** 

![image](https://github.com/caolvchong-top/twitter_download/assets/57820488/aa109e18-5ef1-4d77-902c-658ed1b3ff53)

**纯文本推文获取(仅文本) Text_down.py** 

![QQ截图20240511032859](https://github.com/caolvchong-top/twitter_download/assets/57820488/0998b6b1-c313-4b1d-a78e-525a666098b2)



**图片下载效果**

![test1](https://github.com/caolvchong-top/twitter_download/assets/57820488/736f7554-612b-4bec-8baf-4a5ab45c6e04)


**视频下载效果**

![test2](https://github.com/caolvchong-top/twitter_download/assets/57820488/6f732042-6f96-4e7a-bd16-e7d08a46a90e)



**生成CSV统计**

![屏幕截图 2023-10-12 223755](https://github.com/caolvchong-top/twitter_download/assets/57820488/b5dfc741-e10f-409a-b298-d56ea236bc5f)