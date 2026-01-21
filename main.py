import os
import json
import re
import aiohttp
from difflib import SequenceMatcher
from astrbot.api.all import *


@register("netease_lyrics_join", "YourName", "网易云歌词接龙插件", "1.0.0")
class LyricsJoinPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # 默认配置：API地址 (Binaryify 项目地址)
        if "api_url" not in self.config:
            self.config["api_url"] = "http://localhost:3000"  # 请确保这是你部署的API地址
            self.context.save_config(self.config)

        # 缓存文件路径
        self.cache_file = os.path.join(os.path.dirname(__file__), "lyric_cache.json")
        self.cache = self._load_cache()

    # --- 辅助方法: 缓存管理 ---
    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_cache(self):
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)

    # --- 辅助方法: API 交互 ---
    async def _search_song_id(self, keyword: str):
        """搜索歌曲返回 ID"""
        url = f"{self.config['api_url']}/cloudsearch"
        params = {"keywords": keyword, "limit": 1}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    songs = data.get("result", {}).get("songs", [])
                    if songs:
                        return songs[0]["id"]
        return None

    async def _get_lyrics_by_id(self, song_id: int):
        """获取歌词文本"""
        url = f"{self.config['api_url']}/lyric"
        params = {"id": song_id}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    lrc = data.get("lrc", {}).get("lyric", "")
                    return self._parse_lrc(lrc)
        return []

    def _parse_lrc(self, lrc_text: str):
        """解析 LRC 格式，返回纯文本列表"""
        lines = []
        # 去除时间轴 [00:00.00]
        regex = re.compile(r'\[.*\]')
        for line in lrc_text.split('\n'):
            clean_line = regex.sub('', line).strip()
            if clean_line:
                lines.append(clean_line)
        return lines

    # --- 核心逻辑: 接歌词 ---
    def _find_next_line(self, user_text: str, lyrics_list: list):
        """查找匹配的下一句"""
        for i, line in enumerate(lyrics_list):
            # 使用 difflib 计算相似度，防止标点符号或轻微错别字导致匹配失败
            similarity = SequenceMatcher(None, user_text, line).ratio()

            # 相似度大于 0.8 且不是最后一句
            if similarity > 0.8 and i + 1 < len(lyrics_list):
                return lyrics_list[i + 1]
        return None

    # --- 事件监听 ---
    @event_handler(AstrMessageEvent)
    async def on_message(self, event: AstrMessageEvent):
        message_obj = event.message_obj

        # 1. 基础过滤：只处理文本消息，且长度适中（太短容易误触发）
        if not message_obj.text or len(message_obj.text) < 4:
            return

        user_text = message_obj.text.strip()

        # 2. 检查缓存
        # 缓存结构建议： { "keywords": ["下一句歌词", "再下一句..."] }
        # 简单起见，这里先尝试用缓存的歌词列表去匹配
        cached_result = None
        for song_key, lyrics in self.cache.items():
            cached_result = self._find_next_line(user_text, lyrics)
            if cached_result:
                break

        if cached_result:
            # 命中缓存，直接回复
            yield event.plain_result(f"接：{cached_result}")
            return

        # 3. 缓存未命中，调用 API 搜索
        # 注意：为了防止所有对话都触发搜索，建议这里可以加一个概率或者特定的前缀
        # 如果你想做“无感接龙”，API 请求可能会比较频繁

        song_id = await self._search_song_id(user_text)
        if not song_id:
            return  # 没搜到歌

        lyrics_list = await self._get_lyrics_by_id(song_id)
        if not lyrics_list:
            return  # 没歌词

        # 4. 存入缓存 (以搜到的第一首歌名为key，或者ID为key)
        self.cache[str(song_id)] = lyrics_list
        self._save_cache()

        # 5. 再次尝试匹配
        next_line = self._find_next_line(user_text, lyrics_list)

        if next_line:
            yield event.plain_result(f"{next_line}")