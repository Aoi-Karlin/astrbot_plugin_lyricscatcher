"""
Lyric Trigger Plugin for AstrBot
- Author: User
- Features: Automatically triggers LLM response when lyrics are detected, sending the next line to AI.
"""

import re
import aiohttp
import urllib.parse
from typing import Dict, Any, Optional, Tuple
from difflib import SequenceMatcher

from astrbot.api import star, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.message_components import Plain


class NeteaseLyricsAPI:
    """
    A wrapper for the NeteaseCloudMusicApi to fetch lyrics.
    """

    def __init__(self, api_url: str, session: aiohttp.ClientSession):
        self.base_url = api_url.rstrip("/")
        self.session = session

    async def search_songs(self, keyword: str, limit: int = 10) -> list:
        """Search for songs by keyword."""
        url = f"{self.base_url}/search?keywords={urllib.parse.quote(keyword)}&limit={limit}&type=1"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    logger.warning(f"Netease API search failed with status {r.status}")
                    return []
                data = await r.json()
                return data.get("result", {}).get("songs", [])
        except Exception as e:
            logger.error(f"Netease API search error: {e}")
            return []

    async def get_lyrics(self, song_id: int) -> Optional[Dict[str, Any]]:
        """Get lyrics for a song."""
        url = f"{self.base_url}/lyric?id={song_id}"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    logger.warning(f"Netease API lyrics failed with status {r.status}")
                    return None
                data = await r.json()
                
                # Check if lyrics exist
                if data.get("lrc") and data["lrc"].get("lyric"):
                    return data
                return None
        except Exception as e:
            logger.error(f"Netease API lyrics error: {e}")
            return None

    def parse_lyrics(self, lyric_text: str) -> list:
        """Parse lyrics text into lines, removing timestamps."""
        if not lyric_text:
            return []
        
        lines = []
        for line in lyric_text.split('\n'):
            # Remove timestamp like [00:00.00] or [00:00:00]
            cleaned = re.sub(r'\[\d{2}:\d{2}(:\d{2})?\.?\d*\]', '', line).strip()
            if cleaned:
                lines.append(cleaned)
        return lines


class Main(star.Star):
    """
    Lyric Trigger Plugin Main Class
    """

    def __init__(self, context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.config = config or {}
        
        # Default configuration
        self.config.setdefault("api_url", "http://127.0.0.1:3000")
        self.config.setdefault("similarity_threshold", 0.6)
        self.config.setdefault("max_search_results", 5)
        self.config.setdefault("trigger_prompt", "用户输入了歌词：'{lyric}'，下一句是：'{next_line}'。请根据这两句歌词进行回应，可以：\n1. 继续接唱\n2. 评论这两句歌词\n3. 表达相关的情感或联想\n请用自然、富有情感的方式回复，不要超过两句话。")
        
        # Show warning if using default API URL
        if self.config["api_url"] == "http://127.0.0.1:3000":
            logger.warning("Lyric Trigger plugin: 使用默认API URL (127.0.0.1:3000)，请在配置中修改如果您的API服务在其他地址")
        
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.api: Optional[NeteaseLyricsAPI] = None

    async def initialize(self):
        """Initialize the plugin."""
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.api = NeteaseLyricsAPI(self.config["api_url"], self.http_session)
        logger.info("Lyric Trigger plugin: 初始化成功")

    async def terminate(self):
        """Clean up resources when the plugin is unloaded."""
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            logger.info("Lyric Trigger plugin: HTTP session 已关闭")
        await super().terminate()

    def calculate_similarity(self, str1: str, str2: str) -> float:
        """Calculate similarity between two strings using SequenceMatcher."""
        if not str1 or not str2:
            return 0.0
        
        # Convert to lowercase and remove spaces for better matching
        str1_clean = str1.lower().replace(" ", "")
        str2_clean = str2.lower().replace(" ", "")
        
        if not str1_clean or not str2_clean:
            return 0.0
            
        return SequenceMatcher(None, str1_clean, str2_clean).ratio()

    async def find_matching_lyric(self, user_text: str) -> Optional[Tuple[str, str, str, int]]:
        """
        Find matching lyrics in Netease Music.
        Returns: (song_name, matched_line, next_line, song_id) or None
        """
        # Search for songs using the user text as keyword
        songs = await self.api.search_songs(user_text, self.config["max_search_results"])
        
        if not songs:
            return None
        
        # Check lyrics for each song
        for song in songs:
            song_id = song.get("id")
            song_name = song.get("name", "未知歌曲")
            
            if not song_id:
                continue
            
            # Get lyrics for this song
            lyrics_data = await self.api.get_lyrics(song_id)
            if not lyrics_data:
                continue
            
            # Parse lyrics lines
            lyric_text = lyrics_data["lrc"]["lyric"]
            lines = self.api.parse_lyrics(lyric_text)
            
            if len(lines) < 2:
                continue
            
            # Find matching line
            for i, line in enumerate(lines[:-1]):  # Don't check the last line
                similarity = self.calculate_similarity(user_text, line)
                
                if similarity >= self.config["similarity_threshold"]:
                    next_line = lines[i + 1]
                    return song_name, line, next_line, song_id
        
        return None

    @filter.command("歌词匹配", alias={"lyric", "匹配歌词", "lyricmatch"}, priority=100)
    async def cmd_lyric_match(self, event: AstrMessageEvent, *args):
        """指令触发歌词匹配和AI回复。使用方法：/歌词匹配 <歌词内容>"""
        event.stop_event()
        
        # Get lyric text from args
        lyric_text = " ".join(args) if args else ""
        
        # Check if lyric text is provided
        if not lyric_text.strip():
            await event.send(MessageChain([Plain("请提供要匹配的歌词内容。\n使用方法：/歌词匹配 <歌词内容>\n例如：/歌词匹配 天青色等烟雨")]))
            return
        
        try:
            # Try to find matching lyrics
            result = await self.find_matching_lyric(lyric_text.strip())
            
            if result:
                song_name, matched_line, next_line, song_id = result
                
                logger.info(f"Lyric Trigger plugin: 匹配到歌词 '{matched_line}' 来自歌曲 '{song_name}'")
                
                # Prepare the prompt for LLM
                prompt_template = self.config.get("trigger_prompt", "")
                prompt = prompt_template.format(
                    lyric=matched_line,
                    next_line=next_line,
                    song_name=song_name
                )
                
                # Silently trigger LLM using current personality
                # Use reply() to leverage the default personality of current conversation
                await event.reply(MessageChain([Plain(prompt)]))
                
                # Log the trigger
                logger.info(f"Lyric Trigger plugin: 已静默触发LLM回复（使用当前人格），歌曲: {song_name}, 歌词: {matched_line} -> {next_line}")
            else:
                # No match found
                error_msg = f"❌ 未找到匹配的歌词。\n\n可能的原因：\n• 相似度低于阈值（当前：{self.config['similarity_threshold']})\n• 未在搜索结果中找到匹配歌曲\n• 歌词内容可能不够独特\n\n建议：尝试更长的歌词片段或调整配置参数。"
                await event.send(MessageChain([Plain(error_msg)]))
        except Exception as e:
            logger.error(f"Lyric Trigger plugin: 处理失败: {e}")
            await event.send(MessageChain([Plain(f"处理失败：{str(e)}")]))
        finally:
            # Clean up processing message if needed
            pass
