"""
AstrBot 接歌词插件
检测消息中的歌词，自动搜索歌曲并接龙
使用网易云音乐第三方 API (BinaryFly 项目)
"""
import asyncio
import aiohttp
import hashlib
import json
import random
import re
from typing import Optional, Dict
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


@register(
    "lyrics_catcher",
    "Azured",
    "基于网易云API的接歌词插件",
    "1.0.1"
)
class LyricsCatcher(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 获取配置
        self.api_base_url = self.config.get("api_base_url", "https://music.api.example.com")
        self.min_match_length = self.config.get("min_match_length", 5)
        self.max_cache_size = self.config.get("max_cache_size", 1000)
        self.enable_cache = self.config.get("enable_cache", True)
        self.trigger_probability = self.config.get("trigger_probability", 100)
        
        # 初始化缓存 - 使用正确的路径获取方式
        data_path = get_astrbot_data_path()
        self.cache_dir = data_path / "plugin_data" / "lyrics_catcher"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "lyrics_cache.json"
        self.lyrics_cache: Dict[str, Dict] = self._load_cache()
        
        logger.info(f"接歌词插件初始化完成，缓存大小: {len(self.lyrics_cache)}")

    def _load_cache(self) -> Dict[str, Dict]:
        """从文件加载缓存"""
        if not self.enable_cache:
            return {}
        
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载缓存失败: {e}")
        return {}

    def _save_cache(self):
        """保存缓存到文件"""
        if not self.enable_cache:
            return
        
        try:
            # 限制缓存大小
            if len(self.lyrics_cache) > self.max_cache_size:
                # 保留最新的缓存项
                items = list(self.lyrics_cache.items())
                self.lyrics_cache = dict(items[-self.max_cache_size:])
            
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.lyrics_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存缓存失败: {e}")

    def _get_cache_key(self, lyrics_text: str) -> str:
        """生成缓存键"""
        return hashlib.md5(lyrics_text.encode('utf-8')).hexdigest()

    async def _search_lyrics(self, lyrics_text: str) -> Optional[Dict]:
        """
        通过歌词搜索歌曲
        返回格式: {
            'song_name': str,
            'artist': str,
            'lyrics': str,
            'next_line': str  # 下一句歌词
        }
        """
        # 检查缓存
        cache_key = self._get_cache_key(lyrics_text)
        if cache_key in self.lyrics_cache:
            logger.info(f"命中缓存: {lyrics_text[:20]}...")
            return self.lyrics_cache[cache_key]

        try:
            async with aiohttp.ClientSession() as session:
                # 搜索歌曲
                search_url = f"{self.api_base_url}/search"
                params = {
                    'keywords': lyrics_text,
                    'type': '1',  # 单曲
                    'limit': 5
                }
                
                async with session.get(search_url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        logger.error(f"搜索请求失败: {resp.status}")
                        return None
                    
                    data = await resp.json()
                    songs = data.get('result', {}).get('songs', [])
                    
                    if not songs:
                        logger.info(f"未找到相关歌曲: {lyrics_text[:20]}...")
                        return None

                # 获取第一首歌的歌词
                song_id = songs[0]['id']
                song_name = songs[0]['name']
                artist = songs[0]['artists'][0]['name'] if songs[0].get('artists') else '未知'
                
                lyrics_url = f"{self.api_base_url}/lyric"
                params = {'id': song_id}
                
                async with session.get(lyrics_url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        logger.error(f"获取歌词失败: {resp.status}")
                        return None
                    
                    lyric_data = await resp.json()
                    lrc = lyric_data.get('lrc', {}).get('lyric', '')
                    
                    if not lrc:
                        return None
                    
                    # 解析歌词，找到下一句
                    next_line = self._find_next_line(lrc, lyrics_text)
                    
                    result = {
                        'song_name': song_name,
                        'artist': artist,
                        'lyrics': lrc,
                        'next_line': next_line
                    }
                    
                    # 保存到缓存
                    if self.enable_cache:
                        self.lyrics_cache[cache_key] = result
                        self._save_cache()
                    
                    return result
                    
        except asyncio.TimeoutError:
            logger.error("API 请求超时")
        except Exception as e:
            logger.error(f"搜索歌词出错: {e}")
        
        return None

    def _find_next_line(self, lrc: str, query_text: str) -> Optional[str]:
        """
        从歌词中找到匹配行的下一句
        lrc 格式: [00:00.00]歌词内容
        """
        lines = lrc.strip().split('\n')
        cleaned_query = self._clean_text(query_text)
        
        for i, line in enumerate(lines):
            # 移除时间标签
            lyric_text = line.split(']')[-1].strip()
            cleaned_lyric = self._clean_text(lyric_text)
            
            # 检查是否匹配
            if cleaned_query in cleaned_lyric or cleaned_lyric in cleaned_query:
                # 找到下一句非空歌词
                for j in range(i + 1, len(lines)):
                    next_lyric = lines[j].split(']')[-1].strip()
                    if next_lyric and next_lyric != lyric_text:
                        return next_lyric
        
        return None

    def _clean_text(self, text: str) -> str:
        """清理文本，移除标点和空格"""
        # 移除标点符号和空格
        text = re.sub(r'[^\w\s]', '', text)
        text = text.replace(' ', '').lower()
        return text

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，检测歌词"""
        message_text = event.message_str.strip()
        
        # 过滤太短的消息
        if len(message_text) < self.min_match_length:
            return
        
        # 检查是否是指令（以 / 开头）
        if message_text.startswith('/'):
            return
        
        # 触发概率控制（1-100）
        if random.randint(1, 100) > self.trigger_probability:
            return
        
        logger.info(f"检测消息: {message_text[:30]}...")
        
        # 搜索歌词
        result = await self._search_lyrics(message_text)
        
        if result and result.get('next_line'):
            song_info = f"♪ {result['song_name']} - {result['artist']}"
            next_line = result['next_line']
            
            # 发送接龙消息
            reply = f"{next_line}\n\n{song_info}"
            yield event.plain_result(reply)
            
            logger.info(f"成功接歌: {song_info}")

    @filter.command("lyrics_stats")
    async def get_stats(self, event: AstrMessageEvent):
        """查看插件统计信息"""
        stats = f"""📊 接歌词插件统计
━━━━━━━━━━━━━━━
🗂️ 缓存歌曲数: {len(self.lyrics_cache)}
📦 最大缓存: {self.max_cache_size}
🎯 触发概率: {self.trigger_probability}%
✅ 缓存状态: {'开启' if self.enable_cache else '关闭'}
━━━━━━━━━━━━━━━"""
        yield event.plain_result(stats)

    @filter.command("lyrics_clear")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def clear_cache(self, event: AstrMessageEvent):
        """清空歌词缓存（仅管理员）"""
        self.lyrics_cache.clear()
        self._save_cache()
        yield event.plain_result("✅ 歌词缓存已清空")
