"""
AstrBot 网易云歌词接龙插件
检测消息中的歌词，自动搜索歌曲并接龙
"""
import os
import json
import re
import aiohttp
from difflib import SequenceMatcher
from typing import Dict, Any, Optional, List

from astrbot.api import star, logger
from astrbot.api.event import on_decor
from astrbot.api.model import MessageEvent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.message_components import Plain


# --- API 封装类 ---
class NeteaseLyricsAPI:
    """网易云音乐API封装类"""
    def __init__(self, api_url: str, session: aiohttp.ClientSession):
        self.base_url = api_url.rstrip("/")
        self.session = session

    async def search_and_get_lyrics(self, keyword: str) -> List[str]:
        """搜索并获取歌词"""
        search_url = f"{self.base_url}/cloudsearch"
        params = {"keywords": keyword, "limit": "1"}
        try:
            async with self.session.get(search_url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    songs = data.get("result", {}).get("songs", [])
                    if songs:
                        return await self._fetch_lyric(songs[0]["id"])
        except Exception as e:
            logger.error(f"[歌词插件] API搜索错误: {e}")
        return []

    async def _fetch_lyric(self, song_id: int) -> List[str]:
        """获取歌曲歌词"""
        url = f"{self.base_url}/lyric?id={song_id}"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    lrc = data.get("lrc", {}).get("lyric", "")
                    return self._parse_lrc(lrc)
        except Exception as e:
            logger.error(f"[歌词插件] 获取歌词错误: {e}")
        return []

    def _parse_lrc(self, lrc_text: str) -> List[str]:
        """解析LRC歌词格式"""
        lines = []
        regex = re.compile(r'\[.*?\]')
        for line in lrc_text.split('\n'):
            clean = regex.sub('', line).strip()
            if clean and not clean.startswith(("作词", "作曲", "编曲", "制作")):
                lines.append(clean)
        return lines


# --- 插件主类 ---
@star.register("netease_lyrics_join", "YourName", "网易云歌词接龙", "1.1.1")
class LyricsJoinPlugin(star.Star):
    """网易云歌词接龙插件主类"""
    
    def __init__(self, context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.config = config or {}
        
        # 配置项设置
        self.api_url = self.config.get("api_url", "http://localhost:3000")
        self.similarity_threshold = self.config.get("similarity_threshold", 0.8)
        self.search_min_length = self.config.get("search_min_length", 5)
        self.enable_cache = self.config.get("enable_cache", True)
        
        # 初始化缓存和会话
        self.cache_file = os.path.join(os.path.dirname(__file__), "lyric_cache.json")
        self.lyric_cache = self._load_cache() if self.enable_cache else {}
        self.http_session = None
        self.api = None
        
        logger.info(f"[歌词插件] 插件初始化完成，API地址: {self.api_url}")

    async def initialize(self):
        """插件初始化"""
        try:
            self.http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
            self.api = NeteaseLyricsAPI(self.api_url, self.http_session)
            logger.info("[歌词插件] 插件初始化成功")
        except Exception as e:
            logger.error(f"[歌词插件] 插件初始化失败: {e}")

    async def terminate(self):
        """插件终止"""
        try:
            if self.enable_cache:
                self._save_cache()
            if self.http_session:
                await self.http_session.close()
            logger.info("[歌词插件] 插件已正常关闭")
        except Exception as e:
            logger.error(f"[歌词插件] 插件关闭出错: {e}")

    def _load_cache(self) -> Dict[str, List[str]]:
        """加载歌词缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"[歌词插件] 加载缓存失败: {e}")
        return {}

    def _save_cache(self):
        """保存歌词缓存"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.lyric_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[歌词插件] 保存缓存失败: {e}")

    def _match_lyrics(self, text: str, lyrics: List[str]) -> Optional[str]:
        """匹配歌词并返回下一句"""
        threshold = self.similarity_threshold
        
        for i, line in enumerate(lyrics):
            # 精确匹配或相似度匹配
            if text in line or SequenceMatcher(None, text, line).ratio() >= threshold:
                if i + 1 < len(lyrics):
                    return lyrics[i + 1]
        return None

    @on_decor.message_created
    async def handle_lyrics(self, event: MessageEvent):
        """处理消息事件，检测歌词并接龙"""
        # 获取消息文本
        user_text = event.message_str.strip()
        
        # 基础过滤
        if len(user_text) < self.search_min_length:
            return
            
        # 过滤命令消息
        if user_text.startswith(('/', '!', '.', '。', '#')):
            return
        
        logger.debug(f"[歌词插件] 检测消息: {user_text[:30]}...")
        
        try:
            # 1. 先检查缓存
            for cache_key, lyrics in self.lyric_cache.items():
                next_line = self._match_lyrics(user_text, lyrics)
                if next_line:
                    await event.send(MessageChain([Plain(next_line)]))
                    logger.info(f"[歌词插件] 缓存命中，发送接龙: {next_line[:20]}...")
                    return
            
            # 2. API搜索
            lyrics_list = await self.api.search_and_get_lyrics(user_text)
            if lyrics_list:
                # 存入缓存
                cache_key = f"song_{len(self.lyric_cache)}"
                self.lyric_cache[cache_key] = lyrics_list
                
                # 尝试匹配
                next_line = self._match_lyrics(user_text, lyrics_list)
                if next_line:
                    await event.send(MessageChain([Plain(next_line)]))
                    logger.info(f"[歌词插件] API搜索成功，发送接龙: {next_line[:20]}...")
                    
        except Exception as e:
            logger.error(f"[歌词插件] 处理消息出错: {e}")

    @star.command("lyrics_stats")
    async def get_stats(self, event: MessageEvent):
        """获取插件统计信息"""
        stats_text = f"""📊 歌词接龙插件统计
━━━━━━━━━━━━━━━
🗂️ 缓存歌曲数: {len(self.lyric_cache)}
🎯 最小长度: {self.search_min_length}
📊 相似度阈值: {self.similarity_threshold}
💾 缓存状态: {'开启' if self.enable_cache else '关闭'}
🔗 API地址: {self.api_url}
━━━━━━━━━━━━━━━"""
        await event.send(MessageChain([Plain(stats_text)]))

    @star.command("lyrics_clear")
    @star.permission_admin
    async def clear_cache(self, event: MessageEvent):
        """清空歌词缓存（仅管理员）"""
        try:
            self.lyric_cache.clear()
            if self.enable_cache and os.path.exists(self.cache_file):
                os.remove(self.cache_file)
            await event.send(MessageChain([Plain("✅ 歌词缓存已清空")]))
            logger.info("[歌词插件] 缓存已清空")
        except Exception as e:
            logger.error(f"[歌词插件] 清空缓存失败: {e}")
            await event.send(MessageChain([Plain("❌ 清空缓存失败")]))