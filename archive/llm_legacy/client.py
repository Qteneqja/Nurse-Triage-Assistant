"""
Unified LLM Client Wrapper
Handles both DeepSeek and Gemma with proper error handling
"""
import os
import logging
import httpx
from typing import Dict, List, Optional
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

# Never log secrets
class SecretFilter(logging.Filter):
    def filter(self, record):
        record.msg = str(record.msg).replace(os.getenv('DEEPSEEK_API_KEY', ''), '[REDACTED]')
        record.msg = str(record.msg).replace(os.getenv('GEMMA_API_KEY', ''), '[REDACTED]')
        return True

logger.addFilter(SecretFilter())


class LLMClient:
    """Unified LLM client for OpenAI-compatible endpoints"""
    
    def __init__(self):
        self.deepseek_base = os.getenv('DEEPSEEK_BASE_URL', '').rstrip('/')
        self.deepseek_key = os.getenv('DEEPSEEK_API_KEY', '')
        self.gemma_base = os.getenv('GEMMA_BASE_URL', '').rstrip('/')
        self.gemma_key = os.getenv('GEMMA_API_KEY', '')
        self.timeout = int(os.getenv('LLM_TIMEOUT_SECONDS', '10'))
        
        if not self.deepseek_key or not self.gemma_key:
            logger.warning("LLM API keys not configured - using fallback mode")
    
    @retry(stop=stop_after_attempt(2), wait=wait_fixed(1))
    async def call_deepseek(self, messages: List[Dict], max_tokens: int = 500) -> Dict:
        """Call DeepSeek for structured reasoning"""
        return await self._call_llm(
            base_url=self.deepseek_base,
            api_key=self.deepseek_key,
            model="deepseek-ai/DeepSeek-V3.2-Exp",
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3
        )
    
    @retry(stop=stop_after_attempt(2), wait=wait_fixed(1))
    async def call_gemma(self, messages: List[Dict], max_tokens: int = 300) -> Dict:
        """Call Gemma for communication/summaries"""
        return await self._call_llm(
            base_url=self.gemma_base,
            api_key=self.gemma_key,
            model="google/gemma-3-27b-it",
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.7
        )
    
    async def _call_llm(self, base_url: str, api_key: str, model: str, 
                        messages: List[Dict], max_tokens: int, temperature: float) -> Dict:
        """Internal method to call OpenAI-compatible endpoint"""
        
        if not base_url or not api_key:
            return self._fallback_response("LLM not configured")
        
        endpoint = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return {
                    "success": True,
                    "content": data["choices"][0]["message"]["content"],
                    "model": model
                }
        except httpx.TimeoutException:
            logger.error("LLM request timed out")
            return self._fallback_response("Request timeout")
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM HTTP error: {e.response.status_code}")
            return self._fallback_response(f"HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"LLM call failed: {type(e).__name__}")
            return self._fallback_response(str(type(e).__name__))
    
    def _fallback_response(self, reason: str) -> Dict:
        """Safe fallback when LLM unavailable"""
        return {
            "success": False,
            "content": "HUMAN_REVIEW",
            "error": reason,
            "fallback": True
        }


# Singleton instance
_client: Optional[LLMClient] = None

def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
